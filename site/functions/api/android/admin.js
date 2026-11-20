// ===== functions/api/android/admin.js - the seller's back office =====
//
// POST only, and only with ADMIN_SECRET. No secret ever goes in a query string:
// URLs end up in logs, referrers and screenshots.
//
//   { secret, action: "ping" }
//   { secret, action: "list", status?: "pending", limit?: 50, cursor? }
//   { secret, action: "paid", orderId, device?, note? }
//   { secret, action: "cancel", orderId, note? }
//   { secret, action: "mint", days, tier?, device? }
//   { secret, action: "verify", key, device? }
//
// "paid" mints the key. It is idempotent: approving twice returns the first key
// instead of selling two licences.

import { json, fail, body, allow, safeEqual } from '../../_lib/http.js';
import {
  listOrders, markPaid, cancelOrder, kvFor, secretFor, isOrderId, cleanDevice,
} from '../../_lib/orders.js';
import { mint, verify } from '../../_lib/key.js';
import { payMode, qrisConfig } from '../../_lib/pay.js';
import { PLANS } from '../../_lib/plans.js';

function authorised(env, data, request) {
  const want = env.ADMIN_SECRET;
  if (!want) return { ok: false, reason: 'admin is disabled: set ADMIN_SECRET', status: 503 };
  const given = request.headers.get('x-admin-secret') || (data && data.secret) || '';
  if (!safeEqual(given, want)) return { ok: false, reason: 'wrong secret', status: 401 };
  return { ok: true };
}

function ping(env) {
  const cfg = qrisConfig(env);
  let kv = 'bound';
  try { kvFor(env); } catch (e) { kv = e.message; }
  let secret = 'set';
  try { secretFor(env); } catch (e) { secret = e.message; }

  return json({
    ok: true,
    platform: 'android',
    payMode: payMode(env),
    qris: cfg.ok ? 'valid' : cfg.reason,
    kv,
    licenseSecret: secret,
    adminSecret: env.ADMIN_SECRET ? 'set' : 'missing',
    webhookSecret: env.WEBHOOK_SECRET ? 'set' : 'missing (webhooks disabled)',
    plans: PLANS.map((p) => ({ id: p.id, price: p.price, days: p.days, tier: p.tier })),
    now: new Date().toISOString(),
  });
}

async function handle(context) {
  const { env, request } = context;
  const data = await body(request);
  if (!data) return fail('body must be JSON');

  const auth = authorised(env, data, request);
  if (!auth.ok) return fail(auth.reason, auth.status);

  const action = String(data.action || '').toLowerCase();

  try {
    if (action === 'ping') return ping(env);

    if (action === 'list') {
      const status = data.status ? String(data.status).toLowerCase() : null;
      const rows = await listOrders(env, {
        limit: Number(data.limit) || 100,
        cursor: data.cursor || null,
        status,
      });
      const counts = rows.orders.reduce((acc, o) => {
        acc[o.status] = (acc[o.status] || 0) + 1;
        return acc;
      }, {});
      return json({ ...rows, counts });
    }

    if (action === 'paid') {
      if (!isOrderId(data.orderId)) return fail('bad orderId');
      const rec = await markPaid(env, data.orderId, {
        device: data.device,
        note: data.note || 'approved by admin',
      });
      return json({
        ok: true,
        orderId: rec.id,
        plan: rec.plan,
        label: rec.label,
        amount: rec.amount,
        device: rec.device || '',
        key: rec.key,
        paidAt: rec.paidAt,
      });
    }

    if (action === 'cancel') {
      if (!isOrderId(data.orderId)) return fail('bad orderId');
      const rec = await cancelOrder(env, data.orderId, data.note || '');
      return json({ ok: true, orderId: rec.id, status: rec.status });
    }

    if (action === 'mint') {
      const days = Number(data.days == null ? 30 : data.days);
      const tier = Number(data.tier == null ? 1 : data.tier);
      if (!Number.isFinite(days) || days < 0) return fail('days must be a number >= 0');
      if (!Number.isFinite(tier) || tier < 0 || tier > 15) return fail('tier must be 0..15');
      const device = cleanDevice(data.device);
      const key = await mint({
        secret: secretFor(env), days, tier, deviceId: device, now: Date.now(),
      });
      return json({ ok: true, key, days, tier, device, note: data.note || '' });
    }

    if (action === 'verify') {
      const r = await verify(String(data.key || ''), {
        secret: secretFor(env),
        deviceId: cleanDevice(data.device),
        now: Date.now(),
      });
      return json(r);
    }

    return fail('unknown action: ' + (action || '(none)'));
  } catch (e) {
    return fail(e && e.message ? e.message : String(e), 400);
  }
}

export function onRequest(context) {
  const method = context.request.method;
  if (method === 'POST') return handle(context);
  if (method === 'GET') {
    // Someone opened the URL in a browser. Say what it is without leaking
    // whether a secret is set.
    return json({
      ok: true,
      endpoint: 'android admin',
      method: 'POST',
      actions: ['ping', 'list', 'paid', 'cancel', 'mint', 'verify'],
      hint: 'send { "secret": "...", "action": "list" }',
    });
  }
  return allow(['POST'], method);
}
