// ===== tools/pay_probe.mjs - ask a real gateway to make one QR, and say what came back
//
// Guessing a payment gateway's response shape is how a shop ends up with orders
// that look fine and never settle. This sends one create call with a test amount
// and reports, using the *same* functions production uses - extract() to choose
// the field, gatewayConfig() to choose the header, requestBody() to build the
// body. A green probe means the checkout will work, not that a second
// implementation agrees with itself.
//
//   node tools/pay_probe.mjs --url https://api.gateway/create --key sk_test_...
//   KLIKQRIS_CREATE_URL=... KLIKQRIS_KEY=... node tools/pay_probe.mjs
//
// It prints the status, the body (trimmed), which field was picked, and - when
// nothing matches - the exact env var to set. No order is created and nothing is
// stored; ref_id is PROBE-<random>.
//
// Exit codes:  0 found something   2 nothing matched   3 unreachable / HTTP error
import { extract, fieldList, gatewayConfig, requestBody } from '../functions/_lib/pay.js';

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const k = a.slice(2);
    const v = argv[i + 1] !== undefined && !argv[i + 1].startsWith('--') ? argv[++i] : 'true';
    out[k] = v;
  }
  return out;
}

const a = args(process.argv.slice(2));

// The CLI wins over the environment, so a value can be tried without exporting it.
const env = { ...process.env };
if (a.url) env.KLIKQRIS_CREATE_URL = a.url;
if (a.key) env.KLIKQRIS_KEY = a.key;
if (a['auth-header']) env.KLIKQRIS_AUTH_HEADER = a['auth-header'];
if (a['auth-prefix'] !== undefined) env.KLIKQRIS_AUTH_PREFIX = a['auth-prefix'];
if (a['merchant-id']) env.KLIKQRIS_MERCHANT_ID = a['merchant-id'];
if (a.body) env.KLIKQRIS_BODY = a.body;
if (a.qr) env.KLIKQRIS_QR_FIELDS = a.qr;
if (a.payment) env.KLIKQRIS_URL_FIELDS = a.payment;

const cfg = gatewayConfig(env);
const amount = Number(a.amount || 1000);
const ref = a.ref || ('PROBE-' + Math.random().toString(36).slice(2, 8).toUpperCase());

if (!cfg.url || !cfg.key) {
  console.error('need --url and --key (or KLIKQRIS_CREATE_URL + KLIKQRIS_KEY / KLIKQRIS_API_KEY)');
  process.exit(3);
}

const order = { id: ref, amount };

let payload;
try {
  payload = requestBody(env, order);
} catch (e) {
  console.error('KLIKQRIS_BODY is not valid JSON once the placeholders are filled: ' + e.message);
  process.exit(3);
}

const line = (k, v) => console.log(String(k).padEnd(16) + ' ' + v);

console.log('POST ' + cfg.url);
line('auth header', cfg.header + ': ' + cfg.prefix + cfg.key.slice(0, 4) + '...(' + cfg.key.length + ' chars)');
line('body', JSON.stringify(payload));
line('qr fields', fieldList(env, 'qr').join(', '));
line('url fields', fieldList(env, 'url').join(', '));
console.log();

let res;
try {
  res = await fetch(cfg.url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', [cfg.header]: cfg.prefix + cfg.key },
    body: JSON.stringify(payload),
  });
} catch (e) {
  console.error('unreachable: ' + (e && e.message ? e.message : e));
  process.exit(3);
}

const text = await res.text();
line('status', res.status + ' ' + res.statusText);
line('content-type', res.headers.get('content-type') || '(none)');
line('bytes', text.length);
console.log();

let data = null;
try {
  data = JSON.parse(text);
} catch {
  console.error('body is not JSON. First 400 bytes:');
  console.error(text.slice(0, 400));
  process.exit(res.ok ? 2 : 3);
}

console.log(JSON.stringify(data, null, 2).slice(0, 2000));
console.log();

const picked = extract(env, data);
if (picked.qr) line('qr_string', picked.qr.path + ' = ' + picked.qr.value.slice(0, 48) + '...');
if (picked.url) line('url', picked.url.path + ' = ' + picked.url.value);

if (picked.qr || picked.url) {
  console.log();
  console.log('[ok] production would accept this response.');
  process.exit(0);
}

console.log('[FAIL] no known field carries the QR or the payment URL.');
console.log('       look at the JSON above, then set the dotted path, e.g.:');
console.log('         KLIKQRIS_QR_FIELDS=result.qris_content');
console.log('         KLIKQRIS_URL_FIELDS=result.payment_link');
console.log('       and pass --qr / --payment on the next run to confirm before saving.');
process.exit(res.ok ? 2 : 3);
