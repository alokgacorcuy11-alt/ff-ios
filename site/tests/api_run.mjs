// ===== tests/api_run.mjs - the whole order lifecycle, against a fake KV =====
//
// Runs the four Pages Functions exactly as the runtime would: build a Request,
// hand it to onRequest, read the Response. The only thing replaced is KV.
//
// Every assertion prints [ok  ] or [FAIL] so run_all.py can count them, and the
// process exits non-zero on the first failure count > 0.
import { onRequest as plansFn } from '../functions/api/android/plans.js';
import { onRequest as orderFn } from '../functions/api/android/order.js';
import { onRequest as adminFn } from '../functions/api/android/admin.js';
import { onRequest as webhookFn } from '../functions/api/android/webhook.js';
import { withAmount, valid, amountOf } from '../functions/_lib/qris.js';
import { payload as qrPayload } from '../functions/_lib/qr.js';
import { verify as verifyKey } from '../functions/_lib/key.js';

// ---- fake KV ---------------------------------------------------------------
class MemoryKV {
  constructor() { this.m = new Map(); this.puts = 0; }

  async get(key, type) {
    const v = this.m.get(key);
    if (v === undefined) return null;
    return type === 'json' ? JSON.parse(v) : v;
  }

  async put(key, value) {
    this.puts++;
    this.m.set(key, String(value));
  }

  async delete(key) { this.m.delete(key); }

  async list({ prefix = '', limit = 1000, cursor = null } = {}) {
    const all = [...this.m.keys()].filter((k) => k.startsWith(prefix)).sort();
    const start = cursor ? all.indexOf(cursor) + 1 : 0;
    const page = all.slice(start, start + limit);
    const done = start + limit >= all.length;
    return {
      keys: page.map((name) => ({ name })),
      list_complete: done,
      cursor: done ? undefined : page[page.length - 1],
    };
  }
}

const STATIC = '00020101021126650014ID.CO.QRIS.WWW0117936005030000089820215ID20200000000000303UMI51440014ID.CO.QRIS.WWW0215ID20200000000000303UMI5204541153033605802ID5912DEMIOS STORE6007JAKARTA6105123456209070503A0163048160';
const SECRET = 'unit-test-secret-that-is-long-enough';
const ADMIN = 'admin-secret-for-the-test';
const ORIGIN = 'https://demios.pages.dev';

let pass = 0;
let failed = 0;

function ok(msg) { pass++; console.log('[ok  ] ' + msg); }
function bad(msg) { failed++; console.log('[FAIL] ' + msg); }
function check(cond, msg, extra) {
  if (cond) ok(msg + (extra ? '  ' + extra : ''));
  else bad(msg + (extra ? '  ' + extra : ''));
}

function makeEnv(over = {}) {
  return {
    DEMIOS_KV: new MemoryKV(),
    LICENSE_SECRET: SECRET,
    ADMIN_SECRET: ADMIN,
    QRIS_STATIC: STATIC,
    PAY_MODE: 'manual',
    ...over,
  };
}

async function call(fn, env, method, path, payload, headers = {}) {
  const url = ORIGIN + path;
  const init = { method, headers: { ...headers } };
  if (payload !== undefined) {
    init.headers['content-type'] = 'application/json';
    init.body = JSON.stringify(payload);
  }
  const res = await fn({ env, request: new Request(url, init), params: {} });
  let json = null;
  try { json = await res.json(); } catch { json = null; }
  return { status: res.status, json, headers: res.headers };
}

const order = (env, payload, headers) => call(orderFn, env, 'POST', '/api/android/order', payload, headers);
const status = (env, id, token) => call(orderFn, env, 'GET',
  '/api/android/order?id=' + encodeURIComponent(id) + '&token=' + encodeURIComponent(token));
const admin = (env, payload, headers = {}) => call(adminFn, env, 'POST', '/api/android/admin', payload, headers);

