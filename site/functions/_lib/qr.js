// ===== functions/_lib/qr.js - QR Code, byte mode, EC level M =====
//
// Why this exists: the checkout page has to show a QRIS code with the exact
// amount baked in. That means building a QR from an arbitrary string on the
// server, with no npm dependency (Pages Functions bundle, not a Node app).
//
// Scope is deliberately narrow: byte mode, error correction M, versions 1..20.
// That covers 666 bytes at v20 - a QRIS payload with a dynamic amount is
// 200-400 bytes - and it is the subset that every wallet reads.
//
// The output is the module matrix, not an image. The matrix packs to
// ceil(size^2/8) bytes, which the page draws on a canvas. A data-URI SVG of the
// same code is 40-60 KB of path data per order; this is ~800 bytes.
//
// Verified in tools/tests/test_site_qr.py: the matrix is rendered to a bitmap
// and decoded back with OpenCV, for every version boundary.

// ---- GF(256), polynomial 0x11D -------------------------------------------
const EXP = new Uint8Array(512);
const LOG = new Uint8Array(256);
{
  let x = 1;
  for (let i = 0; i < 255; i++) {
    EXP[i] = x;
    LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
}

function gmul(a, b) {
  if (a === 0 || b === 0) return 0;
  return EXP[LOG[a] + LOG[b]];
}

function genPoly(n) {
  let g = [1];
  for (let i = 0; i < n; i++) {
    const next = new Array(g.length + 1).fill(0);
    const a = EXP[i];
    for (let j = 0; j < g.length; j++) {
      next[j] ^= g[j];
      next[j + 1] ^= gmul(g[j], a);
    }
    g = next;
  }
  return g;
}

function rsEncode(data, ecLen) {
  const g = genPoly(ecLen);
  const res = new Uint8Array(data.length + ecLen);
  res.set(data);
  for (let i = 0; i < data.length; i++) {
    const c = res[i];
    if (c === 0) continue;
    for (let j = 0; j < g.length; j++) res[i + j] ^= gmul(g[j], c);
  }
  return res.subarray(data.length);
}

// ---- version / level tables ----------------------------------------------
// Level M only. [ecCodewordsPerBlock, group1Blocks, group1Data,
//                group2Blocks, group2Data]
const BLOCKS_M = [
  null,
  [10, 1, 16, 0, 0], [16, 1, 28, 0, 0], [26, 1, 44, 0, 0],
  [18, 2, 32, 0, 0], [24, 2, 43, 0, 0], [16, 4, 27, 0, 0],
  [18, 4, 31, 0, 0], [22, 2, 38, 2, 39], [22, 3, 36, 2, 37],
  [26, 4, 43, 1, 44], [30, 1, 50, 4, 51], [22, 6, 36, 2, 37],
  [22, 8, 37, 1, 38], [24, 4, 40, 5, 41], [24, 5, 41, 5, 42],
  [28, 7, 45, 3, 46], [28, 10, 46, 1, 47], [26, 9, 43, 4, 44],
  [26, 3, 44, 11, 45], [26, 3, 41, 13, 42],
];

const ALIGN = [
  null, [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34],
  [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50], [6, 30, 54],
  [6, 32, 58], [6, 34, 62], [6, 26, 46, 66], [6, 26, 48, 70],
  [6, 26, 50, 74], [6, 30, 54, 78], [6, 30, 56, 82], [6, 30, 58, 86],
  [6, 34, 62, 90],
];

const MAX_VERSION = 20;
const EC_M_BITS = 0; // level M is 00b in the format information

// ---- bit helpers ----------------------------------------------------------
function bitLength(n) {
  let len = 0;
  while (n !== 0) { len++; n >>>= 1; }
  return len;
}

function bchFormat(data) {
  let d = data << 10;
  while (bitLength(d) - 11 >= 0) d ^= 0x537 << (bitLength(d) - 11);
  return ((data << 10) | d) ^ 0x5412;
}

function bchVersion(version) {
  let d = version << 12;
  while (bitLength(d) - 13 >= 0) d ^= 0x1f25 << (bitLength(d) - 13);
  return (version << 12) | d;
}

// ---- codeword construction ------------------------------------------------
function dataCodewords(bytes, version) {
  const spec = BLOCKS_M[version];
  const total = spec[1] * spec[2] + spec[3] * spec[4];
  const capBits = total * 8;
  const ccBits = version <= 9 ? 8 : 16;

  const bits = [];
  const push = (val, len) => {
    for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1);
  };
  push(0b0100, 4);              // byte mode
  push(bytes.length, ccBits);
  for (let i = 0; i < bytes.length; i++) push(bytes[i], 8);

  if (bits.length > capBits) {
    throw new Error('qr: ' + bytes.length + ' bytes does not fit v' + version);
  }
  for (let i = 0; i < 4 && bits.length < capBits; i++) bits.push(0);
  while (bits.length % 8 !== 0) bits.push(0);

  const cw = [];
  for (let i = 0; i < bits.length; i += 8) {
    let v = 0;
    for (let j = 0; j < 8; j++) v = (v << 1) | bits[i + j];
    cw.push(v);
  }
  const pad = [0xec, 0x11];
  for (let k = 0; cw.length < total; k++) cw.push(pad[k & 1]);
  return cw;
}

