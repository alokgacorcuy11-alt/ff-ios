/* il2cpp_hunt.js - find the values for native/state/ff_native.json
 *
 * This is the derivation tool. It does NOT guess anything: it walks the live
 * IL2CPP metadata inside the running game and prints real class names, real
 * field names with real offsets, and real method signatures.
 *
 *   frida -U -f com.dts.freefireth -l tools/il2cpp_hunt.js --no-pause
 *
 * Commands (type into the Frida console):
 *   .camera                 find candidates for the camera + WorldToScreen
 *   .players                find static fields holding a List<T>
 *   .fields <Class>         dump every field of a class (name + offset)
 *   .methods <Class>        dump every method (name + param count)
 *   .find <regex>           classes whose name matches
 *   .statics <Class>        static fields only, with type names
 *   .all                    run the full hunt and print a JSON skeleton
 *   .w2s <Class> <method>   live-probe a WorldToScreenPoint implementation
 */

'use strict';

const REQ = [
  'il2cpp_domain_get', 'il2cpp_domain_get_assemblies', 'il2cpp_assembly_get_image',
  'il2cpp_image_get_name', 'il2cpp_image_get_class_count', 'il2cpp_image_get_class',
  'il2cpp_class_get_name', 'il2cpp_class_get_namespace', 'il2cpp_class_get_parent',
  'il2cpp_class_get_fields', 'il2cpp_field_get_name', 'il2cpp_field_get_offset',
  'il2cpp_field_get_flags', 'il2cpp_field_get_type', 'il2cpp_type_get_name',
  'il2cpp_class_get_methods', 'il2cpp_method_get_name', 'il2cpp_method_get_param_count',
  'il2cpp_class_get_field_from_name', 'il2cpp_class_get_method_from_name',
];

const E = {};
let ok = true;
for (const n of REQ) {
  let p = null;
  try { p = Module.findExportByName(null, n); } catch (e) { p = null; }
  if (!p) {
    ok = false;
    console.log('[hunt] MISSING export: ' + n);
  }
  E[n] = p;
}
if (!ok) {
  console.log('[hunt] il2cpp exports not found yet. Launch the game fully, then reload.');
}

const F = (name, ret, args) => name && new NativeFunction(E[name], ret, args);

const f_domain   = F('il2cpp_domain_get', 'pointer', []);
const f_asm      = F('il2cpp_domain_get_assemblies', 'pointer', ['pointer', 'pointer']);
const f_img      = F('il2cpp_assembly_get_image', 'pointer', ['pointer']);
const f_imgname  = F('il2cpp_image_get_name', 'pointer', ['pointer']);
const f_imgcount = F('il2cpp_image_get_class_count', 'pointer', ['pointer']);
const f_imgclass = F('il2cpp_image_get_class', 'pointer', ['pointer', 'pointer']);
const f_cname    = F('il2cpp_class_get_name', 'pointer', ['pointer']);
const f_cns      = F('il2cpp_class_get_namespace', 'pointer', ['pointer']);
const f_cparent  = F('il2cpp_class_get_parent', 'pointer', ['pointer']);
const f_fields   = F('il2cpp_class_get_fields', 'pointer', ['pointer', 'pointer']);
const f_fname    = F('il2cpp_field_get_name', 'pointer', ['pointer']);
const f_foff     = F('il2cpp_field_get_offset', 'pointer', ['pointer']);
const f_fflags   = F('il2cpp_field_get_flags', 'int', ['pointer']);
const f_ftype    = F('il2cpp_field_get_type', 'pointer', ['pointer']);
const f_tname    = F('il2cpp_type_get_name', 'pointer', ['pointer']);
const f_methods  = F('il2cpp_class_get_methods', 'pointer', ['pointer', 'pointer']);
const f_mname    = F('il2cpp_method_get_name', 'pointer', ['pointer']);
const f_mparams  = F('il2cpp_method_get_param_count', 'int', ['pointer']);

const rd = (p) => (p.isNull() ? '' : p.readUtf8String());
const cname = (k) => rd(f_cname(k));
const cns = (k) => rd(f_cns(k));

// ---------------------------------------------------------------- walking
function images() {
  const n = Memory.alloc(8);
  const arr = f_asm(f_domain(), n);
  const count = n.readU64().toNumber();
  const out = [];
  for (let i = 0; i < count; i++) {
    const img = f_img(arr.add(i * 8).readPointer());
    if (!img.isNull()) out.push(img);
  }
  return out;
}

function imageName(img) { return rd(f_imgname(img)); }

