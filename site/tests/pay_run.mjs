// ===== tests/pay_run.mjs - the gateway adapter against a local fake gateway =====
//
// PAY_MODE=klikqris is the mode nobody can test until the account exists. So the
// shapes are simulated here: a real HTTP server, the real present() function, the
// real QRIS builder. What is asserted is the decision, not a mock's return value:
//
//   flat qr_string        -> a QR whose payload carries this order's amount
//   nested data.qr_string -> same
//   checkout_url only     -> a payUrl, no QR
//   unknown nesting       -> refused, with the env var to set
//   unknown shape         -> refused, no silent empty checkout
//   HTTP 500 / not JSON   -> refused with the status
//   the account's own header and body shape are configurable, not assumed
//
// The probe is run as a subprocess too, because that is what the seller will use
// against the live account.
import http from 'node:http';
import { present, extract, fieldList, gatewayConfig, requestBody }
  from '../functions/_lib/pay.js';
import { payload as qrPayload } from '../functions/_lib/qr.js';
import { withAmount, valid, amountOf } from '../functions/_lib/qris.js';

let pass = 0;
let failed = 0;
const ok = (m) => { pass++; console.log('[ok  ] ' + m); };
const bad = (m) => { failed++; console.log('[FAIL] ' + m); };
const check = (c, m, extra) => (c ? ok(m + (extra ? '  ' + extra : '')) : bad(m + (extra ? '  ' + extra : '')));

const STATIC = '00020101021126650014ID.CO.QRIS.WWW0117936005030000089820215ID20200000000000303UMI'
  + '51440014ID.CO.QRIS.WWW0215ID20200000000000303UMI5204541153033605802ID5912DEMIOS STORE'
  + '6007JAKARTA6105123456209070503A0163048160';

const ORDER = { id: 'AND-7K2M9Q', amount: 140000, plan: 'a7d', label: '7 Hari', days: 7, tier: 1 };

const ROUTES = {
  '/flat-qr': () => ({ qr_string: STATIC }),
  '/nested-qr': () => ({ status: 'success', data: { qr_string: STATIC, expired_at: '2026-11-20T10:00:00Z' } }),
  '/url': () => ({ checkout_url: 'https://pay.example.test/abc123' }),
  '/weird': () => ({ result: { qris_content: STATIC } }),
  '/garbage': () => ({ foo: 'bar', nested: { nothing: true } }),
};

let seen = [];   // { path, header, apiKey, auth, body }

async function start() {
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      const send = (code, obj, type = 'application/json') => {
        res.writeHead(code, { 'content-type': type });
        res.end(typeof obj === 'string' ? obj : JSON.stringify(obj));
      };
      if (req.method !== 'POST') return send(405, { error: 'POST only' });

      const apiKey = req.headers['x-api-key'];
      const auth = req.headers.authorization;
      if (apiKey !== 'probe-key' && auth !== 'Bearer probe-key') {
        return send(401, { error: 'bad key' });
      }

      let parsed = null;
      try { parsed = JSON.parse(body); } catch { /* ignored */ }
      seen.push({ path: req.url, apiKey, auth, body: parsed });

      const route = ROUTES[req.url];
      if (route) {
        if (!parsed || typeof parsed !== 'object') return send(400, { error: 'no body' });
        return send(200, route(parsed));
      }
      if (req.url === '/500') return send(500, { error: 'internal' });
      if (req.url === '/html') return send(200, '<html>not json</html>', 'text/html');
      return send(404, { error: 'no such route' });
    });
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, port: server.address().port };
}

const base = (port, path) => 'http://127.0.0.1:' + port + path;

