// ===== functions/api/android/order.js - create an order, then poll it =====
//
//   POST { plan: "a7d", device?: "<android id>" }
//     -> { ok, orderId, token, amount, status, qr?, payUrl?, note }
//
//   GET ?id=AND-XXXXXX&token=...
//     -> { ok, status, key? }
//
// The token is returned once, at creation, and never again. Everything after
// that needs it: the order id is short enough to read over the phone, so it
// cannot also be the thing that unlocks someone else's key.

import { json, fail, body, allow, clientIp } from '../../_lib/http.js';
import { createOrder, readOrder, shape, kvFor, cleanDevice, ORDER_TTL_MS } from '../../_lib/orders.js';
import { planById } from '../../_lib/plans.js';
import { present } from '../../_lib/pay.js';

const RL_WINDOW_MS = 60 * 60 * 1000;

/**
 * A soft cap on orders per IP per hour. It is not security - it is so a
 * mistyped retry loop in someone's client cannot fill the namespace with ten
 * thousand dead orders. Fails open: if KV hiccups, the order is still created.
 */
async function rateOk(env, ip) {
  const limit = Number(env.ORDER_RATE_PER_HOUR || 30);
  if (!ip || !Number.isFinite(limit) || limit <= 0) return true;
  try {
    const kv = kvFor(env);
    const bucket = Math.floor(Date.now() / RL_WINDOW_MS);
    const key = 'a:rl:' + ip + ':' + bucket;
    const seen = Number(await kv.get(key)) || 0;
    if (seen >= limit) return false;
    await kv.put(key, String(seen + 1), { expirationTtl: 7200 });
    return true;
  } catch {
    return true;
  }
}

async function create(context) {
  const { env, request } = context;
  const data = await body(request);
  if (!data) return fail('body must be JSON: { "plan": "a7d" }');

  const planId = String(data.plan || '');
  if (!planById(planId)) {
    return fail('unknown plan: ' + (planId || '(none)'), 400, { plans: true });
  }

  let device = '';
  try {
    device = cleanDevice(data.device);
  } catch (e) {
    return fail(e.message);
  }

  if (!(await rateOk(env, clientIp(request)))) {
    return fail('too many orders from this address; try again later', 429);
  }

  let order;
  try {
    order = await createOrder(env, { planId, device, source: 'web' });
  } catch (e) {
    return fail(e.message, 503);
  }

  let pay;
  try {
    pay = await present(env, order);
  } catch (e) {
    // The order exists but cannot be paid for. Say so instead of showing a
    // checkout page with no QR: the buyer would otherwise sit and wait.
    return json({
      ok: true,
      orderId: order.id,
      token: order.token,
      plan: order.plan,
      label: order.label,
      amount: order.amount,
      status: order.status,
      expiresAt: order.expiresAt,
      configured: false,
      note: 'pembayaran belum siap: ' + e.message,
    }, 200);
  }

  return json({
    ok: true,
    orderId: order.id,
    token: order.token,
    plan: order.plan,
    label: order.label,
    days: order.days,
    price: order.price,
    unique: order.amount - order.price,
    amount: order.amount,
    status: order.status,
    createdAt: order.createdAt,
    expiresAt: order.expiresAt,
    ttlMinutes: Math.round(ORDER_TTL_MS / 60000),
    configured: pay.configured !== false,
    qr: pay.qr,
    payUrl: pay.payUrl,
    note: pay.note,
  });
}

async function status(context) {
  const { env, request } = context;
  const url = new URL(request.url);
  const id = url.searchParams.get('id') || '';
  const token = url.searchParams.get('token') || '';

  let rec;
  try {
    rec = await readOrder(env, id, token);
  } catch (e) {
    return fail(e.message, 503);
  }
  if (!rec) return fail('no such order', 404);

  const view = shape(rec, Date.now());
  if (view.status === 'paid' && rec.key) {
    // The key is in the record. It is also the only thing that matters here, so
    // send it and nothing else about the order's internals.
    return json(view);
  }

  // Still payable: re-send the payment block so a reloaded page can show the
  // same QR instead of an empty checkout with no way to pay.
  if (view.status === 'pending') {
    try {
      const pay = await present(env, rec);
      return json({ ...view, qr: pay.qr, payUrl: pay.payUrl, note: pay.note, configured: pay.configured !== false });
    } catch (e) {
      return json({ ...view, configured: false, note: e.message });
    }
  }

  return json(view);
}

export function onRequest(context) {
  const method = context.request.method;
  if (method === 'POST') return create(context);
  if (method === 'GET') return status(context);
  return allow(['GET', 'POST'], method);
}