// unpack the wire form back into the payload string is not possible (it is a
// QR matrix), so re-encode and compare matrices instead
function unpackMatrix(size, bits) {
  const raw = Buffer.from(bits, 'base64');
  const out = [];
  for (let i = 0; i < size * size; i++) out.push((raw[i >> 3] >> (7 - (i & 7))) & 1);
  return out;
}

async function main() {
  const env = makeEnv();

  // ---- 1. plans ----------------------------------------------------------
  {
    const r = await call(plansFn, env, 'GET', '/api/android/plans');
    check(r.status === 200 && r.json.ok, 'GET /plans answers 200');
    check(r.json.plans.length === 3, 'three plans', JSON.stringify(r.json.plans.map((p) => p.id)));
    check(r.json.gateway === 'manual', 'gateway is manual');
    check(r.json.ready === true, 'ready because QRIS_STATIC is valid');
    check(r.json.plans.every((p) => p.price > 0 && p.days > 0), 'every plan has a price and a duration');
    check(r.json.plans.map((p) => p.id).join(',') === 'a1d,a7d,a30d',
      'ids are the android ones, not the ios ones');
    check(r.json.plans.every((p) => !('tier' in p) || typeof p.tier === 'string'),
      'tier is a name, not the raw nibble');
  }

  // ---- 2. create --------------------------------------------------------
  let created;
  {
    const r = await order(env, { plan: 'a7d' });
    created = r.json;
    check(r.status === 200 && r.json.ok, 'POST /order creates an order');
    check(/^AND-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}$/.test(r.json.orderId),
      'order id is AND- plus six unambiguous characters', r.json.orderId);
    check(/^[0-9a-f]{24}$/.test(r.json.token), 'token is 24 hex characters');
    check(r.json.price === 140000, 'price comes from the plan table', String(r.json.price));
    check(r.json.amount - r.json.price >= 100 && r.json.amount - r.json.price <= 999,
      'the amount carries a three-digit unique code', 'Rp' + r.json.amount + ' = 140000+' + r.json.unique);
    check(r.json.status === 'pending', 'status starts pending');
    check(r.json.configured === true, 'payment is configured');
    check(r.json.qr && r.json.qr.size > 20 && r.json.qr.bits.length > 100,
      'a QR matrix came back', r.json.qr && (r.json.qr.size + 'x' + r.json.qr.size));
    check(r.json.expiresAt - r.json.createdAt === 30 * 60 * 1000,
      'the order lives 30 minutes');
  }

  // ---- 3. the QR carries this order's amount, uniquely -------------------
  {
    const dyn = withAmount(STATIC, created.amount, { decimals: 2 });
    check(valid(dyn) && amountOf(dyn) === created.amount,
      'a dynamic payload for this order holds its exact amount', String(amountOf(dyn)));
    const want = qrPayload(dyn);
    check(want.size === created.qr.size && want.bits === created.qr.bits,
      'the QR matrix the server sent is exactly that payload', want.size + 'x' + want.size);

    const m = unpackMatrix(created.qr.size, created.qr.bits);
    check(m.length === created.qr.size * created.qr.size, 'QR bits unpack to a full matrix');
    check(m.filter(Boolean).length > m.length * 0.3 && m.filter(Boolean).length < m.length * 0.7,
      'the matrix is roughly half dark, as a QR should be');
  }

  // ---- 3b. two orders of one plan get different amounts ------------------
  {
    const e = makeEnv();   // its own KV, so the sections below see what they expect
    const a = await order(e, { plan: 'a7d' });
    const b = await order(e, { plan: 'a7d' });
    check(a.json.amount !== b.json.amount,
      'two orders of the same plan owe different amounts',
      a.json.amount + ' vs ' + b.json.amount);
    check(b.json.unique - a.json.unique === 1,
      'the unique code advances by one', a.json.unique + ' -> ' + b.json.unique);
    const list = await admin(e, { action: 'list', secret: ADMIN, status: 'pending' });
    const amounts = list.json.orders.map((o) => o.amount);
    check(new Set(amounts).size === amounts.length,
      'every pending order has a distinct amount', amounts.join(', '));
    const paid = await admin(e, { action: 'paid', orderId: b.json.orderId, secret: ADMIN });
    check(paid.json.key && paid.json.amount === b.json.amount,
      'approving the right order mints its key', String(paid.json.amount));
  }

  // ---- 4. poll ----------------------------------------------------------
  {
    const r = await status(env, created.orderId, created.token);
    check(r.status === 200 && r.json.status === 'pending', 'polling shows pending');
    check(r.json.key === undefined, 'no key before payment');

    const wrong = await status(env, created.orderId, '0'.repeat(24));
    check(wrong.status === 404, 'a wrong token is a 404, not a key');

    const missing = await status(env, 'AND-222222', created.token);
    check(missing.status === 404, 'an unknown order is a 404');

    const junk = await status(env, 'not-an-order', created.token);
    check(junk.status === 404, 'a malformed id is a 404');
  }

  // ---- 5. admin auth ---------------------------------------------------
  {
    const r = await admin(env, { action: 'list' });
    check(r.status === 401, 'admin without a secret is refused');

    const r2 = await admin(env, { action: 'list', secret: 'wrong' });
    check(r2.status === 401, 'admin with a wrong secret is refused');

    const r3 = await admin(env, { action: 'list', secret: ADMIN });
    check(r3.status === 200 && r3.json.ok, 'admin with the secret works');
    check(r3.json.orders.length === 1 && r3.json.orders[0].status === 'pending',
      'the pending order is listed');
    check(!('token' in r3.json.orders[0]), 'the admin list never carries a token');
  }

  // ---- 6. approve ------------------------------------------------------
  {
    const r = await admin(env, { action: 'paid', orderId: created.orderId, secret: ADMIN });
    check(r.status === 200 && r.json.ok, 'admin can mark paid');
    check(/^[2-9A-Z]{4}(-[2-9A-Z]{4}){5}$/.test(r.json.key), 'a key was minted', r.json.key);

    const v = await verifyKey(r.json.key, { secret: SECRET, now: Date.now() });
    check(v.ok, 'the minted key verifies', v.reason);
    check(v.tier === 1, 'tier 1 for the 7-day plan', String(v.tier));
    check(v.left >= 6 && v.left <= 7, 'seven days left', String(v.left));

    const s = await status(env, created.orderId, created.token);
    check(s.json.status === 'paid', 'the buyer sees paid');
    check(s.json.key === r.json.key, 'and sees the same key');

    const again = await admin(env, { action: 'paid', orderId: created.orderId, secret: ADMIN });
    check(again.json.key === r.json.key, 'approving twice returns the first key, not a second');

    // a different order for the same plan must not produce the same key string
    const other = await order(env, { plan: 'a7d' });
    const otherPaid = await admin(env, { action: 'paid', orderId: other.json.orderId, secret: ADMIN });
    check(otherPaid.json.key !== r.json.key,
      'a second order for the same plan gets its own key', otherPaid.json.key);
    const v1 = await verifyKey(r.json.key, { secret: SECRET, now: Date.now() });
    const v2 = await verifyKey(otherPaid.json.key, { secret: SECRET, now: Date.now() });
    check(v1.ok && v2.ok, 'and both verify');
  }

  // ---- 7. device binding ----------------------------------------------
  {
    const androidId = 'a1b2c3d4e5f6a7b8';
    const r = await order(env, { plan: 'a1d', device: androidId });
    check(r.status === 200, 'an order can carry an android id');
    const paid = await admin(env, { action: 'paid', orderId: r.json.orderId, secret: ADMIN });
    const onDevice = await verifyKey(paid.json.key, { secret: SECRET, deviceId: androidId });
    const elsewhere = await verifyKey(paid.json.key, { secret: SECRET, deviceId: 'ffffffffffffffff' });
    check(onDevice.ok, 'a bound key works on its device');
    check(!elsewhere.ok && /another device/.test(elsewhere.reason),
      'and is refused on another', elsewhere.reason);

    const badId = await order(env, { plan: 'a1d', device: 'no way' });
    check(badId.status === 400, 'a silly android id is refused at creation');
  }

  // ---- 8. unknown plan -------------------------------------------------
  {
    const r = await order(env, { plan: '1d' });
    check(r.status === 400, 'the ios plan id is not an android plan');
    const r2 = await order(env, {});
    check(r2.status === 400, 'a missing plan is refused');
    const r3 = await order(env, 'not json');
    check(r3.status === 400, 'a non-object body is refused');
  }

  // ---- 9. cancel -------------------------------------------------------
  {
    const r = await order(env, { plan: 'a30d' });
    const c = await admin(env, { action: 'cancel', orderId: r.json.orderId, secret: ADMIN });
    check(c.json.status === 'cancelled', 'an order can be cancelled');
    const s = await status(env, r.json.orderId, r.json.token);
    check(s.json.status === 'cancelled' && s.json.key === undefined,
      'a cancelled order has no key');
    const p = await admin(env, { action: 'paid', orderId: r.json.orderId, secret: ADMIN });
    check(p.status === 200 && p.json.key, 'cancelling does not block a later approval');
  }

  // ---- 10. expiry is computed, not written -----------------------------
  {
    const r = await order(env, { plan: 'a1d' });
    const rec = await env.DEMIOS_KV.get('a:order:' + r.json.orderId, 'json');
    rec.expiresAt = Date.now() - 1000;
    await env.DEMIOS_KV.put('a:order:' + r.json.orderId, JSON.stringify(rec));
    const s = await status(env, r.json.orderId, r.json.token);
    check(s.json.status === 'expired', 'an order past its window reads as expired');
  }

  // ---- 11. rate limit --------------------------------------------------
  {
    const env2 = makeEnv({ ORDER_RATE_PER_HOUR: '2' });
    const ip = { 'cf-connecting-ip': '203.0.113.9' };
    const a = await order(env2, { plan: 'a1d' }, ip);
    const b = await order(env2, { plan: 'a1d' }, ip);
    const c = await order(env2, { plan: 'a1d' }, ip);
    check(a.status === 200 && b.status === 200, 'two orders from one ip are fine');
    check(c.status === 429, 'the third is rate limited', String(c.status));
    const other = await order(env2, { plan: 'a1d' }, { 'cf-connecting-ip': '198.51.100.4' });
    check(other.status === 200, 'a different ip is unaffected');
  }

  // ---- 12. webhook -----------------------------------------------------
  {
    const r = await order(env, { plan: 'a7d' });
    const off = await call(webhookFn, env, 'POST', '/api/android/webhook',
      { orderId: r.json.orderId, status: 'paid', amount: 140000, signature: 'x' });
    check(off.status === 503, 'the webhook is disabled without WEBHOOK_SECRET', String(off.status));

    const env3 = makeEnv({ WEBHOOK_SECRET: 'whsec-test' });
    const o = await order(env3, { plan: 'a7d' });
    const noSig = await call(webhookFn, env3, 'POST', '/api/android/webhook',
      { orderId: o.json.orderId, status: 'paid', amount: 140000 });
    check(noSig.status === 401, 'no signature is refused');

    const badSig = await call(webhookFn, env3, 'POST', '/api/android/webhook',
      { orderId: o.json.orderId, status: 'paid', amount: 140000, signature: 'deadbeef' });
    check(badSig.status === 401, 'a wrong signature is refused');

    const enc = new TextEncoder();
    const k = await crypto.subtle.importKey('raw', enc.encode('whsec-test'),
      { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    const mac = new Uint8Array(await crypto.subtle.sign('HMAC', k,
      enc.encode(o.json.orderId + ':paid:140000')));
    const sig = [...mac].map((b) => b.toString(16).padStart(2, '0')).join('');

    const good = await call(webhookFn, env3, 'POST', '/api/android/webhook',
      { orderId: o.json.orderId, status: 'paid', amount: 140000, signature: sig });
    check(good.status === 200 && good.json.ok, 'a signed webhook settles the order');
    const s = await status(env3, o.json.orderId, o.json.token);
    check(s.json.status === 'paid' && s.json.key, 'and the buyer has a key');

    const sigFor = async (status) => {
      const mac2 = new Uint8Array(await crypto.subtle.sign('HMAC', k,
        enc.encode(o.json.orderId + ':' + status + ':140000')));
      return [...mac2].map((b) => b.toString(16).padStart(2, '0')).join('');
    };

    const ignore = await call(webhookFn, env3, 'POST', '/api/android/webhook',
      { orderId: o.json.orderId, status: 'pending', amount: 140000, signature: await sigFor('pending') });
    check(ignore.json && ignore.json.ignored === 'pending',
      'a non-paid status is recorded and ignored', JSON.stringify(ignore.json));
  }

  // ---- 13. ad-hoc mint -------------------------------------------------
  {
    const r = await admin(env, { action: 'mint', days: 3, tier: 2, secret: ADMIN });
    check(r.status === 200 && /^[2-9A-Z]{4}(-[2-9A-Z]{4}){5}$/.test(r.json.key),
      'admin can mint a key by hand', r.json.key);
    const v = await verifyKey(r.json.key, { secret: SECRET });
    check(v.ok && v.tier === 2 && v.left === 3, 'three days, tier 2', v.left + 'd');
  }

  // ---- 14. no QRIS configured -----------------------------------------
  {
    const bare = makeEnv({ QRIS_STATIC: '' });
    const p = await call(plansFn, bare, 'GET', '/api/android/plans');
    check(p.json.ready === false && /QRIS_STATIC/.test(p.json.reason),
      'plans says why it is not ready', p.json.reason);
    const o = await order(bare, { plan: 'a1d' });
    check(o.status === 200 && o.json.configured === false && !o.json.qr,
      'an order still exists, but with no QR and an honest note', o.json.note);
  }

  // ---- 15. bad QRIS is caught, not passed through ----------------------
  {
    const broken = makeEnv({ QRIS_STATIC: STATIC.slice(0, -4) + '0000' });
    const p = await call(plansFn, broken, 'GET', '/api/android/plans');
    check(p.json.ready === false && /CRC/.test(p.json.reason),
      'a QRIS payload with a bad CRC is caught at the plan endpoint', p.json.reason);
  }

  // ---- 16. no KV -------------------------------------------------------
  {
    const noKv = { LICENSE_SECRET: SECRET, ADMIN_SECRET: ADMIN, QRIS_STATIC: STATIC };
    const o = await order(noKv, { plan: 'a1d' });
    check(o.status === 503 && /KV binding/.test(o.json.error),
      'a missing KV binding is reported clearly', o.json.error);
  }

  // ---- 17. methods -----------------------------------------------------
  {
    const g = await call(orderFn, env, 'GET', '/api/android/order');
    check(g.status === 404, 'GET without id/token is a 404');
    const put = await call(orderFn, env, 'PUT', '/api/android/order', {});
    check(put.status === 405, 'PUT is not allowed');
    const post = await call(plansFn, env, 'POST', '/api/android/plans', {});
    check(post.status === 405, 'POST to plans is not allowed');
    const getAdmin = await call(adminFn, env, 'GET', '/api/android/admin');
    check(getAdmin.status === 200 && getAdmin.json.method === 'POST',
      'GET on admin explains itself without leaking anything');
  }

  console.log();
  console.log('api: ' + pass + ' ok, ' + failed + ' failed');
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.log('[FAIL] harness crashed: ' + (e && e.stack ? e.stack : e));
  process.exit(1);
});
