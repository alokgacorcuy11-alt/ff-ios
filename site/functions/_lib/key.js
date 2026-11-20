// ===== functions/_lib/key.js - the activation key, minted server-side =====
//
// This is the same key format as tools/keygen.py and the same one License.java
// checks on the phone. Three implementations now, so the arithmetic is pinned
// by tests on both sides: python -> java in tools/tests/test_license.py, and
// node -> java in site/tests/test_site_key.py. If this file drifts, the site
// sells keys the app refuses, and that test is the only thing that notices.
//
//   XXXX-XXXX-XXXX-XXXX-XXXX          20 chars in five groups
//
//   alphabet   23456789ABCDEFGHJKLMNPQRSTUVWXYZ   (no 0/O/1/I)
//   payload    12 chars = 7 bytes
//              b0       version (high nibble) | tier (low nibble)
//              b1..b2   expiry: days since 2026-01-01 UTC, LE. 0 = never
//              b3..b6   device hash, LE. 0 = any device
//   mac        8 chars = 5 bytes = HMAC-SHA256(secret, payload)[0:5]
//
// Two things that must match License.java exactly:
//
//   * the day count. keygen.py uses local date.today(); the app uses
//     (nowMs - EPOCH_MS) / 86400000, which is UTC. A server in Jakarta and a
//     phone in Jakarta can disagree by a day for seven hours every night, so
//     the site follows the app, not keygen.py. `now` is injectable for tests.
//   * the device hash is a *signed* int in Java and is written as four
//     little-endian bytes. Writing the unsigned value produces the same bytes,
//     which is why `>>> 0` appears below and not `| 0`.

const ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';
const VERSION = 1;
const PAYLOAD_CHARS = 12;
const MAC_CHARS = 8;
export const EPOCH_MS = 1767225600000; // 2026-01-01T00:00:00Z
const DAY_MS = 86400000;

const enc = new TextEncoder();

function toHex(buf) {
  const b = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < b.length; i++) s += b[i].toString(16).padStart(2, '0');
  return s;
}

/** Fixed-width base32, five bits per character, most significant first. */
export function b32encode(bytes, chars) {
  let bits = 0n;
  for (let i = 0; i < bytes.length; i++) bits = (bits << 8n) | BigInt(bytes[i]);
  bits <<= BigInt(chars * 5 - bytes.length * 8);
  let out = '';
  for (let i = chars - 1; i >= 0; i--) {
    out += ALPHABET[Number((bits >> BigInt(i * 5)) & 31n)];
  }
  return out;
}

export function b32decode(text, chars) {
  const total = chars * 5;
  let bits = 0n;
  for (let i = 0; i < text.length; i++) {
    const idx = ALPHABET.indexOf(text[i]);
    if (idx < 0) throw new Error('not in the alphabet: ' + text[i]);
    bits = (bits << 5n) | BigInt(idx);
  }
  const nbytes = Math.floor(total / 8);
  bits >>= BigInt(total - nbytes * 8);
  const out = new Uint8Array(nbytes);
  for (let i = nbytes - 1; i >= 0; i--) {
    out[i] = Number(bits & 0xffn);
    bits >>= 8n;
  }
  return out;
}

/** The day number License.java computes: UTC, from 2026-01-01. */
export function dayNumber(nowMs) {
  return Math.floor((nowMs - EPOCH_MS) / DAY_MS);
}

/** sha256(secret + "|" + deviceId)[0:4] read as a big-endian u32. */
export async function deviceHash(secret, deviceId) {
  if (!deviceId) return 0;
  const data = new Uint8Array(enc.encode(secret).length + 1 + enc.encode(deviceId).length);
  data.set(enc.encode(secret), 0);
  data[enc.encode(secret).length] = 0x7c; // '|'
  data.set(enc.encode(deviceId), enc.encode(secret).length + 1);
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', data));
  return (((digest[0] << 24) | (digest[1] << 16) | (digest[2] << 8) | digest[3]) >>> 0);
}

async function hmac(secret, payload) {
  const key = await crypto.subtle.importKey(
    'raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
  );
  return new Uint8Array(await crypto.subtle.sign('HMAC', key, payload));
}