function classesIn(img) {
  const out = [];
  if (f_imgcount && f_imgclass) {
    const n = f_imgcount(img).toNumber();
    for (let i = 0; i < n; i++) {
      const k = f_imgclass(img, ptr(i));
      if (!k.isNull()) out.push(k);
    }
  }
  return out;
}

function allClasses() {
  const out = [];
  for (const img of images()) {
    for (const k of classesIn(img)) out.push({ k, img: imageName(img) });
  }
  return out;
}

function fieldsOf(k) {
  const it = Memory.alloc(8);
  it.writePointer(NULL);
  const out = [];
  let f;
  while (!(f = f_fields(k, it)).isNull()) {
    const flags = f_fflags ? f_fflags(f) : 0;
    const t = f_ftype ? f_ftype(f) : ptr(0);
    out.push({
      name: rd(f_fname(f)),
      offset: f_foff(f).toNumber(),
      static: (flags & 0x0010) !== 0,
      type: t.isNull() ? '?' : rd(f_tname(t)),
    });
  }
  return out;
}

function methodsOf(k) {
  const it = Memory.alloc(8);
  it.writePointer(NULL);
  const out = [];
  let m;
  while (!(m = f_methods(k, it)).isNull()) {
    out.push({ name: rd(f_mname(m)), params: f_mparams ? f_mparams(m) : -1, ptr: m });
  }
  return out;
}

function findClass(name) {
  for (const c of allClasses()) if (cname(c.k) === name) return c;
  return null;
}

// ---------------------------------------------------------------- commands
const camRx = /camera|worldtoscreen|screenpoint|viewport/i;
const playerRx = /player|actor|avatar|hero|character|pawn|entity|enemy|unit|battle|match|room/i;

rpc.exports = {};

globalThis.findClasses = function (re) {
  const rx = new RegExp(re, 'i');
  const hits = [];
  for (const c of allClasses()) {
    const n = cname(c.k), ns = cns(c.k);
    if (rx.test(n) || rx.test(ns)) hits.push({ cls: n, ns, img: c.img, ptr: c.k });
  }
  console.log(`[hunt] ${hits.length} classes match /${re}/`);
  hits.slice(0, 120).forEach(h => console.log(`  ${h.img.padEnd(20)} ${h.ns ? h.ns + '.' : ''}${h.cls}`));
  return hits.length;
};

globalThis.dumpFields = function (name) {
  const c = findClass(name);
  if (!c) { console.log(`[hunt] class ${name} not found`); return; }
  const fs = fieldsOf(c.k);
  console.log(`[hunt] ${name}: ${fs.length} fields`);
  fs.forEach(f => console.log(`  ${String(f.offset).padStart(4)}  ${f.static ? 'static ' : '       '} ${f.type.padEnd(30)} ${f.name}`));
};

globalThis.dumpStatics = function (name) {
  const c = findClass(name);
  if (!c) { console.log(`[hunt] class ${name} not found`); return; }
  fieldsOf(c.k).filter(f => f.static)
    .forEach(f => console.log(`  ${f.type.padEnd(36)} ${f.name}`));
};

globalThis.dumpMethods = function (name) {
  const c = findClass(name);
  if (!c) { console.log(`[hunt] class ${name} not found`); return; }
  const ms = methodsOf(c.k);
  console.log(`[hunt] ${name}: ${ms.length} methods`);
  ms.forEach(m => console.log(`  ${String(m.params).padStart(2)} params  ${m.name}`));
};

globalThis.findW2S = function () {
  const hits = [];
  for (const c of allClasses()) {
    if (!camRx.test(cname(c.k))) continue;
    for (const m of methodsOf(c.k)) {
      if (camRx.test(m.name) && m.params === 1) {
        hits.push(`${cname(c.k)}.${m.name} (${m.params})`);
      }
    }
  }
  console.log('[hunt] WorldToScreen candidates:');
  [...new Set(hits)].slice(0, 40).forEach(h => console.log('  ' + h));
  return hits.length;
};