function interleave(cw, version) {
  const spec = BLOCKS_M[version];
  const [ecLen, b1, d1, b2, d2] = spec;
  const blocks = [];
  let off = 0;
  for (let i = 0; i < b1; i++) { blocks.push(cw.slice(off, off + d1)); off += d1; }
  for (let i = 0; i < b2; i++) { blocks.push(cw.slice(off, off + d2)); off += d2; }

  const out = [];
  const maxLen = Math.max(d1, d2);
  for (let i = 0; i < maxLen; i++) {
    for (const b of blocks) if (i < b.length) out.push(b[i]);
  }
  const ecs = blocks.map((b) => rsEncode(b, ecLen));
  for (let i = 0; i < ecLen; i++) for (const e of ecs) out.push(e[i]);
  return out;
}

// ---- matrix ---------------------------------------------------------------
function maskFn(mask, r, c) {
  switch (mask) {
    case 0: return (r + c) % 2 === 0;
    case 1: return r % 2 === 0;
    case 2: return c % 3 === 0;
    case 3: return (r + c) % 3 === 0;
    case 4: return (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0;
    case 5: return ((r * c) % 2) + ((r * c) % 3) === 0;
    case 6: return (((r * c) % 2) + ((r * c) % 3)) % 2 === 0;
    case 7: return (((r + c) % 2) + ((r * c) % 3)) % 2 === 0;
    default: throw new Error('qr: bad mask ' + mask);
  }
}

function buildMatrix(version, mask, codewords) {
  const size = version * 4 + 17;
  const m = new Int8Array(size * size).fill(-1);
  const set = (r, c, v) => {
    if (r < 0 || c < 0 || r >= size || c >= size) return;
    m[r * size + c] = v ? 1 : 0;
  };

  // finder patterns + separators
  for (const [fr, fc] of [[0, 0], [0, size - 7], [size - 7, 0]]) {
    for (let r = -1; r <= 7; r++) {
      for (let c = -1; c <= 7; c++) {
        const ring = (r >= 0 && r <= 6 && (c === 0 || c === 6))
          || (c >= 0 && c <= 6 && (r === 0 || r === 6));
        const core = r >= 2 && r <= 4 && c >= 2 && c <= 4;
        set(fr + r, fc + c, ring || core);
      }
    }
  }

  // timing patterns
  for (let i = 8; i < size - 8; i++) {
    set(6, i, i % 2 === 0);
    set(i, 6, i % 2 === 0);
  }

  // alignment patterns
  const pos = ALIGN[version];
  for (let i = 0; i < pos.length; i++) {
    for (let j = 0; j < pos.length; j++) {
      const r = pos[i];
      const c = pos[j];
      if ((r === 6 && c === 6) || (r === 6 && c === size - 7)
        || (r === size - 7 && c === 6)) continue;
      for (let dr = -2; dr <= 2; dr++) {
        for (let dc = -2; dc <= 2; dc++) {
          set(r + dr, c + dc, Math.max(Math.abs(dr), Math.abs(dc)) !== 1);
        }
      }
    }
  }

  // format information (also writes the always-dark module)
  const fmt = bchFormat((EC_M_BITS << 3) | mask);
  for (let i = 0; i < 15; i++) {
    const on = ((fmt >> i) & 1) === 1;
    if (i < 6) set(i, 8, on);
    else if (i < 8) set(i + 1, 8, on);
    else set(size - 15 + i, 8, on);

    if (i < 8) set(8, size - i - 1, on);
    else if (i === 8) set(8, 7, on);
    else set(8, 15 - i - 1, on);
  }
  set(size - 8, 8, 1);

  // version information
  if (version >= 7) {
    const ver = bchVersion(version);
    for (let i = 0; i < 18; i++) {
      const on = ((ver >> i) & 1) === 1;
      set(Math.floor(i / 3), (i % 3) + size - 11, on);
      set((i % 3) + size - 11, Math.floor(i / 3), on);
    }
  }

  // data, two columns at a time, bottom-right, snaking upward
  let inc = -1;
  let row = size - 1;
  let bit = 7;
  let byte = 0;
  for (let col = size - 1; col > 0; col -= 2) {
    if (col === 6) col--;
    for (;;) {
      for (let c = 0; c < 2; c++) {
        const cc = col - c;
        if (m[row * size + cc] !== -1) continue;
        let on = false;
        if (byte < codewords.length) on = ((codewords[byte] >>> bit) & 1) === 1;
        if (maskFn(mask, row, cc)) on = !on;
        m[row * size + cc] = on ? 1 : 0;
        bit--;
        if (bit === -1) { byte++; bit = 7; }
      }
      row += inc;
      if (row < 0 || row >= size) { row -= inc; inc = -inc; break; }
    }
  }
  return m;
}

// ---- penalty (ISO 18004 section 8.8.2) -----------------------------------
function penalty(m, size) {
  let score = 0;

  // rule 1: runs of five or more
  for (let i = 0; i < size; i++) {
    let runH = 1;
    let runV = 1;
    for (let j = 1; j < size; j++) {
      if (m[i * size + j] === m[i * size + j - 1]) runH++;
      else { if (runH >= 5) score += runH - 2; runH = 1; }
      if (m[j * size + i] === m[(j - 1) * size + i]) runV++;
      else { if (runV >= 5) score += runV - 2; runV = 1; }
    }
    if (runH >= 5) score += runH - 2;
    if (runV >= 5) score += runV - 2;
  }

  // rule 2: 2x2 blocks of one colour
  for (let r = 0; r < size - 1; r++) {
    for (let c = 0; c < size - 1; c++) {
      const v = m[r * size + c];
      if (v === m[r * size + c + 1] && v === m[(r + 1) * size + c]
        && v === m[(r + 1) * size + c + 1]) score += 3;
    }
  }

  // rule 3: the finder-like 1:1:3:1:1 sequence with four light modules
  const pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0];
  const pat2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1];
  const match = (get, i) => {
    let a = true;
    let b = true;
    for (let k = 0; k < 11; k++) {
      const v = get(i + k);
      if (v !== pat1[k]) a = false;
      if (v !== pat2[k]) b = false;
      if (!a && !b) return 0;
    }
    return (a ? 40 : 0) + (b ? 40 : 0);
  };
  for (let r = 0; r < size; r++) {
    const getRow = (c) => m[r * size + c];
    for (let c = 0; c <= size - 11; c++) score += match(getRow, c);
  }
  for (let c = 0; c < size; c++) {
    const getCol = (r) => m[r * size + c];
    for (let r = 0; r <= size - 11; r++) score += match(getCol, r);
  }

  // rule 4: deviation from 50% dark
  let dark = 0;
  for (let i = 0; i < m.length; i++) dark += m[i];
  const pct = (dark * 100) / (size * size);
  score += Math.floor(Math.abs(pct - 50) / 5) * 10;
  return score;
}

