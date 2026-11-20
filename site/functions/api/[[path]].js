// ===== functions/api/[[path]].js - a JSON 404 for the API namespace =====
//
// Anything under /api/ that is not one of the real routes answers JSON, not a
// web page. A client that expects JSON and receives HTML fails at parse time
// with something unhelpful; a 404 with a sentence in it is a five-second fix.
//
// This is also why the endpoint is written down rather than deleted silently:
// the iOS build called /api/buy, /api/order, /api/activate, /api/install,
// /api/admin and /api/config, and any copy of it still installed in the field
// will now find out exactly what happened.
//
// Routing: a more specific route wins, so /api/android/plans and friends are
// untouched. Verified against the live deployment with tools/smoke.py, which
// asserts that the real endpoints still answer - a catch-all that swallowed them
// would be worse than the problem it solves.

const RETIRED = {
  '/api/buy': 'the iOS storefront was removed',
  '/api/order': 'the iOS storefront was removed',
  '/api/activate': 'the iOS activation endpoint was removed',
  '/api/install': 'the iOS profile install endpoint was removed',
  '/api/admin': 'the iOS admin endpoint was removed',
  '/api/config': 'the iOS client config endpoint was removed',
};

export function onRequest(context) {
  const url = new URL(context.request.url);
  const path = url.pathname.replace(/\/+$/, '') || '/api';

  const why = RETIRED[path];
  return new Response(JSON.stringify({
    ok: false,
    error: why
      ? why + ' - this site sells the Android build only'
      : 'no such endpoint',
    path,
    hint: 'the endpoints that exist are /api/android/plans and /api/android/order',
  }, null, 2), {
    status: 404,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
    },
  });
}
