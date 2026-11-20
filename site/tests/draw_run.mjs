// ===== tests/draw_run.mjs - public/qr.js draws the matrix it was given =====
//
// The server packs the matrix into base64; the page unpacks and draws it on a
// canvas. If the drawer transposes a row, shifts by one module, or forgets the
// quiet zone, the page still shows something that looks like a QR and no wallet
// will read it. So this runs the real file in a VM with a recording 2D context
// and rebuilds the matrix from the rectangles it drew.
import fs from 'node:fs';
import vm from 'node:vm';
import { payload } from '../functions/_lib/qr.js';

let pass = 0;
let failed = 0;
const ok = (m) => { pass++; console.log('[ok  ] ' + m); };
const bad = (m) => { failed++; console.log('[FAIL] ' + m); };
const check = (c, m, extra) => (c ? ok(m + (extra ? '  ' + extra : '')) : bad(m + (extra ? '  ' + extra : '')));

const src = fs.readFileSync(new URL('../public/qr.js', import.meta.url), 'utf8');

function loadDrawer() {
  const calls = [];
  const ctx2d = {
    fillStyle: '#000',
    fillRect(x, y, w, h) { calls.push({ x, y, w, h, fill: this.fillStyle }); },
  };
  const canvas = {
    width: 0,
    height: 0,
    style: {},
    getContext: () => ctx2d,
  };
  const sandbox = { window: {}, atob, Uint8Array, Math, console };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return { api: sandbox.window.DemiosQR, canvas, calls };
}

const TEXT = '00020101021226650014ID.CO.QRIS.WWW0117936005030000089820215ID20200000000000303UMI51440014ID.CO.QRIS.WWW0215ID20200000000000303UMI52045411530336054061400005802ID5912DEMIOS STORE6007JAKARTA6105123456209070503A016304C692';

function main() {
  const { api, canvas, calls } = loadDrawer();
  check(api && typeof api.draw === 'function', 'qr.js exposes DemiosQR.draw');
  check(typeof api.unpack === 'function', 'qr.js exposes unpack');

  const p = payload(TEXT);
  check(p.size > 20 && p.bits.length > 100, 'server packed a matrix',
    p.size + 'x' + p.size + ', ' + p.bits.length + ' b64 chars');

  const drew = api.draw(canvas, p.size, p.bits);
  check(drew === true, 'draw() returns true');
  check(canvas.width > 0 && canvas.width === canvas.height, 'canvas is square',
    canvas.width + 'px');
  check(!canvas.style.width, 'the drawer leaves sizing to CSS (.qr), not to an inline style');

  const bg = calls.filter((c) => c.x === 0 && c.y === 0 && c.w === canvas.width);
  check(bg.length === 1, 'exactly one background fill covers the whole canvas');

  const modules = calls.filter((c) => c.w === c.h && c.w < canvas.width);
  const dark = modules.length;

  const raw = api.unpack(p.bits);
  let want = 0;
  for (let i = 0; i < p.size * p.size; i++) if ((raw[i >> 3] >> (7 - (i & 7))) & 1) want++;
  check(dark === want, 'one rectangle per dark module', dark + ' drawn, ' + want + ' in the matrix');

  const px = modules.length ? modules[0].w : 0;
  check(px >= 3 && px * (p.size + 8) <= canvas.width + px,
    'module size is a whole number of pixels', px + 'px per module');

  // rebuild the matrix from the rectangles: same shape as the server sent?
  const dim = p.size + 8;
  const grid = new Uint8Array(dim * dim);
  for (const m of modules) {
    const cx = Math.round(m.x / px);
    const cy = Math.round(m.y / px);
    if (cx >= 0 && cy >= 0 && cx < dim && cy < dim) grid[cy * dim + cx] = 1;
  }

  let diff = 0;
  for (let y = 0; y < p.size; y++) {
    for (let x = 0; x < p.size; x++) {
      const i = y * p.size + x;
      const sent = (raw[i >> 3] >> (7 - (i & 7))) & 1;
      if (grid[(y + 4) * dim + (x + 4)] !== sent) diff++;
    }
  }
  check(diff === 0, 'every module lands at the same coordinate it came from', diff + ' wrong');

  // quiet zone: the 4-module border must be untouched
  let border = 0;
  for (let i = 0; i < dim; i++) {
    border += grid[i] + grid[(dim - 1) * dim + i] + grid[i * dim] + grid[i * dim + dim - 1];
  }
  check(border === 0, 'the quiet zone is empty', border + ' dark modules in the border');

  // a 1x1 code must not crash the drawer
  const tiny = loadDrawer();
  const tinyOk = tiny.api.draw(tiny.canvas, 1, 'gA==');
  check(tinyOk === true, 'a degenerate 1x1 matrix does not throw');

  // and a missing argument must be refused, not drawn
  check(api.draw(canvas, 0, '') === false, 'draw() refuses an empty matrix');

  console.log();
  console.log('draw: ' + pass + ' ok, ' + failed + ' failed');
  process.exit(failed ? 1 : 0);
}

try {
  main();
} catch (e) {
  console.log('[FAIL] draw harness crashed: ' + (e && e.stack ? e.stack : e));
  process.exit(1);
}
