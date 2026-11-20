// ===== functions/api/android/plans.js - GET the Android price list =====

import { publicPlans } from '../../_lib/plans.js';
import { json, allow } from '../../_lib/http.js';
import { payMode, qrisConfig } from '../../_lib/pay.js';

function get(env) {
  const mode = payMode(env);
  const cfg = qrisConfig(env);
  const ready = mode === 'klikqris'
    ? Boolean(env.KLIKQRIS_CREATE_URL && env.KLIKQRIS_KEY)
    : cfg.ok;

  return json({
    ok: true,
    platform: 'android',
    gateway: mode,
    ready,
    reason: ready ? undefined : (mode === 'klikqris'
      ? 'gateway credentials are not configured'
      : cfg.reason),
    plans: publicPlans(),
  });
}

export function onRequest(context) {
  const method = context.request.method;
  if (method === 'GET' || method === 'HEAD') return get(context.env);
  return allow(['GET', 'HEAD'], method);
}
