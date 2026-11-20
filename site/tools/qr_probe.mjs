// ===== tools/qr_probe.mjs - dump QR matrices as JSON for the python test =====
// Usage: node tools/qr_probe.mjs < out.json
import { payload, svg, encode } from '../functions/_lib/qr.js';

const samples = [
  'A',
  'HELLO WORLD',
  'DEMIOS',
  'https://demios.pages.dev/android',
  // a realistic QRIS payload with a dynamic amount, tag 54 injected
  '00020101021226670016COM.NOBUBANK.WWW01189360050300000898210214300000000000030303UMI51440014ID.CO.QRIS.WWW0215ID20200000000000303UMI52045411530336054072500005802ID5910DEMIOSSTORE6007JAKARTA61051234062070703A0163041A2B',
  'X'.repeat(120),
  'Y'.repeat(200),
  'Z'.repeat(330),
  'Q'.repeat(420),
];

const out = samples.map((s) => {
  const code = encode(s);
  const packed = payload(s);
  return {
    text: s,
    version: code.version,
    size: code.size,
    mask: code.mask,
    // unpacked matrix, 0/1 per module, for a decoder that wants no guessing
    matrix: Array.from(code.modules),
    bits: packed.bits,
    svgLen: svg(s).length,
  };
});

process.stdout.write(JSON.stringify(out));
