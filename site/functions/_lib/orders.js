// ===== functions/_lib/orders.js - order records in KV =====
//
// One key per order: a:order:<ID>. The record carries everything the page needs
// and the token that proves the reader is the buyer who created it.
//
// KV is eventually consistent (up to ~60 s across colos). That is fine for the
// flow it serves: the buyer creates an order, pays, and polls the same endpoint
// on the same connection - by the time an admin approves it, the write has
// propagated. What it is not fine for is using a KV read as a lock, which is
// why nothing here does.
//
// The token is 24 hex chars from a CSPRNG and compared in constant time. An
// order id is short and readable on purpose, so the token is what makes it
// unguessable - without it, /order?id=AND-XXXXXX would hand out other people's
// keys.

import { safeEqual, randomHex, randomId } from './http.js';
import { planById } from './plans.js';
import { mint, fingerprint, NONCE_MAX } from './key.js';

const PREFIX = 'a:order:';
const SEQ_KEY = 'a:seq';
const KEYSEQ_KEY = 'a:keyseq';
export const ORDER_TTL_MS = 30 * 60 * 1000; // a pending order lives 30 minutes

/**
 * The nonce that goes into a key, from a counter in KV.
 *
 * A counter, not a random number: it makes keys unique *by construction* rather
 * than by luck, across every plan and tier. 24 bits is 16.7 million, so wrapping
 * within one plan's expiry window is not a practical concern; if it ever did
 * wrap, the keys would still be valid, just occasionally repeated.
 */
export async function nextKeyNonce(env) {
  const kv = kvFor(env);
  let n = Number(await kv.get(KEYSEQ_KEY));
  if (!Number.isFinite(n) || n < 0) n = 0;
  n = (Math.floor(n) + 1) % NONCE_MAX;
  await kv.put(KEYSEQ_KEY, String(n));
  return n;
}

/**
 * A three-digit suffix, unique across the last 900 orders, added to the plan
 * price so every pending order has its own amount.
 *
 * Why: two buyers of the same plan otherwise owe the same rupiah, and matching a
 * payment to an order becomes guesswork from the timestamp in the merchant app.
 * With a unique amount, the number in the transaction identifies the order.
 * This is what the reference store does (a 40.000 plan showed up as 40.313,
 * 40.312, ...) and it is the single biggest quality-of-life difference in daily
 * operation.
 *
 * A KV read-modify-write, so it is not atomic: two orders created in the same
 * millisecond can draw the same suffix. With a 900-wide window and a 30-minute
 * order life, that means the admin occasionally has to match by time as well -
 * which is the fallback anyway, so nothing breaks.
 */
async function nextSuffix(kv) {
  let n = Number(await kv.get(SEQ_KEY));
  if (!Number.isFinite(n) || n < 0) n = 0;
  n = (Math.floor(n) + 1) % 900;
  await kv.put(SEQ_KEY, String(n));
  return 100 + n;
}

/** The binding name is not fixed: reuse whatever the project already has. */
export function kvFor(env) {
  const kv = env.DEMIOS_KV || env.ORDERS || env.STORE_KV || env.KV;
  if (!kv || typeof kv.get !== 'function') {
    throw new Error('no KV binding: bind one as DEMIOS_KV (wrangler.toml) and redeploy');
  }
  return kv;
}

export function secretFor(env) {
  const s = env.LICENSE_SECRET;
  if (!s || String(s).length < 16) {
    throw new Error('LICENSE_SECRET is not set (it must equal License.SECRET in the APK)');
  }
  return String(s);
}

export function isOrderId(id) {
  return typeof id === 'string' && /^AND-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}$/.test(id);
}

export function newOrderId() {
  return 'AND-' + randomId(6);
}

function shape(rec, now) {
  const status = rec.status === 'pending' && now > rec.expiresAt ? 'expired' : rec.status;
  return {
    ok: true,
    orderId: rec.id,
    plan: rec.plan,
    label: rec.label,
    days: rec.days,
    price: rec.price == null ? rec.amount : rec.price,
    unique: rec.price == null ? 0 : rec.amount - rec.price,
    amount: rec.amount,
    status,
    createdAt: rec.createdAt,
    expiresAt: rec.expiresAt,
    paidAt: rec.paidAt || 0,
    device: rec.device || '',
    key: status === 'paid' ? rec.key : undefined,
    keyPrint: status === 'paid' && rec.key ? fingerprint(rec.key) : undefined,
  };
}

