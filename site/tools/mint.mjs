// ===== tools/mint.mjs - mint or check a key from the command line =====
//
//   node tools/mint.mjs mint   --secret S --days 30 --tier 1 [--nonce N] [--device ID] [--now MS]
//   node tools/mint.mjs verify --secret S --key K [--device ID] [--now MS]
//
// Prints one JSON object. site/tests/test_site_key.py drives this, and the
// seller can use it to reissue a key by hand when an order goes sideways.
//
// The nonce is what makes each key a different string. Without --nonce one is
// generated from the CSPRNG, because two keys minted for the same plan on the
// same day are otherwise byte-identical.
import { mint, verify, deviceHash, dayNumber, fingerprint, freshNonce } from '../functions/_lib/key.js';

function args(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const k = a.slice(2);
      const v = argv[i + 1] !== undefined && !argv[i + 1].startsWith('--') ? argv[++i] : 'true';
      out[k] = v;
    } else {
      out._.push(a);
    }
  }
  return out;
}

const a = args(process.argv.slice(2));
const cmd = a._[0];
const secret = a.secret;
const now = a.now ? Number(a.now) : Date.now();

if (!secret) {
  console.error('--secret is required');
  process.exit(2);
}

try {
  if (cmd === 'mint') {
    const nonce = a.nonce === undefined ? freshNonce() : Number(a.nonce);
    const key = await mint({
      secret,
      days: a.days === undefined ? 30 : Number(a.days),
      tier: a.tier === undefined ? 1 : Number(a.tier),
      deviceId: a.device || '',
      nonce,
      now,
    });
    process.stdout.write(JSON.stringify({
      ok: true,
      key,
      nonce,
      fingerprint: fingerprint(key),
      day: dayNumber(now),
      deviceHash: await deviceHash(secret, a.device || ''),
    }));
  } else if (cmd === 'verify') {
    const r = await verify(a.key, { secret, deviceId: a.device || '', now });
    process.stdout.write(JSON.stringify({ ...r, day: dayNumber(now) }));
  } else {
    console.error('usage: mint.mjs <mint|verify> --secret S ...');
    process.exit(2);
  }
} catch (e) {
  process.stdout.write(JSON.stringify({ ok: false, error: String(e && e.message || e) }));
  process.exit(1);
}
