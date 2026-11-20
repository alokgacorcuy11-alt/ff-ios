import { encode } from '../functions/_lib/qr.js';
const text = process.argv[2] || 'HELLO WORLD';
const out = [];
for (let mask = 0; mask < 8; mask++) {
  const c = encode(text, { mask });
  out.push({ mask, v: c.version, size: c.size, m: Array.from(c.modules) });
}
process.stdout.write(JSON.stringify(out));
