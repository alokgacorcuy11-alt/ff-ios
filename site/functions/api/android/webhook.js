// ===== functions/api/android/webhook.js - gateway callback =====
//
// Disabled unless WEBHOOK_SECRET is set. A webhook that marks orders paid
// without a signature is a free-licence endpoint, and a shop with a public URL
// will find out the hard way.
//
//   POST { orderId, status, amount, signature }
//   signature = hex(HMAC-SHA256(WEBHOOK_SECRET, orderId + ":" + status + ":" + amount))
//
// `status` is "paid" to settle. Anything else is recorded and ignored.

import { json, fail, body, allow } from '../../_lib/http.js';
import { markPaid, isOrderId, kvFor } from '../../_lib/orders.js';
import { verifyWebhook } from '../../_lib/pay.js';

async function handle(context) {
  const { env, request } = context;
  const data = await body(request);
  if (!data) return fail('body must be JSON');

  const check = await verifyWebhook(env, data);
  if (!check.ok) return fail(check.reason, check.reason.includes('not set') ? 503 : 401);

  if (!isOrderId(data.orderId)) return fail('bad orderId');

  const status = String(data.status || '').toLowerCase();
  if (status !== 'paid' && status !== 'settled' && status !== 'success') {
    return json({ ok: true, ignored: status || '(empty)' });
  }

  try {
    const rec = await markPaid(env, data.orderId, { note: 'webhook' });
    return json({ ok: true, orderId: rec.id, status: rec.status });
  } catch (e) {
    return fail(e.message, 404);
  }
}

export function onRequest(context) {
  const method = context.request.method;
  if (method === 'POST') return handle(context);
  if (method === 'GET') {
    // A GET here is almost always someone checking whether the URL is live.
    // Answer honestly without doing anything.
    let kvReady = true;
    try { kvFor(context.env); } catch { kvReady = false; }
    return json({
      ok: true,
      webhook: context.env.WEBHOOK_SECRET ? 'enabled' : 'disabled (set WEBHOOK_SECRET)',
      kv: kvReady ? 'bound' : 'missing',
    });
  }
  return allow(['POST'], method);
}
