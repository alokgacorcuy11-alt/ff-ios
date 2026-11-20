// ===== functions/_lib/qris.js - turn a static QRIS into one with the amount =====
//
// The seller has one static QRIS string from their merchant app (GoPay Merchant,
// DANA Bis, BRI, whatever). A static code has no amount: the buyer types it, and
// then nothing on this side can match a payment to an order.
//
// A dynamic code is the same string with tag 54 (transaction amount) inserted
// and the CRC in tag 63 recomputed. Every Indonesian wallet reads it, the amount
// is fixed at scan time, and the order can be matched by that amount.
//
// Layout is EMVCo TLV: tag(2 ascii digits) + length(2 ascii digits) + value.
// Tag 63 is the CRC: CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflect,
// no final xor) over the whole payload *including* the literal "6304".

/** CRC-16/CCITT-FALSE, uppercase hex, four digits. */
export function crc16(text) {
  let crc = 0xffff;
  for (let i = 0; i < text.length; i++) {
    crc ^= (text.charCodeAt(i) & 0xff) << 8;
    for (let b = 0; b < 8; b++) {
      crc = (crc & 0x8000) ? (((crc << 1) ^ 0x1021) & 0xffff) : ((crc << 1) & 0xffff);
    }
  }
  return crc.toString(16).toUpperCase().padStart(4, '0');
}

/** Split an EMVCo payload into [{tag, value}], in order. */
export function parse(text) {
  const s = String(text || '').trim();
  // QRIS is ASCII. Lengths count bytes, and every wallet agrees on that, so a
  // non-ASCII character here would make the length fields lie. Refuse it rather
  // than emit a payload that scans as corrupt.
  if (/[^\x20-\x7e]/.test(s)) {
    throw new Error('qris: payload is not ASCII');
  }
  const out = [];
  let i = 0;
  while (i < s.length) {
    if (i + 4 > s.length) {
      throw new Error('qris: header cut short at byte ' + i);
    }
    const tag = s.slice(i, i + 2);
    const lenText = s.slice(i + 2, i + 4);
    if (!/^[0-9]{2}$/.test(tag) || !/^[0-9]{2}$/.test(lenText)) {
      throw new Error('qris: not a TLV header at byte ' + i + ' (' + s.slice(i, i + 4) + ')');
    }
    const len = parseInt(lenText, 10);
    const value = s.slice(i + 4, i + 4 + len);
    if (value.length !== len) {
      throw new Error('qris: tag ' + tag + ' wants ' + len + ' bytes, has ' + value.length);
    }
    out.push({ tag, value });
    i += 4 + len;
  }
  return out;
}

export function build(items) {
  return items.map(({ tag, value }) => tag
    + String(value.length).padStart(2, '0') + value).join('');
}

/** Rebuild with a fresh CRC. `items` must not contain tag 63. */
export function seal(items) {
  const body = build(items.filter((t) => t.tag !== '63')) + '6304';
  return body + crc16(body);
}

/** True when the payload parses and its CRC checks out. */
export function valid(text) {
  try {
    const s = String(text || '').trim();
    if (s.length < 8) return false;
    const items = parse(s);
    const last = items[items.length - 1];
    if (!last || last.tag !== '63' || last.value.length !== 4) return false;
    const body = s.slice(0, s.length - 4);
    return crc16(body) === last.value.toUpperCase();
  } catch {
    return false;
  }
}

/** True when this looks like a QRIS payload at all (not just any EMVCo code). */
export function isQris(text) {
  try {
    const items = parse(String(text || '').trim());
    const head = items.find((t) => t.tag === '00');
    if (!head || head.value !== '01') return false;
    // 26-51 are the acquirer / merchant-account templates QRIS requires
    return items.some((t) => {
      const n = Number(t.tag);
      return n >= 26 && n <= 51;
    });
  } catch {
    return false;
  }
}

/**
 * Static payload -> dynamic payload carrying `amount` rupiah.
 *
 * Tag 01 becomes 12 (dynamic), tag 54 is inserted in order, the old CRC is
 * dropped and a new one sealed. Returns the new payload string.
 */
export function withAmount(staticPayload, amount) {
  const rp = Math.round(Number(amount));
  if (!Number.isFinite(rp) || rp <= 0) {
    throw new Error('qris: amount must be a positive number of rupiah');
  }
  if (rp > 9999999999999) {
    throw new Error('qris: amount is larger than the field allows');
  }

  const items = parse(staticPayload).filter((t) => t.tag !== '63' && t.tag !== '54');

  // Refuse anything that is not a merchant code. Without this, a truncated or
  // empty string would still produce a sealed payload that scans but has no
  // payee - the buyer pays, nothing arrives, and the order never closes.
  const head = items.find((t) => t.tag === '00');
  if (!head || head.value !== '01') {
    throw new Error('qris: payload does not start with format indicator 01');
  }
  const hasMerchant = items.some((t) => {
    const n = Number(t.tag);
    return n >= 26 && n <= 51;
  });
  if (!hasMerchant) {
    throw new Error('qris: payload has no merchant account (tag 26-51)');
  }

  const pmi = items.find((t) => t.tag === '01');
  if (pmi) pmi.value = '12';
  else items.unshift({ tag: '01', value: '12' });

  // root tags ascend; 54 belongs after 53 (currency) and before 55/56/57/58
  let at = items.findIndex((t) => Number(t.tag) > 54);
  if (at < 0) at = items.length;
  items.splice(at, 0, { tag: '54', value: String(rp) });

  return seal(items);
}

/** Pull the amount back out of a payload, or 0 when there is none. */
export function amountOf(payload) {
  try {
    const t = parse(payload).find((x) => x.tag === '54');
    return t ? Number(t.value) || 0 : 0;
  } catch {
    return 0;
  }
}