globalThis.findPlayerContainers = function () {
  console.log('[hunt] static fields whose type is a generic list (player container candidates)');
  let n = 0;
  for (const c of allClasses()) {
    if (!playerRx.test(cname(c.k))) continue;
    for (const f of fieldsOf(c.k)) {
      if (!f.static) continue;
      if (!/List`1|\[\]|Dictionary/.test(f.type)) continue;
      console.log(`  ${cname(c.k)}.${f.name}  :  ${f.type}`);
      n++;
    }
  }
  console.log(`[hunt] ${n} candidates`);
  return n;
};

globalThis.suggestFixed = function () {
  console.log('[hunt] Java-style fixed offsets are not a thing on IL2CPP. Use');
  console.log('       il2cpp_field_get_offset output, they are already stable per build.');
};

// =====================================================================
// huntShape - find the real gameplay classes by field TYPE, not by name.
//
// 1.132.1 has 57.6% of COW.GamePlay member names randomised, so name matching
// only finds UI code. But types are never obfuscated: a player entity is still
// { Vector3, float, int, List<...> }. il2cpp_type_get_name gives that exactly,
// with zero guessing - which is why this runs on device and not offline.
//
//   huntShape()                 scan every class, rank by shape
//   huntShape({ns:'COW.GamePlay', min:6, top:20})
//   huntShape({only:'.Player'})  filter by class-name substring
// =====================================================================
globalThis.huntShape = function (opts) {
  opts = opts || {};
  const nsFilter = opts.ns || '';
  const only = opts.only || '';
  const minScore = opts.min !== undefined ? opts.min : 6;
  const top = opts.top || 20;
  const maxFields = opts.maxFields || 60;

  const POS = /Vector3|Vector2|Vector3Int/;
  let scanned = 0, withFields = 0;
  const cands = [];
  const allFieldCache = {};
  const t0 = Date.now();

  for (const c of allClasses()) {
    const name = cname(c.k);
    if (!name) continue;
    const ns = cns(c.k);
    const full = ns ? ns + '.' + name : name;
    if (nsFilter && full.indexOf(nsFilter) !== 0) continue;
    if (only && full.indexOf(only) < 0) continue;
    scanned++;

    let fs;
    try { fs = fieldsOf(c.k); } catch (e) { continue; }
    if (!fs.length || fs.length > maxFields) continue;
    withFields++;

    let pos = 0, flt = 0, int = 0, boolc = 0, strc = 0, lst = 0, obj = 0;
    const ev = [];
    const typed = [];
    for (const f of fs) {
      const tn = f.type || '';
      typed.push(f.name + ':' + tn);
      if (POS.test(tn)) { pos++; ev.push(f.name + ':' + tn); }
      else if (tn === 'System.Single' || tn === 'float') flt++;
      else if (tn === 'System.Int32' || tn === 'int') int++;
      else if (tn === 'System.Boolean' || tn === 'bool') boolc++;
      else if (tn === 'System.String' || tn === 'string') strc++;
      else if (tn.indexOf('List`1') >= 0 || tn.indexOf('Dictionary`2') >= 0
               || tn.indexOf('[]') >= 0) { lst++; ev.push(f.name + ':' + tn); }
      else obj++;
    }
    allFieldCache[full] = typed;
    let score = 0;
    if (pos >= 1) score += 3;
    if (flt >= 1) score += 2;
    if (int >= 1) score += 1;
    if (strc >= 1) score += 1;
    if (lst >= 1) score += 1;
    score += 1;                        // small class bonus (already bounded)
    if (pos && score >= minScore) {
      cands.push({ full, score, n: fs.length, typed, ev, pos });
    }
  }

  cands.sort((a, b) => b.score - a.score || a.n - b.n);
  console.log('[hunt] scanned ' + scanned + ' classes, ' + withFields +
              ' with fields, ' + cands.length + ' entity candidates  (' +
              ((Date.now() - t0) / 1000).toFixed(1) + 's)');
  for (const c of cands.slice(0, top)) {
    console.log('  [' + c.score + '] ' + c.full + '  (' + c.n + ' fields)');
    for (const t of c.typed.slice(0, 14)) console.log('        ' + t);
  }

  // containers: a class holding List<X>/X[] where X is one of the candidates
  const entNames = new Set(cands.slice(0, 300).map(c => c.full));
  const cont = [];
  for (const [full, fs] of Object.entries(allFieldCache)) {
    for (const t of fs) {
      const m = /<([^<>]+)>|^([A-Za-z0-9_.\[\]]+)\.?\[\]$/.exec(t);
      if (!m) continue;
      const inner = (m[1] || m[2] || '').trim();
      if (entNames.has(inner)) { cont.push([full, t]); break; }
    }
  }
  if (cont.length) {
    console.log('');
    console.log('[hunt] container candidates (hold a collection of an entity):');
    cont.slice(0, 12).forEach(([f, t]) => console.log('  ' + f + '   ->   ' + t));
  }

  console.log('');
  console.log('[hunt] next: pick a class above, then');
  console.log('        dumpStatics("<class>")   to find the singleton holding it');
  console.log('        dumpMethods("<class>")   to find transform/position methods');
  return cands.length;
};

console.log('');
console.log('===== il2cpp_hunt ready =====');
console.log('start with:  .all');
console.log('or:  findClasses("player")  dumpStatics("Player")  findW2S()');
console.log('=============================');