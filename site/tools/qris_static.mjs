// ===== tools/qris_static.mjs - recover the merchant template from a dynamic QR =====
//
// A seller usually has a static QRIS behind an image in a merchant app, and
// nothing else. But if anything on the site already issues dynamic codes for the
// same merchant (a gateway, another page), the template is *in* that payload:
// a static and a dynamic code differ in exactly two fields.
//
//   tag 01   11 (static) vs 12 (dynamic)
//   tag 54   absent      vs the amount
//
// So this takes one dynamic payload, sets 01 back to 11, drops 54, reseals the
// CRC, and then proves the result: put the same amount back and the bytes must
// come out identical, CRC included. When they do, the template is exact - it is
// not a guess about which merchant fields to keep.
//
//   node tools/qris_static.mjs "00020101021226...6304XXXX"
//   node tools/qris_static.mjs --image qris.png
//   node tools/qris_static.mjs --check "$QRIS_STATIC" "00020101021226...6304XXXX"
//
// Exit codes: 0 verified   1 the round trip did not reproduce the payload   2 bad input
import { parse, withAmount, toTemplate, valid, isQris, amountOf, crc16, build }
  from '../functions/_lib/qris.js';

const argv = process.argv.slice(2);
const flag = (name) => {
  const i = argv.indexOf(name);
  if (i < 0) return null;
  const v = argv[i + 1];
  return v && !v.startsWith('--') ? v : 'true';
};

function deriveOne(dynamic) {
  const d = String(dynamic || '').trim();
  if (!d) throw new Error('empty payload');
  if (!valid(d)) throw new Error('the payload fails its own CRC - retype or re-decode it');

  const items = parse(d);
  const amt = items.find((t) => t.tag === '54');
  if (!amt) throw new Error('this is already a static payload (no tag 54) - it is its own template');
  if (!isQris(d)) throw new Error('this does not look like a QRIS merchant code');

  const template = toTemplate(d);
  const decimals = amt.value.includes('.') ? 2 : 0;
  const back = withAmount(template, Number(amt.value), { decimals });

  // Compare structurally as well, so the failure says which field moved rather
  // than only "the bytes differ".
  const a = parse(d);
  const b = parse(back);
  const diffs = [];
  const max = Math.max(a.length, b.length);
  for (let i = 0; i < max; i++) {
    const x = a[i];
    const y = b[i];
    if (!x || !y) { diffs.push(`tag ${(x || y).tag} present in only one of them`); continue; }
    if (x.tag !== y.tag) { diffs.push(`position ${i}: ${x.tag} vs ${y.tag}`); continue; }
    if (x.tag === '63') continue;
    if (x.tag === '54') {
      if (Number(x.value) !== Number(y.value)) diffs.push(`54: ${x.value} vs ${y.value}`);
      continue;
    }
    if (x.value !== y.value) diffs.push(`${x.tag}: differs`);
  }

  return {
    template,
    amount: amt.value,
    decimals,
    reproduced: back,
    identical: back === d,
    diffs,
    crc: { live: (items.find((t) => t.tag === '63') || {}).value, recomputed: crc16(back.slice(0, -4)) },
  };
}

const check = flag('--check');
if (check) {
  // Verify a template that is already in use, without deriving a new one.
  const dynamic = argv[argv.indexOf('--check') + 2];
  if (!dynamic) { console.error('--check needs: <template> <dynamic payload>'); process.exit(2); }
  const items = parse(String(dynamic).trim());
  const amt = items.find((t) => t.tag === '54');
  const decimals = amt && amt.value.includes('.') ? 2 : 0;
  const back = withAmount(check, Number(amt ? amt.value : 1), { decimals });
  const same = back === String(dynamic).trim();
  console.log('template valid:', valid(check), ' isQris:', isQris(check));
  console.log('rebuild identical:', same);
  if (!same) {
    console.log('  built:', back);
    console.log('  live :', String(dynamic).trim());
    console.log('  first difference at', [...back].findIndex((c, i) => c !== String(dynamic).trim()[i]));
  }
  process.exit(same ? 0 : 1);
}

const image = flag('--image');
let payload = flag('--payload') || (argv[0] && !argv[0].startsWith('--') ? argv[0] : null);

if (image && image !== 'true') {
  const { spawnSync } = await import('node:child_process');
  const r = spawnSync('python', ['tools/qris_from_image.py', image], { encoding: 'utf8' });
  if (r.status !== 0 || !r.stdout) {
    console.error('could not decode ' + image + ': ' + (r.stderr || '').trim());
    process.exit(2);
  }
  payload = r.stdout.trim().split('\n')[0];
  console.log('decoded from ' + image + ' (' + payload.length + ' bytes)');
}

if (!payload) {
  console.error('usage: node tools/qris_static.mjs <dynamic payload> | --image qris.png | --check <template> <dynamic>');
  process.exit(2);
}

let out;
try {
  out = deriveOne(payload);
} catch (e) {
  console.error(String(e.message || e));
  process.exit(2);
}

console.log();
console.log('amount in the payload : ' + out.amount + (out.decimals === 2 ? '  (2 decimals)' : ''));
console.log('template              : ' + out.template);
console.log('rebuild with the amount: ' + (out.identical ? 'byte-identical, CRC included' : 'DIFFERENT'));
if (!out.identical) {
  console.log('  live CRC ' + out.crc.live + '  recomputed ' + out.crc.recomputed);
  for (const d of out.diffs.slice(0, 8)) console.log('  ' + d);
  console.log();
  console.log('The template is not right for this payload. Do not use it.');
  process.exit(1);
}

console.log();
console.log('tags: ' + parse(out.template).map((t) => t.tag).join(' '));
console.log('merchant name: ' + ((parse(out.template).find((t) => t.tag === '59') || {}).value || '(none)'));
console.log('merchant city: ' + ((parse(out.template).find((t) => t.tag === '60') || {}).value || '(none)'));
console.log();
console.log('This is a static template for one merchant. Set it as QRIS_STATIC, and keep it');
console.log('out of a public repository: it identifies the merchant underneath it.');
console.log();
console.log(build(parse(out.template)) === out.template ? 'parse -> build reproduces it exactly.' : 'WARNING: lengths are inconsistent');
process.exit(0);
