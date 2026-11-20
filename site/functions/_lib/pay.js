// ===== functions/_lib/pay.js - how the buyer is asked to pay =====
//
// Two modes, chosen by PAY_MODE:
//
//   manual    (default) the seller's static QRIS is turned into a dynamic code
//             for this order's amount. The buyer scans and pays; the seller
//             approves from /android-admin; the key is minted at that moment.
//             No gateway account, no callback URL, no API key. This is the mode
//             that works today.
//
//   klikqris  POST to the gateway's create endpoint and hand back whatever QR
//             or URL it returns. The request and response shapes below are the
//             only thing that needs editing to match the account - they are
//             isolated in one function for exactly that reason.
//
// A webhook exists (functions/api/android/webhook.js) but refuses to do
// anything unless WEBHOOK_SECRET is set. An unauthenticated endpoint that marks
// orders paid is a free-licence endpoint.

import { withAmount, valid, isQris, amountOf } from './qris.js';
import { payload as qrPayload } from './qr.js';

export function payMode(env) {
  return String(env.PAY_MODE || 'manual').toLowerCase();
}

/** Validate the seller's static payload once, so a typo is caught at startup. */
export function qrisConfig(env) {
  const raw = env.QRIS_STATIC;
  if (!raw) return { ok: false, reason: 'QRIS_STATIC is not set' };
  const s = String(raw).trim();
  if (!valid(s)) return { ok: false, reason: 'QRIS_STATIC fails its own CRC' };
  if (!isQris(s)) return { ok: false, reason: 'QRIS_STATIC is not a QRIS merchant code' };
  return { ok: true, payload: s };
}

function qrFor(text) {
  const p = qrPayload(text);
  return { size: p.size, bits: p.bits, version: p.version };
}

/**
 * Build the payment block for an order.
 * Returns { mode, qr, payUrl, note, configured }.
 */
export async function present(env, order) {
  const mode = payMode(env);
  if (mode === 'klikqris') return klikqris(env, order);
  return manual(env, order);
}

function manual(env, order) {
  const cfg = qrisConfig(env);
  if (!cfg.ok) {
    return {
      mode: 'manual',
      configured: false,
      qr: null,
      payUrl: env.PAY_URL || null,
      note: cfg.reason + ' - hubungi admin untuk pembayaran manual.',
    };
  }
  const dyn = withAmount(cfg.payload, order.amount);
  return {
    mode: 'manual',
    configured: true,
    qr: qrFor(dyn),
    payUrl: null,
    note: 'Scan, bayar tepat Rp' + Number(order.amount).toLocaleString('id-ID')
      + ', lalu tekan CEK STATUS. Key muncul setelah pembayaran dikonfirmasi.',
  };
}

/**
 * klikqris adapter.
 *
 * Expected contract - edit this function if the account differs:
 *
 *   POST $KLIKQRIS_CREATE_URL
 *   x-api-key: $KLIKQRIS_KEY
 *   { "amount": 140000, "ref_id": "AND-7K2M9Q" }
 *
 *   -> { "qr_string": "000201..." }      or  { "data": { "qr_string": "..." } }
 *   -> { "qr_url": "https://..." }       or  { "checkout_url": "..." }
 *
 * Anything else is reported with the status code and the first 200 bytes of the
 * body, because guessing at a gateway's field names produces orders that look
 * fine and never settle.
 */
async function klikqris(env, order) {
  const url = env.KLIKQRIS_CREATE_URL;
  const key = env.KLIKQRIS_KEY;
  if (!url || !key) {
    return {
      mode: 'klikqris',
      configured: false,
      qr: null,
      payUrl: null,
      note: 'PAY_MODE=klikqris but KLIKQRIS_CREATE_URL / KLIKQRIS_KEY are not set.',
    };
  }

  let res;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-api-key': key },
      body: JSON.stringify({ amount: order.amount, ref_id: order.id }),
    });
  } catch (e) {
    throw new Error('gateway unreachable: ' + (e && e.message ? e.message : e));
  }

  const text = await res.text();
  let data = null;
  try { data = JSON.parse(text); } catch { /* handled below */ }

  if (!res.ok || !data) {
    throw new Error('gateway ' + res.status + ': ' + text.slice(0, 200));
  }

  const pick = (obj, keys) => {
    for (const k of keys) {
      const v = k.split('.').reduce((o, part) => (o && o[part] !== undefined ? o[part] : undefined), obj);
      if (typeof v === 'string' && v) return v;
    }
    return null;
  };

  const qrString = pick(data, ['qr_string', 'qrString', 'qris', 'data.qr_string', 'data.qrString', 'data.qris']);
  const payUrl = pick(data, ['qr_url', 'qrUrl', 'checkout_url', 'checkoutUrl', 'pay_url', 'payment_url', 'data.qr_url', 'data.checkout_url']);

  if (qrString) {
    // Some gateways return a static code and expect the caller to add the
    // amount; others return one with tag 54 already in it. Detect, do not guess.
    let text2 = qrString;
    if (valid(text2) && amountOf(text2) !== order.amount) {
      try { text2 = withAmount(text2, order.amount); } catch { /* leave as-is */ }
    }
    return {
      mode: 'klikqris',
      configured: true,
      qr: qrFor(text2),
      payUrl: null,
      note: 'Scan QRIS, lalu tunggu key muncul otomatis.',
    };
  }

  if (payUrl) {
    return {
      mode: 'klikqris',
      configured: true,
      qr: null,
      payUrl,
      note: 'Buka halaman pembayaran, lalu tunggu key muncul otomatis.',
    };
  }

  throw new Error('gateway returned no qr_string and no url: ' + text.slice(0, 200));
}

/** Verify a webhook body. Fail closed when no secret is configured. */
export async function verifyWebhook(env, data) {
  const secret = env.WEBHOOK_SECRET;
  if (!secret) return { ok: false, reason: 'WEBHOOK_SECRET is not set; webhooks are disabled' };
  const { orderId, status, amount, signature } = data || {};
  if (!orderId || !status || signature === undefined) {
    return { ok: false, reason: 'orderId, status and signature are required' };
  }
  const material = orderId + ':' + status + ':' + (amount == null ? '' : amount);
  const enc = new TextEncoder();
  const k = await crypto.subtle.importKey(
    'raw', enc.encode(String(secret)), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
  );
  const mac = new Uint8Array(await crypto.subtle.sign('HMAC', k, enc.encode(material)));
  let hex = '';
  for (let i = 0; i < mac.length; i++) hex += mac[i].toString(16).padStart(2, '0');

  const want = String(signature).toLowerCase();
  if (want.length !== hex.length) return { ok: false, reason: 'bad signature' };
  let diff = 0;
  for (let i = 0; i < hex.length; i++) diff |= hex.charCodeAt(i) ^ want.charCodeAt(i);
  if (diff !== 0) return { ok: false, reason: 'bad signature' };
  return { ok: true };
}
