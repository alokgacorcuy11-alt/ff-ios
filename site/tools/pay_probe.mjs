// ===== tools/pay_probe.mjs - ask a real gateway to make one QR, and say what came back
//
// Guessing a payment gateway's response shape is how a shop ends up with orders
// that look fine and never settle. This sends one create call with a test
// amount and reports, using the *same* extract() that production uses:
//
//   node tools/pay_probe.mjs --url https://api.gateway/create --key sk_test_...
//   KLIKQRIS_CREATE_URL=... KLIKQRIS_KEY=... node tools/pay_probe.mjs
//
// It prints the status, the body (trimmed), which field was picked, and - when
// nothing matches - the exact env var to set. Nothing is stored and no order is
// created; ref_id is PROBE-<random>.
//
// Exit codes:  0 found something   2 nothing matched   3 unreachable / HTTP error
import { extract, fieldList } from '../functions/_lib/pay.js';

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
const url = a.url || process.env.KLIKQRIS_CREATE_URL || '';
const key = a.key || process.env.KLIKQRIS_KEY || '';
const amount = Number(a.amount || 1000);
const ref = a.ref || ('PROBE-' + Math.random().toString(36).slice(2, 8).toUpperCase());

if (!url || !key) {
  console.error('need --url and --key (or KLIKQRIS_CREATE_URL / KLIKQRIS_KEY)');
  process.exit(3);
}

// A body the way the adapter sends it, unless the account wants something else.
const payload = a.body
  ? JSON.parse(a.body)
  : { amount, ref_id: ref };

const env = {
  KLIKQRIS_QR_FIELDS: a.qr || process.env.KLIKQRIS_QR_FIELDS || '',
  KLIKQRIS_URL_FIELDS: a.payment || process.env.KLIKQRIS_URL_FIELDS || '',
};

const line = (k, v) => console.log(String(k).padEnd(14) + ' ' + v);

console.log('POST ' + url);
console.log('body ' + JSON.stringify(payload));
console.log('qr fields   ' + fieldList(env, 'qr').join(', '));
console.log('url fields  ' + fieldList(env, 'url').join(', '));
console.log();

let res;
try {
  res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-api-key': key },
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
console.log('       look at the JSON above and set, e.g.:');
console.log('       wrangler pages secret put KLIKQRIS_QR_FIELDS --project-name demios');
console.log('       # value: the dotted path of the string, comma separated if several');
process.exit(res.ok ? 2 : 3);