function payloadBytes(version, tier, exp, device) {
  return new Uint8Array([
    ((version & 0xf) << 4) | (tier & 0xf),
    exp & 0xff,
    (exp >>> 8) & 0xff,
    device & 0xff,
    (device >>> 8) & 0xff,
    (device >>> 16) & 0xff,
    (device >>> 24) & 0xff,
  ]);
}

/** 20 chars -> "XXXX-XXXX-XXXX-XXXX-XXXX" */
export function group(body) {
  return body.match(/.{1,4}/g).join('-');
}

/**
 * Mint a key.
 *
 * @param {object} o
 * @param {string} o.secret   must equal License.SECRET
 * @param {number} o.days     days from now; 0 or less means never expires
 * @param {number} [o.tier]   1 basic, 2 pro
 * @param {string} [o.deviceId] Android ID to bind to; empty = any device
 * @param {number} [o.now]    epoch ms, for tests
 */
export async function mint({ secret, days = 30, tier = 1, deviceId = '', now = Date.now() }) {
  if (!secret) throw new Error('key: no secret');
  const n = Math.trunc(Number(days));
  if (!Number.isFinite(n)) throw new Error('key: days is not a number');
  if (n < 0) throw new Error('key: days cannot be negative');
  const t = Math.trunc(Number(tier));
  if (!(t >= 0 && t <= 15)) throw new Error('key: tier must be 0..15');

  const exp = n <= 0 ? 0 : (dayNumber(now) + n) & 0xffff;
  const device = await deviceHash(secret, String(deviceId || ''));
  const payload = payloadBytes(VERSION, t, exp, device);
  const mac = (await hmac(secret, payload)).slice(0, 5);
  return group(b32encode(payload, PAYLOAD_CHARS) + b32encode(mac, MAC_CHARS));
}

/** Same decisions as License.verify, for the admin panel and the tests. */
export async function verify(key, { secret, deviceId = '', now = Date.now() }) {
  const body = String(key || '').replace(/[- ]/g, '').toUpperCase();
  if (body.length !== PAYLOAD_CHARS + MAC_CHARS) {
    return { ok: false, reason: 'a key is 20 characters, this one is ' + body.length };
  }
  let payload;
  let mac;
  try {
    payload = b32decode(body.slice(0, PAYLOAD_CHARS), PAYLOAD_CHARS);
    mac = b32decode(body.slice(PAYLOAD_CHARS), MAC_CHARS);
  } catch (e) {
    return { ok: false, reason: e.message };
  }
  const version = (payload[0] >> 4) & 0xf;
  const tier = payload[0] & 0xf;
  if (version !== VERSION) {
    return { ok: false, reason: 'unsupported version ' + version };
  }
  const want = (await hmac(secret, payload)).slice(0, 5);
  let same = want.length === mac.length;
  for (let i = 0; same && i < want.length; i++) same = want[i] === mac[i];
  if (!same) return { ok: false, reason: 'the signature does not match' };

  const device = (payload[3] | (payload[4] << 8) | (payload[5] << 16) | (payload[6] << 24)) >>> 0;
  if (device !== 0) {
    if (!deviceId) return { ok: false, reason: 'this key is bound to a device', tier };
    if ((await deviceHash(secret, deviceId)) !== device) {
      return { ok: false, reason: 'this key belongs to another device', tier };
    }
  }

  const exp = payload[1] | (payload[2] << 8);
  let left = -1;
  if (exp !== 0) {
    left = exp - dayNumber(now);
    if (left < 0) {
      return { ok: false, reason: 'expired ' + (-left) + ' days ago', tier, left };
    }
  }
  return { ok: true, reason: 'ok', tier, left };
}

/** Short digest of a key, safe to put in a log or an admin list. */
export function fingerprint(key) {
  const body = String(key || '').replace(/[- ]/g, '').toUpperCase();
  let h = 0x811c9dc5;
  for (let i = 0; i < body.length; i++) {
    h ^= body.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, '0');
}

export { toHex };
