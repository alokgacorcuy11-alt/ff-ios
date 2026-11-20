// ===== public/qr.js - draw a QR matrix that the API sent as packed bits =====
//
// The API sends { size, bits } where bits is base64 of ceil(size^2/8) bytes,
// row-major, most significant bit first. That is ~800 characters for a QRIS
// code, against 40-60 KB of SVG path data for the same symbol.
//
// Rendered at ~1024 px wide no matter how small the element is: a phone camera
// gets more modules to work with, and CSS scales the element down.

(function () {
  function unpack(b64) {
    const s = atob(b64);
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }

  function draw(canvas, size, bits, opts) {
    if (!canvas || !size || !bits) return false;
    opts = opts || {};
    const quiet = opts.quiet == null ? 4 : opts.quiet;
    const dim = size + quiet * 2;
    const px = Math.max(3, Math.floor((opts.target || 1024) / dim));

    canvas.width = dim * px;
    canvas.height = dim * px;

    const ctx = canvas.getContext('2d');
    if (!ctx) return false;
    ctx.fillStyle = opts.light || '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = opts.dark || '#000000';

    const b = unpack(bits);
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const i = y * size + x;
        if ((b[i >> 3] >> (7 - (i & 7))) & 1) {
          ctx.fillRect((x + quiet) * px, (y + quiet) * px, px, px);
        }
      }
    }
    return true;
  }

  window.DemiosQR = { draw, unpack };
})();