/** What the buyer's own page is allowed to see. Never the token. */
export async function readOrder(env, id, token) {
  if (!isOrderId(id) || typeof token !== 'string' || token.length !== 24) return null;
  const kv = kvFor(env);
  const rec = await kv.get(PREFIX + id, 'json');
  if (!rec || !safeEqual(rec.token, token)) return null;
  return rec;
}

export async function createOrder(env, { planId, device = '', source = 'web' }) {
  const plan = planById(planId);
  if (!plan) throw new Error('unknown plan: ' + planId);

  const kv = kvFor(env);
  const now = Date.now();

  // A collision is a 1-in-a-billion event, but a duplicate id would hand one
  // buyer another buyer's key, so it is worth the extra read.
  let id = newOrderId();
  for (let i = 0; i < 4; i++) {
    const clash = await kv.get(PREFIX + id);
    if (!clash) break;
    id = newOrderId();
  }

  const rec = {
    v: 1,
    id,
    token: randomHex(12),
    plan: plan.id,
    label: plan.label,
    days: plan.days,
    tier: plan.tier,
    price: plan.price,
    amount: plan.price + (await nextSuffix(kv)),
    status: 'pending',
    createdAt: now,
    expiresAt: now + ORDER_TTL_MS,
    paidAt: 0,
    key: '',
    device: cleanDevice(device),
    source,
  };
  await kv.put(PREFIX + id, JSON.stringify(rec));
  return rec;
}

/** Android IDs are 16 hex chars; accept a wide band but nothing silly. */
export function cleanDevice(value) {
  const s = String(value == null ? '' : value).trim();
  if (!s) return '';
  if (!/^[A-Za-z0-9_-]{6,64}$/.test(s)) {
    throw new Error('android id must be 6-64 characters of letters, digits, - or _');
  }
  return s;
}

/**
 * Mark paid and mint the key. Idempotent: calling it twice returns the first
 * key instead of minting a second one, because a double-click on the admin page
 * must not cost the seller two licences.
 */
export async function markPaid(env, id, { device, note = '' } = {}) {
  const kv = kvFor(env);
  const rec = await kv.get(PREFIX + id, 'json');
  if (!rec) throw new Error('no such order: ' + id);
  if (rec.status === 'paid' && rec.key) return rec;

  const secret = secretFor(env);
  const bound = device !== undefined ? cleanDevice(device) : rec.device;
  const key = await mint({
    secret,
    days: rec.days,
    tier: rec.tier,
    deviceId: bound,
    nonce: await nextKeyNonce(env),
    now: Date.now(),
  });

  const next = {
    ...rec,
    status: 'paid',
    paidAt: Date.now(),
    key,
    device: bound,
    note: note || rec.note || '',
  };
  await kv.put(PREFIX + id, JSON.stringify(next));
  return next;
}

export async function cancelOrder(env, id, reason = '') {
  const kv = kvFor(env);
  const rec = await kv.get(PREFIX + id, 'json');
  if (!rec) throw new Error('no such order: ' + id);
  if (rec.status === 'paid') throw new Error('order is already paid; minting is final');
  const next = { ...rec, status: 'cancelled', note: reason || rec.note || '' };
  await kv.put(PREFIX + id, JSON.stringify(next));
  return next;
}

/**
 * Newest first, across the whole namespace. 1000 keys is KV's page size and
 * more than a small shop writes in a day, so one page is normally everything.
 */
export async function listOrders(env, { limit = 100, cursor = null, status = null } = {}) {
  const kv = kvFor(env);
  const page = await kv.list({ prefix: PREFIX, limit: Math.min(1000, Math.max(1, limit)), cursor });
  const records = await Promise.all(
    page.keys.map((k) => kv.get(k.name, 'json')),
  );
  const now = Date.now();
  let rows = records.filter(Boolean).map((r) => ({
    ...shape(r, now),
    token: undefined,
    createdAt: r.createdAt,
    source: r.source || 'web',
    note: r.note || '',
  }));
  if (status) rows = rows.filter((r) => r.status === status);
  rows.sort((a, b) => b.createdAt - a.createdAt);
  return {
    ok: true,
    orders: rows,
    cursor: page.list_complete === false ? page.cursor : null,
    complete: page.list_complete !== false,
  };
}

export { shape };