async function main() {
  const { server, port } = await start();
  const env = (path, extra = {}) => ({
    PAY_MODE: 'klikqris',
    KLIKQRIS_CREATE_URL: base(port, path),
    KLIKQRIS_KEY: 'probe-key',
    ...extra,
  });

  // ---- 1. the amount must be in the QR, not in the buyer's head ----------
  const expect = qrPayload(withAmount(STATIC, ORDER.amount));
  {
    seen = [];
    const pay = await present(env('/flat-qr'), ORDER);
    check(pay.configured === true && pay.qr, 'flat qr_string -> a QR is returned');
    check(pay.qr.size === expect.size && pay.qr.bits === expect.bits,
      'the QR is the static code with this order\'s amount baked in',
      pay.qr.size + 'x' + pay.qr.size);
    check(pay.payUrl === null, 'no payUrl when a QR is present');
    check(seen.length === 1 && seen[0].body.amount === 140000 && seen[0].body.ref_id === ORDER.id,
      'the request carried this order\'s amount and id', JSON.stringify(seen[0] && seen[0].body));
  }

  // ---- 2. nested shapes are normal ---------------------------------------
  {
    const pay = await present(env('/nested-qr'), ORDER);
    check(pay.qr && pay.qr.bits === expect.bits, 'nested data.qr_string is found');
  }

  // ---- 3. URL-only gateways ----------------------------------------------
  {
    const pay = await present(env('/url'), ORDER);
    check(pay.qr === null && pay.payUrl === 'https://pay.example.test/abc123',
      'checkout_url becomes payUrl', String(pay.payUrl));
  }

  // ---- 4. an unknown nesting is refused, then fixed by an env var --------
  {
    let threw = null;
    try { await present(env('/weird'), ORDER); } catch (e) { threw = e.message; }
    check(threw && /KLIKQRIS_QR_FIELDS/.test(threw),
      'an unknown nesting is refused and names the env var', (threw || '').slice(0, 60));

    const pay = await present(env('/weird', { KLIKQRIS_QR_FIELDS: 'result.qris_content' }), ORDER);
    check(pay.qr && pay.qr.bits === expect.bits,
      'KLIKQRIS_QR_FIELDS makes it work with no code change');
  }

  // ---- 5. garbage, HTTP errors and non-JSON are refused -------------------
  {
    let threw = null;
    try { await present(env('/garbage'), ORDER); } catch (e) { threw = e.message; }
    check(threw && /no qr_string and no url/.test(threw), 'an empty shape is refused');

    threw = null;
    try { await present(env('/500'), ORDER); } catch (e) { threw = e.message; }
    check(threw && /gateway 500/.test(threw), 'HTTP 500 is reported with its status', (threw || '').slice(0, 40));

    threw = null;
    try { await present(env('/html'), ORDER); } catch (e) { threw = e.message; }
    check(threw && /gateway 200/.test(threw), 'a non-JSON body is reported, not parsed as {}');
  }

  // ---- 6. missing credentials is a note, not an exception ----------------
  {
    const pay = await present({ PAY_MODE: 'klikqris' }, ORDER);
    check(pay.configured === false && /KLIKQRIS_CREATE_URL/.test(pay.note),
      'no credentials -> configured:false with a readable note');
  }

  // ---- 7. extract() itself: precedence and path digging ------------------
  {
    const r = extract({}, { qrString: 'A', data: { qr_string: 'B' } });
    check(r.qr.value === 'A', 'the shallower field wins when both exist', r.qr.path);

    const r2 = extract({}, { data: { nested: { deep: 'x' } } });
    check(r2.qr === null && r2.url === null, 'deep unknown nesting yields null, not undefined');
  }

  // ---- 8. the credential name the existing project already uses ----------
  {
    const c = gatewayConfig({ KLIKQRIS_CREATE_URL: 'https://x', KLIKQRIS_API_KEY: 'abc' });
    check(c.ready && c.key === 'abc',
      'KLIKQRIS_API_KEY is accepted, so an existing secret works unchanged');
    const c2 = gatewayConfig({ KLIKQRIS_CREATE_URL: 'https://x', KLIKQRIS_KEY: 'a', KLIKQRIS_API_KEY: 'b' });
    check(c2.key === 'a', 'KLIKQRIS_KEY wins when both are set');

    seen = [];
    await present(env('/flat-qr', { KLIKQRIS_KEY: '', KLIKQRIS_API_KEY: 'probe-key' }), ORDER);
    check(seen.length === 1, 'a call goes out with the fallback credential');
  }

  // ---- 9. header and body are configurable -------------------------------
  {
    seen = [];
    await present(env('/flat-qr', { KLIKQRIS_AUTH_HEADER: 'Authorization', KLIKQRIS_AUTH_PREFIX: 'Bearer ' }), ORDER);
    check(seen[0] && seen[0].auth === 'Bearer probe-key',
      'Authorization: Bearer works when asked for', JSON.stringify(seen[0] && seen[0].auth));

    seen = [];
    await present(env('/flat-qr', { KLIKQRIS_MERCHANT_ID: 'M-123' }), ORDER);
    check(seen[0] && seen[0].body.merchant_id === 'M-123',
      'KLIKQRIS_MERCHANT_ID is sent when set', JSON.stringify(seen[0] && seen[0].body));

    seen = [];
    await present(env('/flat-qr', {
      KLIKQRIS_MERCHANT_ID: 'M-123',
      KLIKQRIS_BODY: '{"nominal":"{{amount}}","reff":"{{ref_id}}","merchant":"{{merchant_id}}"}',
    }), ORDER);
    check(seen[0] && seen[0].body.nominal === '140000' && seen[0].body.reff === ORDER.id
      && seen[0].body.merchant === 'M-123',
      'a custom body template is filled in', JSON.stringify(seen[0] && seen[0].body));

    check(requestBody({}, ORDER).amount === 140000, 'the default body still works');
    let threw = false;
    try { requestBody({ KLIKQRIS_BODY: '{oops}' }, ORDER); } catch { threw = true; }
    check(threw, 'a broken body template throws instead of sending half a request');
  }

  // ---- 10. the probe the seller will run --------------------------------
  {
    const { execFile } = await import('node:child_process');
    // Async on purpose: the fake gateway lives in this process, so blocking the
    // event loop with execFileSync would make the probe hang until the timeout.
    const run = (...argv) => new Promise((resolve) => {
      execFile(process.execPath, ['tools/pay_probe.mjs', ...argv],
        { encoding: 'utf8', timeout: 30000 },
        (err, stdout, stderr) => resolve({
          code: err ? (typeof err.code === 'number' ? err.code : 1) : 0,
          out: (stdout || '') + (stderr || ''),
        }));
    });

    const good = await run('--url', base(port, '/flat-qr'), '--key', 'probe-key', '--amount', '1000');
    check(good.code === 0 && /production would accept this response/.test(good.out),
      'pay_probe exits 0 on a shape production accepts', 'exit ' + good.code);
    check(/qr_string/.test(good.out), 'the probe names the field it picked');
    check(/auth header/.test(good.out), 'and prints the header it used');

    const meh = await run('--url', base(port, '/garbage'), '--key', 'probe-key');
    check(meh.code === 2, 'pay_probe exits 2 when nothing matches', 'exit ' + meh.code);
    check(/KLIKQRIS_QR_FIELDS/.test(meh.out), 'and tells you which variable to set');

    const boom = await run('--url', base(port, '/500'), '--key', 'probe-key');
    check(boom.code === 3, 'pay_probe exits 3 on an HTTP error', 'exit ' + boom.code);

    const custom = await run('--url', base(port, '/weird'), '--key', 'probe-key',
      '--qr', 'result.qris_content');
    check(custom.code === 0, 'the probe accepts the field list on the command line too',
      'exit ' + custom.code);
  }

  server.close();
  console.log();
  console.log('pay: ' + pass + ' ok, ' + failed + ' failed');
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.log('[FAIL] pay harness crashed: ' + (e && e.stack ? e.stack : e));
  process.exit(1);
});
