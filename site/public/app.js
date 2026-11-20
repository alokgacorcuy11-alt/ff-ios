// ===== public/app.js - the DEMIOS storefront =====
//
// plans -> order -> poll -> key. The QR is drawn on a canvas from packed bits
// rather than an <img> pointing at a data URI: ~800 bytes on the wire against
// 40-60 KB of SVG path data for the same symbol.

(() => {
  const $ = (id) => document.getElementById(id);
  const rp = (n) => 'Rp' + Number(n).toLocaleString('id-ID');
  const LS = 'demios_android_order';
  const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const MODULES = ['ESP CORE', 'AIMBOT', 'OVERLAY MENU', 'NO RECOIL / SPREAD'];

  // ---- ticker ------------------------------------------------------------
  function ticker() {
    const items = [
      'PLATFORM <b class="on">ANDROID</b>', 'ROOT <b class="on">TIDAK PERLU</b>',
      'BUILD <b>1.0</b>', 'MODUL <b class="on">127.0.0.1:7755</b>',
      'VIRTUAL SPACE <b class="on">TIDAK PERLU</b>', 'SHIZUKU <b class="on">TIDAK PERLU</b>',
      'ESP <b class="on">READY</b>', 'AIMBOT <b class="on">READY</b>',
      'HWID <b>OPSIONAL</b>',
    ];
    const one = items.map((t) => `<span>${t}</span>`).join('');
    const el = $('tick');
    if (el) el.innerHTML = one + one;
  }

  // ---- boot terminal -----------------------------------------------------
  function boot() {
    const el = $('boot');
    if (!el) return;
    const lines = [
      ['> demios.android v1.0', 'g'],
      ['[ok] target :: com.dts.freefireth', ''],
      ['[ok] arch :: arm64-v8a / android 8+', ''],
      ['[ok] modul :: 127.0.0.1:7755', ''],
      ['[ok] panel :: overlay, tanpa root', ''],
      ['[ok] simpan :: Android/data/com.dts.freefireth/files', ''],
      ['[ok] stealth :: tail acak per instalasi', ''],
      ['> menunggu lisensi \u2014 pilih paket \u2193', 'c'],
    ];
    const render = (n, done) => {
      const body = lines.slice(0, n)
        .map(([t, c]) => (c ? `<span class="${c}">${esc(t)}</span>` : esc(t)))
        .join('\n');
      const tail = done
        ? '\n<span class="g">> siap.</span>\n<span class="cursor"></span>'
        : '\n<span class="cursor"></span>';
      el.innerHTML = body + tail;
    };
    if (REDUCED) { render(lines.length, true); return; }
    let i = 0;
    const step = () => { i++; render(i, i >= lines.length); if (i < lines.length) setTimeout(step, 220); };
    render(0, false);
    step();
  }

  // ---- build identity ----------------------------------------------------
  // Fetched live from the build branch so the hash on the page is the hash of
  // the APK behind the button, not one that went stale after the next build.
  async function identity() {
    const el = $('apkHash');
    if (!el) return;
    try {
      const r = await fetch('https://raw.githubusercontent.com/alokgacorcuy11-alt/ff-ios/build/IDENTITY.txt', { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const text = await r.text();
      const pkg = (/^package:\s*(.+)$/m.exec(text) || [])[1];
      const sha = (/^apk:\s*([0-9a-f]{64})$/m.exec(text) || [])[1];
      const sdk = (/^sdk:\s*(.+)$/m.exec(text) || [])[1];
      el.innerHTML = sha
        ? `<b>sha256</b> ${esc(sha.slice(0, 16))}&hellip;${esc(sha.slice(-8))}`
          + (pkg ? ` &middot; <b>pkg</b> ${esc(pkg)}` : '')
          + (sdk ? ` &middot; <b>sdk</b> ${esc(sdk)}` : '')
        : '<b>sha256</b> lihat SHA256SUMS di cabang build';
    } catch {
      el.innerHTML = '<b>sha256</b> lihat <a href="https://github.com/alokgacorcuy11-alt/ff-ios/blob/build/SHA256SUMS" rel="noopener" style="color:var(--red)">SHA256SUMS</a>';
    }
  }

  // ---- plans -------------------------------------------------------------
  function card(p, idx, hot) {
    const el = document.createElement('article');
    el.className = 'pcard' + (hot ? ' hot' : '');

    const top = document.createElement('div');
    top.className = 'ctop';
    const ct = document.createElement('span');
    ct.className = 'ctier';
    ct.textContent = 'TIER ' + String(idx + 1).padStart(2, '0');
    const cb = document.createElement('span');
    cb.className = 'cbadge';
    cb.textContent = p.tag || (p.tier === 'pro' ? 'PRO' : 'STD');
    top.append(ct, cb);

    const name = document.createElement('h3');
    name.className = 'pname';
    name.textContent = String(p.label || p.id).toUpperCase();

    const price = document.createElement('div');
    price.className = 'price';
    const rpv = document.createElement('span');
    rpv.className = 'rp';
    rpv.textContent = rp(p.price);
    const sm = document.createElement('small');
    sm.textContent = 'IDR';
    price.append(rpv, sm);

    const dur = document.createElement('div');
    dur.className = 'dur';
    dur.textContent = p.days > 0 ? (p.days + ' HARI AKSES') : 'SELAMANYA';

    const ul = document.createElement('ul');
    ul.className = 'mods';
    for (const m of MODULES) {
      const li = document.createElement('li');
      li.innerHTML = '<span class="k">[+]</span>' + esc(m);
      ul.append(li);
    }

    const b = document.createElement('button');
    b.className = 'activate';
    b.type = 'button';
    b.textContent = 'BELI';
    b.addEventListener('click', () => buy(p.id, b));

    el.append(top, name, price, dur, ul, b);
    return el;
  }

  async function loadPlans() {
    const box = $('plans');
    try {
      const j = await api('/api/android/plans');
      box.setAttribute('aria-busy', 'false');
      box.textContent = '';
      const plans = j.plans || [];
      const hotId = (plans.find((p) => p.tag === 'PALING LAKU') || plans[plans.length - 1] || {}).id;
      plans.forEach((p, i) => box.append(card(p, i, p.id === hotId)));
      const net = $('netInfo');
      if (net) {
        net.textContent = !j.ready ? 'BAYAR VIA ADMIN'
          : (j.gateway === 'klikqris' ? 'QRIS AUTO' : 'QRIS');
      }
    } catch (e) {
      box.setAttribute('aria-busy', 'false');
      box.textContent = '// gagal memuat paket: ' + e.message;
    }
  }

  async function api(url, body) {
    const r = await fetch(url, body ? {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    } : undefined);
    const j = await r.json().catch(() => null);
    if (!r.ok) {
      const err = new Error((j && j.error) || ('HTTP ' + r.status));
      err.status = r.status;
      throw err;
    }
    return j;
  }

  // ---- order -------------------------------------------------------------
  let poll = null;
  let tickerTimer = null;
  let expiresAt = 0;
  let drawnQr = '';

  async function buy(plan, btn) {
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = 'MENYIAPKAN\u2026';
    try {
      const j = await api('/api/android/order', { plan });
      localStorage.setItem(LS, JSON.stringify({ orderId: j.orderId, token: j.token }));
      showOrder(j);
      $('order').scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'center' });
      startPoll();
    } catch (e) {
      alert('Gagal buat pesanan: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  }

  const LABEL = {
    pending: 'menunggu', paid: 'selesai', cancelled: 'dibatalkan', expired: 'kedaluwarsa',
  };

  function setStatus(st, label) {
    const el = $('oStatus');
    el.textContent = label || LABEL[st] || st;
    el.className = 'st ' + (st || 'pending');
  }

  function renderPay(o) {
    const pay = $('oPay');
    const sig = o.qr ? 'q:' + o.qr.bits.length + ':' + o.qr.bits.slice(0, 24) : (o.payUrl || '');
    if (sig && sig === drawnQr) return;
    drawnQr = sig;
    pay.textContent = '';

    if (o.qr && window.DemiosQR) {
      const c = document.createElement('canvas');
      c.className = 'qr';
      c.setAttribute('role', 'img');
      c.setAttribute('aria-label', 'QRIS pembayaran');
      window.DemiosQR.draw(c, o.qr.size, o.qr.bits);
      const cap = document.createElement('div');
      cap.className = 'qrcap';
      cap.textContent = 'SCAN QRIS \u2014 M-BANKING / E-WALLET';
      pay.append(c, cap);
    } else if (o.qr) {
      const w = document.createElement('div');
      w.className = 'qrwait';
      w.textContent = 'QR tidak bisa digambar di browser ini';
      pay.append(w);
    } else if (o.payUrl) {
      const a = document.createElement('a');
      a.href = o.payUrl;
      a.target = '_blank';
      a.rel = 'noopener';
      a.className = 'btn primary';
      a.textContent = 'BUKA HALAMAN BAYAR';
      pay.append(a);
    } else {
      const w = document.createElement('div');
      w.className = 'qrwait';
      w.textContent = o.note || 'Pembayaran belum dikonfigurasi.';
      pay.append(w);
    }
  }

  function showOrder(o) {
    $('order').classList.remove('hidden');
    $('oPlan').textContent = String(o.label || o.plan || '\u2014').toUpperCase();
    const price = o.price == null ? o.amount : o.price;
    $('oPrice').textContent = rp(price);
    $('oAmount').textContent = rp(o.amount || 0);
    $('oId').textContent = o.orderId || '\u2014';
    $('oKey').classList.add('hidden');
    $('again').classList.add('hidden');

    expiresAt = Number(o.expiresAt || 0);
    if (!expiresAt && o.ttlMinutes) expiresAt = Date.now() + o.ttlMinutes * 60000;
    drawTimer();

    renderPay(o);
    $('oHint').textContent = o.note || '';
    setStatus(o.status || 'pending');
    if (o.status === 'paid' && o.key) showKey(o.key);
  }

  function drawTimer() {
    const el = $('oTimer');
    if (!el) return;
    if (!expiresAt) { el.textContent = '\u2014'; return; }
    const left = Math.max(0, expiresAt - Date.now());
    if (left <= 0) {
      el.textContent = 'habis';
      if (!poll) $('again').classList.remove('hidden');
      return;
    }
    const m = Math.floor(left / 60000);
    const s = Math.floor((left % 60000) / 1000);
    el.textContent = m + ':' + String(s).padStart(2, '0');
  }

  function showKey(key) {
    $('oKey').classList.remove('hidden');
    $('oKeyVal').textContent = key;
    setStatus('paid', 'selesai');
    $('oHint').textContent = '// buka app FFMenu, tempel key ini, tekan ACTIVE.';
    $('oKey').scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'center' });
    if (poll) { clearInterval(poll); poll = null; }
    $('again').classList.remove('hidden');
  }

  async function check() {
    const saved = JSON.parse(localStorage.getItem(LS) || 'null');
    if (!saved) return;
    try {
      const o = await api('/api/android/order?id=' + encodeURIComponent(saved.orderId)
        + '&token=' + encodeURIComponent(saved.token));
      $('order').classList.remove('hidden');
      $('oId').textContent = saved.orderId;
      if (o.label) $('oPlan').textContent = String(o.label).toUpperCase();
      if (o.price != null) $('oPrice').textContent = rp(o.price);
      if (o.amount) $('oAmount').textContent = rp(o.amount);
      if (o.expiresAt) expiresAt = o.expiresAt;
      setStatus(o.status);
      if (o.status === 'paid' && o.key) { showKey(o.key); return; }
      if (o.status === 'pending') {
        renderPay(o);
        if (o.note) $('oHint').textContent = o.note;
      } else if (o.status === 'expired') {
        $('oHint').textContent = '// pesanan kedaluwarsa. buat pesanan baru.';
        $('again').classList.remove('hidden');
        drawnQr = '';
        $('oPay').textContent = '';
      }
    } catch (e) {
      if (e.status === 404) {
        localStorage.removeItem(LS);
        $('order').classList.add('hidden');
        if (poll) { clearInterval(poll); poll = null; }
      }
    }
  }

  function startPoll() {
    if (poll) clearInterval(poll);
    poll = setInterval(check, 4000);
    if (tickerTimer) clearInterval(tickerTimer);
    tickerTimer = setInterval(drawTimer, 1000);
  }

  // ---- wiring ------------------------------------------------------------
  $('check').addEventListener('click', check);

  $('again').addEventListener('click', () => {
    localStorage.removeItem(LS);
    if (poll) { clearInterval(poll); poll = null; }
    $('order').classList.add('hidden');
    drawnQr = '';
    $('oPay').textContent = '';
    $('plans').scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'start' });
  });

  $('copyKey').addEventListener('click', async () => {
    const t = $('oKeyVal').textContent;
    try {
      await navigator.clipboard.writeText(t);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = t;
      document.body.append(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    const b = $('copyKey');
    b.textContent = 'TERSALIN \u2713';
    setTimeout(() => { b.textContent = 'SALIN KEY'; }, 1600);
  });

  ticker();
  boot();
  identity();
  loadPlans();
  if (localStorage.getItem(LS)) check().then(startPoll);
})();
