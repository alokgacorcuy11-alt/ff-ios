// ===== functions/_lib/plans.js - the Android price list =====
//
// One table, one place. The ids are deliberately distinct from the iOS store's
// ('1d','7d','30d'): both products share a Pages project and a KV namespace,
// and an Android order must never be answerable by the iOS key path or the
// other way round.
//
// `tier` goes into the key's payload nibble. License.java reports it; the panel
// does not gate on it yet, so 1 and 2 behave the same today. It is set now so
// that a future pro-only feature has something to read.
//
// Prices mirror the iOS ladder on purpose - a buyer who compares the two pages
// should not find a cheaper Android build and wonder which one is broken.

export const PLANS = [
  {
    id: 'a1d',
    label: '1 Hari',
    days: 1,
    tier: 1,
    price: 40000,
    tag: 'COBA',
  },
  {
    id: 'a7d',
    label: '7 Hari',
    days: 7,
    tier: 1,
    price: 140000,
    tag: 'PALING LAKU',
  },
  {
    id: 'a30d',
    label: '30 Hari',
    days: 30,
    tier: 2,
    price: 300000,
    tag: 'PRO',
  },
];

export function planById(id) {
  return PLANS.find((p) => p.id === id) || null;
}

export function publicPlans() {
  return PLANS.map((p) => ({
    id: p.id,
    label: p.label,
    days: p.days,
    tier: p.tier === 2 ? 'pro' : 'basic',
    price: p.price,
    tag: p.tag,
  }));
}

/** The cheapest plan, so the page can mark something when nothing else is. */
export function fallbackPlanId() {
  return PLANS.length ? PLANS[PLANS.length - 1].id : null;
}
