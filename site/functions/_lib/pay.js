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
  // Two decimals by default: it is what the observed acquirer writes (a 40.000
  // plan came back as 40.313.00), so the code we build is the same shape as the
  // one the merchant issues. Set QRIS_DECIMALS=0 for a payload whose acquirer
  // writes whole rupiah.
  const decimals = String(env.QRIS_DECIMALS == null ? 2 : env.QRIS_DECIMALS) === '0' ? 0 : 2;
  const dyn = withAmount(cfg.payload, order.amount, { decimals });
  const rp = Number(order.amount).toLocaleString('id-ID');
  const unique = order.price ? order.amount - order.price : 0;
  return {
    mode: 'manual',
    configured: true,
    qr: qrFor(dyn),
    payUrl: null,
    note: 'Scan dan bayar tepat Rp' + rp
      + (unique ? ' - tiga digit terakhir itu kode unik pesananmu (' + unique + ')' : '')
      + '. Tekan CEK STATUS setelah bayar; key muncul begitu pembayaran dikonfirmasi.',
  };
}

/**
 * Pick the payment material out of a gateway response.
 *
 * Exported because tools/pay_probe.mjs runs this exact function against a live
 * gateway, so what the probe reports is what production would do - not a second
 * implementation that can drift.
 *
 * The field lists are overridable so a gateway with a different shape needs an
 * env var, not a code edit:
 *
 *   KLIKQRIS_QR_FIELDS=result.qris_content,qr.payload
 *   KLIKQRIS_URL_FIELDS=result.payment_link
 */
export function fieldList(env, which) {
  const name = which === 'qr' ? 'KLIKQRIS_QR_FIELDS' : 'KLIKQRIS_URL_FIELDS';
  const raw = env && env[name];
  if (raw && String(raw).trim()) {
    return String(raw).split(',').map((s) => s.trim()).filter(Boolean);
  }
  return which === 'qr'
    ? ['qr_string', 'qrString', 'qris', 'qr_content', 'qris_content',
      'data.qr_string', 'data.qrString', 'data.qris', 'data.qr_content', 'result.qr_string']
    : ['qr_url', 'qrUrl', 'checkout_url', 'checkoutUrl', 'pay_url', 'payment_url',
      'payment_link', 'data.qr_url', 'data.checkout_url', 'data.payment_url', 'result.payment_url'];
}

function dig(obj, path) {
  return path.split('.').reduce((o, part) => (o && o[part] !== undefined ? o[part] : undefined), obj);
}

export function extract(env, data) {
  const first = (paths) => {
    for (const p of paths) {
      const v = dig(data, p);
      if (typeof v === 'string' && v) return { path: p, value: v };
    }
    return null;
  };
  return {
    qr: first(fieldList(env, 'qr')),
    url: first(fieldList(env, 'url')),
  };
}

/**
 * How to talk to the gateway: endpoint, credential, header name, request body.
 *
 * Everything is overridable because the account is the account, not the code:
 *
 *   KLIKQRIS_CREATE_URL            the endpoint
 *   KLIKQRIS_KEY                   the credential. Falls back to
 *                                  KLIKQRIS_API_KEY, which is the name the
 *                                  existing production project already uses -
 *                                  so the credential you have works here too.
 *   KLIKQRIS_AUTH_HEADER           default "x-api-key"
 *   KLIKQRIS_AUTH_PREFIX           default "" - set "Bearer " if the account
 *                                  wants Authorization: Bearer <key>
 *   KLIKQRIS_BODY                  a JSON template with {{amount}}, {{ref_id}},
 *                                  {{merchant_id}} substituted. Default:
 *                                  {"amount":N,"ref_id":"AND-XXXXXX"} plus
 *                                  merchant_id when KLIKQRIS_MERCHANT_ID is set.
 */
export function gatewayConfig(env) {
  const key = env.KLIKQRIS_KEY || env.KLIKQRIS_API_KEY || '';
  const header = (env.KLIKQRIS_AUTH_HEADER || 'x-api-key').trim();
  const prefix = env.KLIKQRIS_AUTH_PREFIX == null ? '' : String(env.KLIKQRIS_AUTH_PREFIX);
  return {
    url: env.KLIKQRIS_CREATE_URL || '',
    key,
    header,
    prefix,
    ready: Boolean(env.KLIKQRIS_CREATE_URL && key),
  };
}

export function requestBody(env, order) {
  const merchant = env.KLIKQRIS_MERCHANT_ID || '';
  const tpl = env.KLIKQRIS_BODY;
  if (tpl && String(tpl).trim()) {
    const filled = String(tpl)
      .replace(/\{\{amount\}\}/g, String(order.amount))
      .replace(/\{\{ref_id\}\}/g, order.id)
      .replace(/\{\{merchant_id\}\}/g, merchant);
    return JSON.parse(filled);
  }
  const body = { amount: order.amount, ref_id: order.id };
  if (merchant) body.merchant_id = merchant;
  return body;
}

/**
 * klikqris adapter.
 *
 * Sends one create call and reads the QR string or payment URL out of the
 * response. Nothing about the account's field names is assumed: unknown shapes
 * are refused with the status and the first 200 bytes of the body, because
 * guessing at a gateway's field names produces orders that look fine and never
 * settle. Confirm the shape first with tools/pay_probe.mjs.
 */
async function klikqris(env, order) {
  const cfg = gatewayConfig(env);
  if (!cfg.ready) {
    return {
      mode: 'klikqris',
      configured: false,
      qr: null,
      payUrl: null,
      note: 'PAY_MODE=klikqris but KLIKQRIS_CREATE_URL / KLIKQRIS_KEY (or KLIKQRIS_API_KEY) are not set.',
    };
  }

  let res;
  try {
    res = await fetch(cfg.url, {
      method: 'POST',
      headers: { 'content-type': 'application/json', [cfg.header]: cfg.prefix + cfg.key },
      body: JSON.stringify(requestBody(env, order)),
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

  const picked = extract(env, data);
  const qrString = picked.qr && picked.qr.value;
  const payUrl = picked.url && picked.url.value;

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

  throw new Error('gateway returned no qr_string and no url: ' + text.slice(0, 200)
    + '  (set KLIKQRIS_QR_FIELDS / KLIKQRIS_URL_FIELDS to the real field names)');
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
