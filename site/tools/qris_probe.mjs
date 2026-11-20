// ===== tools/qris_probe.mjs - exercise the QRIS builder for the python test =====
import { crc16, parse, build, withAmount, valid, amountOf, isQris } from '../functions/_lib/qris.js';

// A tiny independent TLV assembler. Deliberately does not import from qris.js:
// the fixture must not be built by the code that is being tested.
const tlv = (tag, value) => tag + String(value.length).padStart(2, '0') + value;

function staticQris() {
  const account26 = tlv('00', 'ID.CO.QRIS.WWW')
    + tlv('01', '93600503000008982')
    + tlv('02', 'ID2020000000000')
    + tlv('03', 'UMI');
  const account51 = tlv('00', 'ID.CO.QRIS.WWW')
    + tlv('02', 'ID2020000000000')
    + tlv('03', 'UMI');
  const additional = tlv('07', '03A01');
  const body = tlv('00', '01')
    + tlv('01', '11')            // static
    + tlv('26', account26)
    + tlv('51', account51)
    + tlv('52', '5411')
    + tlv('53', '360')
    + tlv('58', 'ID')
    + tlv('59', 'DEMIOS STORE')
    + tlv('60', 'JAKARTA')
    + tlv('61', '12345')
    + tlv('62', additional)
    + '6304';
  return body + crc16(body);
}

const STATIC = staticQris();
const out = { static: STATIC };

// 1. CRC-16/CCITT-FALSE check value. "123456789" -> 0x29B1 is the canonical
//    vector for this polynomial, published in every CRC catalogue.
out.crcCheck = { want: '29B1', got: crc16('123456789') };

// 2. the fixture parses, validates, and is recognised as QRIS
out.staticValid = valid(STATIC);
out.staticIsQris = isQris(STATIC);
out.staticTags = parse(STATIC).map((t) => t.tag);
out.staticAmount = amountOf(STATIC);
out.staticPmi = (parse(STATIC).find((t) => t.tag === '01') || {}).value;

// 3. static -> dynamic
const dyn = withAmount(STATIC, 140000);
out.dynamic = dyn;
out.dynamicValid = valid(dyn);
out.dynamicIsQris = isQris(dyn);
out.dynamicAmount = amountOf(dyn);
out.dynamicPmi = (parse(dyn).find((t) => t.tag === '01') || {}).value;
out.dynamicTags = parse(dyn).map((t) => t.tag);

// 4. amount sits between currency (53) and the next tag, exactly once
const tags = out.dynamicTags;
out.amountPosition = tags.indexOf('54');
out.amountOnce = tags.filter((t) => t === '54').length;
out.amountAfter53 = tags.indexOf('54') > tags.indexOf('53');
out.amountBefore58 = tags.indexOf('54') < tags.indexOf('58');

// 5. re-applying replaces the amount instead of appending a second one
const again = withAmount(dyn, 300000);
out.againValid = valid(again);
out.againAmount = amountOf(again);
out.againCount = parse(again).filter((t) => t.tag === '54').length;

// 6. the merchant fields survive untouched
const before = parse(STATIC).filter((t) => t.tag !== '63' && t.tag !== '01');
const after = parse(dyn).filter((t) => t.tag !== '63' && t.tag !== '01' && t.tag !== '54');
out.merchantSame = JSON.stringify(before) === JSON.stringify(after);

// 7. every length field is still honest: parse -> build is the identity
out.rebuildIdentity = build(parse(STATIC)) === STATIC;
out.rebuildDynamic = build(parse(dyn)) === dyn;
out.lengthsOk = out.rebuildIdentity && out.rebuildDynamic;

// 8. garbage is refused, not silently mangled
out.rejects = [];
for (const bad of ['', '00', '0002', 'hello', '000201', '0002010102',
  '000201010211' + '99', '00\u00e9020101']) {
  let threw = false;
  try { withAmount(bad, 1000); } catch { threw = true; }
  out.rejects.push({ bad, threw });
}

// 9. amounts that make no sense are refused
out.badAmounts = [];
for (const amount of [0, -1, NaN, 'abc', 1e14]) {
  let threw = false;
  try { withAmount(STATIC, amount); } catch { threw = true; }
  out.badAmounts.push({ amount: String(amount), threw });
}

// 10. many amounts produce distinct, valid payloads
const seen = new Set();
let allValid = true;
for (const amount of [1000, 25000, 40000, 140000, 300000, 999999]) {
  const p = withAmount(STATIC, amount);
  if (!valid(p)) allValid = false;
  seen.add(p);
}
out.sweep = { distinct: seen.size, allValid };

process.stdout.write(JSON.stringify(out));