// ---- public ---------------------------------------------------------------
/**
 * Encode a string. Returns { version, size, mask, modules } where modules is a
 * Uint8Array of size*size, row-major, 1 = dark.
 */
export function encode(text, opts = {}) {
  const bytes = new TextEncoder().encode(String(text));
  let version = opts.version || 0;
  if (!version) {
    for (let v = 1; v <= MAX_VERSION; v++) {
      const spec = BLOCKS_M[v];
      const dataCw = spec[1] * spec[2] + spec[3] * spec[4];
      const ccBits = v <= 9 ? 8 : 16;
      if (4 + ccBits + bytes.length * 8 <= dataCw * 8) { version = v; break; }
    }
    if (!version) throw new Error('qr: ' + bytes.length + ' bytes is over v' + MAX_VERSION);
  }
  if (version < 1 || version > MAX_VERSION) throw new Error('qr: version out of range');

  const codewords = interleave(dataCodewords(bytes, version), version);

  let best = null;
  const masks = opts.mask == null ? [0, 1, 2, 3, 4, 5, 6, 7] : [opts.mask];
  for (const mask of masks) {
    const m = buildMatrix(version, mask, codewords);
    const p = penalty(m, version * 4 + 17);
    if (!best || p < best.penalty) best = { mask, modules: m, penalty: p };
  }

  return {
    version,
    size: version * 4 + 17,
    mask: best.mask,
    modules: best.modules,
  };
}

/** Pack the matrix for the wire: base64 of ceil(size^2/8) bytes, MSB first. */
export function pack(code) {
  const { size, modules } = code;
  const n = Math.ceil((size * size) / 8);
  const out = new Uint8Array(n);
  for (let i = 0; i < size * size; i++) {
    if (modules[i]) out[i >> 3] |= 0x80 >> (i & 7);
  }
  let s = '';
  for (let i = 0; i < n; i++) s += String.fromCharCode(out[i]);
  return btoa(s);
}

/** { version, size, bits } - everything the page needs to draw it. */
export function payload(text, opts) {
  const code = encode(text, opts);
  return { version: code.version, size: code.size, bits: pack(code) };
}

/** Standalone SVG, for anything that cannot run the canvas drawer. */
export function svg(text, opts = {}) {
  const code = encode(text, opts);
  const { size, modules } = code;
  const margin = opts.margin == null ? 4 : opts.margin;
  const dim = size + margin * 2;
  const dark = opts.dark || '#000000';
  const light = opts.light || '#ffffff';

  let path = '';
  for (let y = 0; y < size; y++) {
    let x = 0;
    while (x < size) {
      if (!modules[y * size + x]) { x++; continue; }
      let run = 1;
      while (x + run < size && modules[y * size + x + run]) run++;
      path += 'M' + (x + margin) + ' ' + (y + margin) + 'h' + run + 'v1h-' + run + 'z';
      x += run;
    }
  }
  return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + dim + ' ' + dim
    + '" shape-rendering="crispEdges" role="img" aria-label="QR">'
    + '<rect width="' + dim + '" height="' + dim + '" fill="' + light + '"/>'
    + '<path d="' + path + '" fill="' + dark + '"/></svg>';
}
