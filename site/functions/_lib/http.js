// ===== functions/_lib/http.js - request/response plumbing =====

export function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      ...extra,
    },
  });
}

export function fail(message, status = 400, extra = {}) {
  return json({ ok: false, error: message, ...extra }, status);
}

export async function body(req) {
  try {
    const j = await req.json();
    return j && typeof j === 'object' ? j : null;
  } catch {
    return null;
  }
}

export function allow(allowList, method) {
  return new Response('method not allowed', {
    status: 405,
    headers: { allow: allowList.join(', '), 'content-type': 'text/plain' },
  });
}

/** Compare two secrets without leaking their length or content through timing. */
export function safeEqual(a, b) {
  const x = String(a == null ? '' : a);
  const y = String(b == null ? '' : b);
  if (x.length !== y.length) return false;
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x.charCodeAt(i) ^ y.charCodeAt(i);
  return diff === 0;
}

const HEX = '0123456789abcdef';

export function randomHex(bytes = 16) {
  const b = new Uint8Array(bytes);
  crypto.getRandomValues(b);
  let s = '';
  for (let i = 0; i < b.length; i++) s += HEX[b[i] >> 4] + HEX[b[i] & 15];
  return s;
}

// No 0/O/1/I, so an order id can be read aloud on the phone without a mistake.
const ID_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';

export function randomId(len = 6) {
  const b = new Uint8Array(len);
  crypto.getRandomValues(b);
  let s = '';
  for (let i = 0; i < len; i++) s += ID_ALPHABET[b[i] & 31];
  return s;
}

export function clientIp(req) {
  return req.headers.get('cf-connecting-ip') || req.headers.get('x-forwarded-for') || '';
}
