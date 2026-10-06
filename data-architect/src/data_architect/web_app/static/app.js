/* SSDT Atlas: aplikacja webowa (bez zależności). Dane z lokalnego API serwera wtyczki. */
'use strict';

/* ================= Narzędzia ================= */
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const trunc = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
function plural(n, a, b, c) { if (n === 1) return a; const m = n % 10, h = n % 100; return m >= 2 && m <= 4 && (h < 12 || h > 14) ? b : c; }
const objs = (n) => `${n} ${plural(n, 'obiekt', 'obiekty', 'obiektów')}`;
const tabsTxt = (n) => `${n} ${plural(n, 'tabela', 'tabele', 'tabel')}`;
const lgcTxt = (n) => `${n} ${plural(n, 'procedura/widok', 'procedury/widoki', 'procedur/widoków')}`;
const MONTHS = ['sty', 'lut', 'mar', 'kwi', 'maj', 'cze', 'lip', 'sie', 'wrz', 'paź', 'lis', 'gru'];
const fmtDate = (iso) => { if (!iso) return '—'; const d = new Date(iso); return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`; };
function toast(t) { const el = $('#toast'); el.textContent = t; el.classList.add('on'); clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove('on'), 3200); }

async function api(path, body) {
  const opts = body === undefined ? undefined : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Atlas': '1' }, body: JSON.stringify(body) };
  const r = await fetch('api/' + path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

const TYPE = {
  T: { n: 'tabela', c: 'var(--t-table)' }, V: { n: 'widok', c: 'var(--t-view)' }, P: { n: 'procedura', c: 'var(--t-proc)' },
  F: { n: 'funkcja', c: 'var(--t-func)' }, Tr: { n: 'trigger', c: 'var(--t-trig)' }, Sy: { n: 'synonim', c: 'var(--t-trig)' },
  Sq: { n: 'sekwencja', c: 'var(--t-trig)' }, TT: { n: 'typ tabelaryczny', c: 'var(--t-trig)' }, '?': { n: 'obiekt', c: 'var(--t-trig)' },
};
const EDGE = { fk: 'klucz obcy', reads: 'odczyt', writes: 'zapis', calls: 'wywołanie', on: 'trigger', ref: 'odwołanie' };
const ECOL = { fk: 'var(--muted)', reads: 'var(--t-table)', writes: 'var(--orange)', calls: 'var(--t-proc)', on: 'var(--muted)', ref: 'var(--muted)' };
const FAMILY = [['T', 'Tabele'], ['V', 'Widoki'], ['P', 'Procedury'], ['F', 'Funkcje'], ['Tr', 'Triggery'], ['Sy', 'Synonimy'], ['Sq', 'Sekwencje'], ['TT', 'Typy'], ['ext', 'Systemy zewnętrzne']];
const famOrder = FAMILY.map(f => f[0]);
const badge = (t) => `<span class="badge" style="background:${(TYPE[t] || TYPE['?']).c}">${esc(t)}</span>`;
const IC = {
  map: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><rect x="3" y="3" width="7" height="6" rx="1.5"/><rect x="14" y="3" width="7" height="6" rx="1.5"/><rect x="8.5" y="15" width="7" height="6" rx="1.5"/><path d="M6.5 9v2.5h11V9M12 11.5V15"/></svg>',
  changes: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/><path d="M6 8.5v7M8.3 7.2l7.4 3.6"/></svg>',
  issues: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M12 3.5 21 19.5H3z" stroke-linejoin="round"/><path d="M12 10v4.5M12 17v.3"/></svg>',
  guides: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M5 4h10l4 4v12H5z" stroke-linejoin="round"/><path d="M9 12h6M9 16h6M9 8h3"/></svg>',
};

/* ================= Stan ================= */
const S = { view: 'map', sel: null, hover: null, editing: null, q: '', openChange: null, utab: 'open', uform: null, openIssue: null };
let ST = null, M = null, REV = -1;
const CACHE = { obj: new Map(), changes: null, issues: null, guides: null };

function prepModel(m) {
  if (!m.initialized) return m;
  m.byId = new Map(m.objects.map(o => [o.id, o]));
  m.domById = new Map(m.domains.map(d => [d.id, d]));
  m.extById = new Map(m.ext.map(x => [x.id, x]));
  m.projByName = new Map(m.projects.map(p => [p.name, p]));
  m.deg = new Map();
  for (const e of m.edges) { m.deg.set(e.src, (m.deg.get(e.src) || 0) + 1); m.deg.set(e.dst, (m.deg.get(e.dst) || 0) + 1); }
  m.domObjs = new Map();
  for (const o of m.objects) { if (!m.domObjs.has(o.domain)) m.domObjs.set(o.domain, []); m.domObjs.get(o.domain).push(o); }
  for (const list of m.domObjs.values()) list.sort((a, b) => (m.deg.get(b.id) || 0) - (m.deg.get(a.id) || 0) || a.name.localeCompare(b.name));
  for (const d of m.domains) d.colorVar = `var(--d${d.color || 1})`;
  return m;
}
const domColor = (id) => M.domById.get(id)?.colorVar || 'var(--faint)';
const domName = (id) => M.domById.get(id)?.name || id;
const objName = (o) => `${o.schema}.${o.name}`;
const famOf = (id) => (M.byId.get(id)?.t) || 'ext';
function nodeName(id) {
  if (id.startsWith('proj|')) return id.slice(5);
  if (id.startsWith('ext|')) return M.extById.get(id)?.name || id.slice(4);
  if (M.byId.has(id)) return objName(M.byId.get(id));
  return domName(id);
}
function kindOf(id) {
  if (id.startsWith('proj|')) return 'project';
  if (id.startsWith('ext|')) return 'ext';
  if (M.byId.has(id)) return 'obj';
  if (M.domById.has(id)) return 'dom';
  return null;
}

/* ================= Motyw ================= */
function setTheme(t, save) {
  if (t === 'system') document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme', t);
  document.querySelectorAll('#themeSeg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.t === t)));
  if (save) { try { localStorage.setItem('architect-theme', t); } catch (e) { /* brak dostępu */ } }
}
let th = 'system'; try { th = localStorage.getItem('architect-theme') || 'system'; } catch (e) { /* brak dostępu */ }
setTheme(th, false);
$('#themeSeg').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setTheme(b.dataset.t, true); });

/* ================= Nawigacja i ramka ================= */
function renderNav() {
  const ch = ST?.changeset?.changed || 0;
  const items = [['map', 'Mapa', IC.map, ''], ['changes', 'Zmiany', IC.changes, ch ? `<span class="count">${ch}</span>` : ''],
    ['issues', 'Do wyjaśnienia', IC.issues, ST?.openIssues ? `<span class="count warn">${ST.openIssues}</span>` : ''], ['guides', 'Wskazówki', IC.guides, '']];
  $('#nav').innerHTML = items.map(([id, l, ic, c]) => `<button data-v="${id}" ${S.view === id ? 'aria-current="page"' : ''}>${ic}<span>${l}</span>${c}</button>`).join('');
  $('#projName').textContent = ST?.project || '';
  const s = ST?.snapshot;
  $('#lastBox').innerHTML = s ? `<span>Ostatnia analiza: <b>${fmtDate(s.createdAt)}</b></span>
    <span>Nowe commity: ${ST.newCommits ? `<span class="new">${ST.newCommits}</span>` : '<b>0</b>'}</span>
    ${ST.newCommits ? '<span class="muted small">Odświeżenie: <span class="mono-inline">/ssdt-atlas:refresh</span> w Claude Code</span>' : ''}` : '<span>Brak analizy</span>';
}

function titlebar() {
  if (S.view === 'map' && M?.initialized) {
    const t = M.objects.filter(o => o.t === 'T').length;
    return `<h2>Mapa</h2><span class="sub">${M.projects.length} ${plural(M.projects.length, 'baza', 'bazy', 'baz')} · <span style="color:var(--t-table);font-weight:600">${tabsTxt(t)}</span> · <span style="color:var(--t-proc);font-weight:600">${lgcTxt(M.objects.length - t)}</span></span>`;
  }
  return `<h2>${{ map: 'Mapa', changes: 'Zmiany od ostatniej analizy', issues: 'Do wyjaśnienia', guides: 'Wskazówki' }[S.view]}</h2>`;
}

/* ================= Ładowanie danych ================= */
async function loadAll() {
  ST = await api('state');
  REV = ST.rev;
  M = prepModel(await api('model'));
  CACHE.obj.clear(); CACHE.changes = null; CACHE.issues = null; CACHE.guides = null;
}

async function poll() {
  try {
    const v = await api('version');
    if (v.rev !== REV && !S.editing && !S.uform && !document.querySelector('textarea:focus,input:focus')) {
      const keepVB = VB ? { ...VB } : null;
      await loadAll();
      L.bounds = null;
      await render();
      if (keepVB && S.view === 'map' && $('#fsvg')) { VB = keepVB; apply(); }
    }
    if (poll.failed) { poll.failed = 0; toast('Połączenie z serwerem przywrócone.'); }
  } catch (e) {
    poll.failed = (poll.failed || 0) + 1;
    if (poll.failed === 2) toast('Brak połączenia z serwerem SSDT Atlas. Czy sesja Claude Code została zamknięta?');
  }
}

/* ================= Router ================= */
function readHash() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (!h) return;
  const [view, ...rest] = h.split('/');
  if (['map', 'changes', 'issues', 'guides'].includes(view)) S.view = view;
  const id = rest.join('/');
  if (id && S.view === 'map') S.pendingSel = id;
}
function writeHash() {
  const h = S.view + (S.view === 'map' && S.sel ? '/' + S.sel.id : '');
  if (location.hash.slice(1) !== h) history.replaceState(null, '', '#' + h);
}

/* ================= Render ================= */
async function render() {
  renderNav();
  $('#titlebar').innerHTML = titlebar();
  const v = $('#view');
  if (!M?.initialized && S.view !== 'guides') { v.innerHTML = emptyState(); return; }
  if (S.view === 'map') {
    if (S.pendingSel) { const id = S.pendingSel; S.pendingSel = null; const k = kindOf(id); if (k) S.sel = { id, kind: k }; }
    v.innerHTML = mapView();
    initMap();
    if (S.sel) loadPanel();
  } else {
    // ponowne rysowanie tej samej zakładki nie przewija strony na górę
    const keep = render.lastView === S.view ? v.scrollTop : 0;
    v.innerHTML = S.view === 'changes' ? await changesView() : S.view === 'issues' ? await issuesView() : await guidesView();
    v.scrollTop = keep;
  }
  render.lastView = S.view;
  writeHash();
}

function emptyState() {
  return `<div class="empty-state"><h2>Projekt nie został jeszcze przeanalizowany</h2>
    <p>W Claude Code, w katalogu tego projektu, uruchom <code>/ssdt-atlas:init</code>. Claude zada kilka pytań o projekt, a silnik Microsoft DacFx przeanalizuje wszystkie bazy.</p>
    <p class="muted small">Katalog: <span class="mono-inline">${esc(ST?.projectDir || '')}</span></p></div>`;
}

/* ================= Geometria ================= */
const anchor = (n, s) => s === 'l' ? [n.x - n.w / 2, n.y] : s === 'r' ? [n.x + n.w / 2, n.y] : s === 't' ? [n.x, n.y - n.h / 2] : [n.x, n.y + n.h / 2];
const bez = (p0, c1, c2, p1) => `M${p0[0].toFixed(1)},${p0[1].toFixed(1)} C${c1[0].toFixed(1)},${c1[1].toFixed(1)} ${c2[0].toFixed(1)},${c2[1].toFixed(1)} ${p1[0].toFixed(1)},${p1[1].toFixed(1)}`;
function boxEdge(a, b) {
  const dx = b.x - a.x, dy = b.y - a.y, hg = Math.abs(dx) - (a.w + b.w) / 2, vg = Math.abs(dy) - (a.h + b.h) / 2; let p0, p1, c1, c2;
  if (hg >= vg) { p0 = anchor(a, dx > 0 ? 'r' : 'l'); p1 = anchor(b, dx > 0 ? 'l' : 'r'); const k = (p1[0] - p0[0]) * 0.5; c1 = [p0[0] + k, p0[1]]; c2 = [p1[0] - k, p1[1]]; }
  else { p0 = anchor(a, dy > 0 ? 'b' : 't'); p1 = anchor(b, dy > 0 ? 't' : 'b'); const k = (p1[1] - p0[1]) * 0.5; c1 = [p0[0], p0[1] + k]; c2 = [p1[0], p1[1] - k]; }
  return bez(p0, c1, c2, p1);
}
function objEdge(a, b) {
  if (Math.abs(a.x - b.x) < 5) { const p0 = anchor(a, 'r'), p1 = anchor(b, 'r'), k = Math.min(70, 22 + Math.abs(b.y - a.y) * 0.3); return bez(p0, [p0[0] + k, p0[1]], [p1[0] + k, p1[1]], p1); }
  const s = b.x > a.x ? 1 : -1, p0 = anchor(a, s > 0 ? 'r' : 'l'), p1 = anchor(b, s > 0 ? 'l' : 'r'), k = Math.max(30, Math.abs(p1[0] - p0[0]) * 0.45) * s;
  return bez(p0, [p0[0] + k, p0[1]], [p1[0] - k, p1[1]], p1);
}
const markers = () => '<defs>' + Object.keys(EDGE).map(k => `<marker id="m-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path class="m-${k}" d="M0,1 L10,5 L0,9 z"/></marker>`).join('') + '</defs>';
const ctr = (n) => ({ x: n.x + n.w / 2, y: n.y + n.h / 2, w: n.w, h: n.h });

/* ================= Mapa: tabele i procedury w ramkach domen ================= */
const G = { CH: 26, CG: 6, CW: 172, DP: 12, CG2: 10, BAND: 40, LBL: 24, MAXROWS: 40 };
G.DW = G.DP * 2 + G.CW * 2 + G.CG2; G.DH = G.BAND + G.LBL;
const L = { nodes: {}, bounds: null, fitW: 1, dragged: false, fl: null };
let VB = null;

function splitDomain(domId) {
  const list = M.domObjs.get(domId) || [];
  return { T: list.filter(o => o.t === 'T'), Lg: list.filter(o => o.t !== 'T') };
}
const SH = 22; // wysokość nagłówka schematu w kolumnie domeny
/** Schematy domeny, od najliczniejszego. */
function schemasOf(domId) {
  const cnt = new Map();
  for (const o of M.domObjs.get(domId) || []) cnt.set(o.schema, (cnt.get(o.schema) || 0) + 1);
  return [...cnt.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(x => x[0]);
}
/** Kolumna domeny: obiekty pogrupowane schematami, z nagłówkiem schematu, gdy domena ma ich kilka. */
function columnRows(list, schemas) {
  const rows = []; let shown = 0;
  for (const sc of schemas) {
    const items = list.filter(o => o.schema === sc);
    if (!items.length || shown >= G.MAXROWS) continue;
    if (schemas.length > 1) rows.push({ head: sc });
    for (const o of items) { if (shown >= G.MAXROWS) break; rows.push({ o }); shown++; }
  }
  return rows;
}
const rowsHeight = (rows) => rows.reduce((a, r) => a + (r.head ? SH : G.CH + G.CG), 0);
function domainColumns(domId) {
  const { T, Lg } = splitDomain(domId), schemas = schemasOf(domId);
  return { schemas, tRows: columnRows(T, schemas), lRows: columnRows(Lg, schemas) };
}

function layout() {
  if (L.bounds) return;
  L.nodes = {}; L.heads = [];
  const BP = 18, BH = 70, GAP = 16, BG = 140, RG = 160;
  const domH = (d) => { const { tRows, lRows } = domainColumns(d.id); return G.DH + Math.max(rowsHeight(tRows), rowsHeight(lRows), G.CH + G.CG) + 26; };
  // rozmiary baz, potem podział na wiersze najlepiej pasujący do proporcji ekranu
  const shapes = M.projects.map(p => {
    const doms = M.domains.filter(d => d.project === p.name), n = doms.length, cols = n <= 2 ? 1 : n <= 6 ? 2 : n <= 12 ? 3 : 4;
    const colH = Array(cols).fill(0), place = [];
    for (const d of doms) { const h = domH(d), c = colH.indexOf(Math.min(...colH)); place.push([d.id, c, colH[c], h]); colH[c] += h + GAP; }
    return { p, place, w: BP * 2 + cols * G.DW + (cols - 1) * GAP, h: BH + Math.max(...colH, 200) - GAP + BP };
  });
  const svg = document.getElementById('fsvg'), rr = svg ? svg.getBoundingClientRect() : { width: 1200, height: 700 };
  const target = Math.max(0.6, rr.width / Math.max(300, rr.height));
  const totalW = shapes.reduce((a, s) => a + s.w + BG, 0) - BG;
  let best = null;
  for (let r = 1; r <= Math.max(1, shapes.length); r++) {
    const rows = []; let cur = [], w = 0; const lim = totalW / r;
    shapes.forEach((s, i) => { if (cur.length && w + s.w > lim * 1.15 && rows.length < r - 1) { rows.push(cur); cur = []; w = 0; } cur.push(i); w += s.w + BG; });
    rows.push(cur);
    const W = Math.max(...rows.map(row => row.reduce((a, i) => a + shapes[i].w + BG, 0) - BG));
    const H = rows.reduce((a, row) => a + Math.max(...row.map(i => shapes[i].h)), 0) + (rows.length - 1) * RG;
    const score = Math.abs(Math.log((W / H) / target));
    if (!best || score < best.score - 0.05) best = { rows, score };
  }
  let y = 0, maxRight = 0;
  for (const row of best.rows) {
    let x = 0;
    for (const i of row) {
      const { p, place, w, h } = shapes[i];
      L.nodes['proj|' + p.name] = { kind: 'project', id: 'proj|' + p.name, project: p.name, x, y, w, h };
      for (const [id, c, fy, dh] of place) {
        const dx = x + BP + c * (G.DW + GAP), dy = y + BH + fy;
        L.nodes[id] = { kind: 'dom', id, project: p.name, x: dx, y: dy, w: G.DW, h: dh };
        const { tRows, lRows } = domainColumns(id);
        [[tRows, dx + G.DP], [lRows, dx + G.DP + G.CW + G.CG2]].forEach(([rows, cx]) => {
          let ry = dy + G.DH;
          for (const r of rows) {
            if (r.head) { L.heads.push({ dom: id, x: cx, y: ry, schema: r.head }); ry += SH; continue; }
            L.nodes[r.o.id] = { kind: 'obj', id: r.o.id, dom: id, x: cx, y: ry, w: G.CW, h: G.CH }; ry += G.CH + G.CG;
          }
        });
      }
      x += w + BG; maxRight = Math.max(maxRight, x - BG);
    }
    y += Math.max(...row.map(i => shapes[i].h)) + RG;
  }
  // systemy zewnętrzne pod bazami, które z nich korzystają
  const ey = y - RG + 120, EW = 260, EH = 84;
  const wants = M.ext.map(xs => {
    const users = M.edges.filter(e => e.dst === xs.id).map(e => M.byId.get(e.src)?.project).filter(Boolean);
    const cx = users.length ? users.reduce((a, pn) => a + L.nodes['proj|' + pn].x + L.nodes['proj|' + pn].w / 2, 0) / users.length : maxRight / 2;
    return { xs, cx };
  }).sort((a, b) => a.cx - b.cx);
  let lastRight = -Infinity;
  for (const w of wants) { const left = Math.max(w.cx - EW / 2, lastRight + 30); L.nodes[w.xs.id] = { kind: 'ext', id: w.xs.id, x: left, y: ey, w: EW, h: EH }; lastRight = left + EW; }
  L.bounds = { x: -40, y: -40, w: Math.max(maxRight, lastRight) + 80, h: (M.ext.length ? ey + EH : y - RG) + 80 };
}

/** Zaznaczona domena lub baza: jej obiekty i ich sąsiedzi zostają wyraźni. */
function selContext() {
  if (!S.sel || S.sel.kind === 'obj' || S.sel.kind === 'ext') return null;
  const su = S.sel.id;
  const inside = (id) => { const o = M.byId.get(id); return !!o && (o.domain === su || 'proj|' + o.project === su); };
  const rel = new Set();
  for (const o of M.objects) if (inside(o.id)) rel.add(o.id);
  for (const e of M.edges) { if (inside(e.src)) rel.add(e.dst); if (inside(e.dst)) rel.add(e.src); }
  return { inside, rel };
}

function drawMap() {
  const back = $('#fback'); if (!back) return;
  const N = L.nodes, q = S.q.trim().toLowerCase(), ctx = selContext();
  const hit = (id) => q && nodeName(id).toLowerCase().includes(q);
  const cls = (n, extra) => {
    let c = 'node' + (extra ? ' ' + extra : '');
    if (S.sel && S.sel.id === n.id) c += ' sel';
    if (n.kind === 'obj' || n.kind === 'ext') { if (q ? !hit(n.id) : ctx && !ctx.rel.has(n.id)) c += ' dim'; if (hit(n.id)) c += ' hit'; }
    return c;
  };
  let b = '', f = '';
  for (const p of M.projects) {
    const n = N['proj|' + p.name];
    b += `<g class="${cls(n)}" data-id="${esc(n.id)}"><rect class="frame" x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="22"/>
      <text class="tx" x="${n.x + 22}" y="${n.y + 46}" font-size="30" font-weight="700" letter-spacing="-.5">${esc(p.name)}</text>
      <text class="txm" x="${n.x + n.w - 22}" y="${n.y + 44}" font-size="15" text-anchor="end">${tabsTxt(p.tables)} · ${lgcTxt(p.count - p.tables)}</text></g>`;
  }
  for (const d of M.domains) {
    const n = N[d.id]; if (!n) continue;
    const { T, Lg } = splitDomain(d.id), schemas = schemasOf(d.id), c = d.colorVar, c1 = n.x + G.DP, c2 = n.x + G.DP + G.CW + G.CG2;
    const hidden = Math.max(0, T.length - G.MAXROWS) + Math.max(0, Lg.length - G.MAXROWS);
    b += `<g class="${cls(n)}" data-id="${esc(d.id)}"><rect class="card" x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="12"/>
      <path d="M${n.x + 12},${n.y} H${n.x + n.w - 12} Q${n.x + n.w},${n.y} ${n.x + n.w},${n.y + 12} V${n.y + G.BAND} H${n.x} V${n.y + 12} Q${n.x},${n.y} ${n.x + 12},${n.y} Z" style="fill:color-mix(in srgb,${c} 16%,var(--surface))"/>
      <circle cx="${n.x + 20}" cy="${n.y + G.BAND / 2}" r="6" style="fill:${c}"/>
      <text class="tx" x="${n.x + 34}" y="${n.y + G.BAND / 2 + 5}" font-size="15" font-weight="650">${esc(trunc(d.name, 30))}</text>
      <text class="txm" x="${n.x + n.w - 14}" y="${n.y + G.BAND / 2 + 5}" font-size="12" text-anchor="end">${schemas.length === 1 ? `schemat ${esc(schemas[0])} · ` : ""}${objs(d.count)}</text>
      <text class="txm" x="${c1 + 2}" y="${n.y + G.BAND + 16}" font-size="10.5" font-weight="600" letter-spacing=".4">TABELE · ${d.tables}</text>
      <text class="txm" x="${c2 + 2}" y="${n.y + G.BAND + 16}" font-size="10.5" font-weight="600" letter-spacing=".4">PROCEDURY I WIDOKI · ${d.count - d.tables}</text>
      ${hidden ? `<text class="txm" x="${c1 + 2}" y="${n.y + n.h - 9}" font-size="10.5">+ ${objs(hidden)} (pełna lista po kliknięciu domeny)</text>` : ''}
      ${L.heads.filter(h => h.dom === d.id).map(h => `<text class="schema-h" x="${h.x + 2}" y="${h.y + 14}" font-size="11" font-weight="600">${esc(h.schema)}</text><line class="schema-l" x1="${h.x + 8 + h.schema.length * 6.6}" x2="${h.x + G.CW}" y1="${h.y + 10}" y2="${h.y + 10}"/>`).join('')}</g>`;
  }
  for (const n of Object.values(N)) {
    if (n.kind !== 'obj') continue;
    const o = M.byId.get(n.id), bw = o.t.length > 1 ? 21 : 17;
    f += `<g class="${cls(n)}" data-id="${esc(n.id)}"><rect class="chip ${o.t === 'T' ? 'c-t' : 'c-l'}" x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="6"/>
      <rect x="${n.x + 5}" y="${n.y + 5}" width="${bw}" height="16" rx="4" style="fill:${(TYPE[o.t] || TYPE['?']).c}"/><text x="${n.x + 5 + bw / 2}" y="${n.y + 16.5}" font-size="9" font-weight="700" fill="#fff" text-anchor="middle">${esc(o.t)}</text>
      <text class="tx" x="${n.x + bw + 11}" y="${n.y + 17.5}" font-size="11.5">${esc(trunc(o.name, 24))}</text>
      ${o.warn ? `<circle cx="${n.x + n.w - 9}" cy="${n.y + 13}" r="4" style="fill:var(--orange)"/>` : ''}<title>${esc(objName(o))}</title></g>`;
  }
  for (const x of M.ext) {
    const n = N[x.id]; if (!n) continue;
    f += `<g class="${cls(n, 'ext')}" data-id="${esc(x.id)}"><rect class="card" x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="14"/>
      <text class="tx" x="${n.x + 20}" y="${n.y + 36}" font-size="22" font-weight="650">${esc(trunc(x.name, 18))}</text><text class="txm" x="${n.x + 20}" y="${n.y + 62}" font-size="14">system zewnętrzny</text></g>`;
  }
  back.innerHTML = b; $('#ffront').innerHTML = f;
  drawEdges(); drawFocus();
}

function overlayId() { return S.sel && S.sel.kind === 'obj' && M.byId.has(S.sel.id) ? S.sel.id : null; }
function drawEdges() {
  const g = $('#fedges'); if (!g) return;
  const ctx = overlayId() ? null : selContext();
  let es = '';
  if (ctx) for (const e of M.edges) {
    const a = L.nodes[e.src], b = L.nodes[e.dst];
    if (!a || !b || !(ctx.inside(e.src) || ctx.inside(e.dst))) continue;
    const d = a.kind === 'obj' && b.kind === 'obj' ? objEdge(ctr(a), ctr(b)) : boxEdge(ctr(a), ctr(b));
    es += `<path class="edge e-${e.kind}${e.unsure ? ' e-unsure' : ''}" d="${d}" stroke-width="2" marker-end="url(#m-${e.kind})"/>`;
  }
  g.innerHTML = es;
  // najechanie: powiązane obiekty zostają wyraźne, reszta przygasa
  document.querySelectorAll('#ffront .node.rel,#ffront .node.hdim').forEach(n => n.classList.remove('rel', 'hdim'));
  const hv = !S.sel ? S.hover : null;
  if (hv) {
    const rel = new Set([hv]); for (const e of M.edges) { if (e.src === hv) rel.add(e.dst); if (e.dst === hv) rel.add(e.src); }
    document.querySelectorAll('#ffront .node').forEach(n => { const id = n.dataset.id; if (id !== hv) n.classList.add(rel.has(id) ? 'rel' : 'hdim'); });
  }
}

/* Rozłożenie relacji: po kliknięciu obiektu reszta mapy przygasa, a powiązane obiekty rozjeżdżają się wokół niego */
function focusLayout(id, prev) {
  const svg = $('#fsvg'), r = svg.getBoundingClientRect(), s0 = VB.w / r.width;
  const p = $('#panel');
  let pw = p && !p.hidden && r.width > 760 ? p.offsetWidth + 24 : 0;
  if (r.width - pw < 620) pw = 0; // za ciasno obok panelu: używamy całej szerokości
  const f = Math.min(1, (r.width - pw) / 780), s = s0 * f;
  const cx = VB.x + (r.width - pw) / 2 * s0, cy = VB.y + VB.h / 2;
  const callers = [], deps = [], sl = new Set(), sr = new Set();
  for (const e of M.edges) {
    if (e.dst === id && !sl.has(e.src)) { sl.add(e.src); callers.push(e); }
    if (e.src === id && !sr.has(e.dst)) { sr.add(e.dst); deps.push(e); }
  }
  const NW = 196, NH = 30, STEP = 38, HEAD = 26, GAPX = Math.min(380, Math.max(300, (r.width - pw) / f / 2 - 120)), COLW = 220, CW = 250, CHh = 52;
  const maxRows = Math.max(6, Math.floor((r.height / f - 130) / STEP));
  const nodes = [{ id, x: cx, y: cy, w: CW * s, h: CHh * s, center: true }], edges = [], heads = [], titles = [];
  const place = (list, side) => {
    const k = (e) => side < 0 ? e.src : e.dst;
    const sorted = list.slice().sort((a, b) => famOrder.indexOf(famOf(k(a))) - famOrder.indexOf(famOf(k(b))) || nodeName(k(a)).localeCompare(nodeName(k(b))));
    const rows = []; let last = null;
    for (const e of sorted) { const fm = famOf(k(e)); if (fm !== last) { rows.push({ head: fm }); last = fm; } rows.push({ e }); }
    const cols = []; for (let i = 0; i < rows.length; i += maxRows) cols.push(rows.slice(i, i + maxRows));
    let top = cy;
    cols.forEach((cr, ci) => {
      const x = cx + side * (GAPX + ci * COLW) * s, hgt = cr.reduce((a, q) => a + (q.head ? HEAD : STEP), 0); let y = cy - hgt / 2 * s; top = Math.min(top, y);
      for (const q of cr) {
        if (q.head) { heads.push({ x: x - NW / 2 * s, y: y + 17 * s, t: FAMILY.find(z => z[0] === q.head)[1] }); y += HEAD * s; continue; }
        const ny = y + NH / 2 * s;
        nodes.push({ id: k(q.e), x, y: ny, w: NW * s, h: NH * s });
        edges.push({ side, x, y: ny, kind: q.e.kind, unsure: q.e.unsure, origin: q.e.origin }); y += STEP * s;
      }
    });
    const tx = cx + side * GAPX * s;
    titles.push(list.length ? { x: tx, y: top - 18 * s, t: (side < 0 ? 'Korzystają z niego · ' : 'Korzysta z · ') + list.length } : { x: tx, y: cy + 4 * s, t: side < 0 ? 'Nic z niego nie korzysta' : 'Nie korzysta z innych obiektów', muted: true });
  };
  place(callers, -1); place(deps, 1);
  for (const side of [-1, 1]) { const es = edges.filter(e => e.side === side).sort((a, b) => a.y - b.y); es.forEach((e, i) => { e.ay = cy + (es.length > 1 ? (i / (es.length - 1) - 0.5) * CHh * 0.7 * s : 0); }); }
  for (const n of nodes) {
    const pv = prev && prev.nodes.find(q => q.id === n.id), m = L.nodes[n.id] || L.nodes[M.byId.get(n.id)?.domain];
    if (pv) { n.ox = pv.x; n.oy = pv.y; } else if (m) { n.ox = m.x + m.w / 2; n.oy = m.y + m.h / 2; } else { n.ox = n.x; n.oy = n.y; }
  }
  return { id, s, cx, CW, NW, nodes, edges, heads, titles, animate: true };
}
function focusNode(n, s) {
  const w = n.w, h = n.h;
  if (n.id.startsWith('ext|')) { const x = M.extById.get(n.id); return `<rect class="card" x="${-w / 2}" y="${-h / 2}" width="${w}" height="${h}" rx="${8 * s}" style="stroke-dasharray:${5 * s} ${4 * s}"/><text class="tx" x="${-w / 2 + 12 * s}" y="${4.5 * s}" font-size="${12.5 * s}" font-weight="600">${esc(trunc(x?.name || n.id, 18))}</text><text class="txm" x="${w / 2 - 12 * s}" y="${4 * s}" font-size="${11 * s}" text-anchor="end">zewnętrzny</text>`; }
  const o = M.byId.get(n.id); if (!o) return '';
  const bw = (o.t.length > 1 ? 22 : 18) * s, center = M.byId.get(S.sel.id);
  return `<rect class="chip ${o.t === 'T' ? 'c-t' : 'c-l'}" x="${-w / 2}" y="${-h / 2}" width="${w}" height="${h}" rx="${8 * s}" style="stroke-width:${(n.center ? 2.6 : 1) * s}${n.center ? ';stroke:var(--accent)' : ''}"/>
    <rect x="${-w / 2 + 9 * s}" y="${-9 * s}" width="${bw}" height="${18 * s}" rx="${4 * s}" style="fill:${(TYPE[o.t] || TYPE['?']).c}"/><text x="${-w / 2 + 9 * s + bw / 2}" y="${3.6 * s}" font-size="${10 * s}" font-weight="700" fill="#fff" text-anchor="middle">${esc(o.t)}</text>
    ${n.center ? `<text class="tx" x="${-w / 2 + bw + 17 * s}" y="${-2 * s}" font-size="${15 * s}" font-weight="700">${esc(trunc(objName(o), 24))}</text><text class="txm" x="${-w / 2 + bw + 17 * s}" y="${15 * s}" font-size="${11.5 * s}">${TYPE[o.t]?.n || ''} · ${esc(trunc(domName(o.domain), 22))}</text>`
      : `<text class="tx" x="${-w / 2 + bw + 16 * s}" y="${4.4 * s}" font-size="${12.5 * s}" font-weight="500">${esc(trunc(o.project !== center?.project ? o.project + '.' + o.name : o.name, 22))}</text>`}
    <circle cx="${w / 2 - 12 * s}" cy="0" r="${5 * s}" style="fill:${domColor(o.domain)}"><title>${esc(domName(o.domain))}</title></circle>${o.warn ? `<circle cx="${w / 2 - 24 * s}" cy="0" r="${3.5 * s}" style="fill:var(--orange)"/>` : ''}`;
}
function drawFocus() {
  const g = $('#ffocus'); if (!g) return; const id = overlayId();
  if (!id) { g.innerHTML = ''; L.fl = null; return; }
  if (!L.fl || L.fl.id !== id) L.fl = focusLayout(id, L.fl);
  const F = L.fl, s = F.s;
  let h = '<rect class="fbackdrop" x="-50000" y="-50000" width="100000" height="100000"/>';
  for (const t of F.titles) h += `<text class="${t.muted ? 'txm' : 'tx'}" x="${t.x}" y="${t.y}" font-size="${(t.muted ? 13 : 14) * s}" font-weight="${t.muted ? 400 : 650}" text-anchor="middle">${esc(t.t)}</text>`;
  for (const t of F.heads) h += `<text class="txm" x="${t.x}" y="${t.y}" font-size="${10.5 * s}" font-weight="700" letter-spacing="${0.5 * s}">${esc(t.t.toUpperCase())}</text>`;
  let es = '', ls = '';
  for (const e of F.edges) {
    const outer = e.side < 0 ? [e.x + F.NW / 2 * s, e.y] : [e.x - F.NW / 2 * s, e.y], inner = [F.cx + e.side * F.CW / 2 * s, e.ay];
    const p0 = e.side < 0 ? outer : inner, p1 = e.side < 0 ? inner : outer, k = (p1[0] - p0[0]) * 0.5;
    es += `<path class="edge e-${e.kind}" d="${bez(p0, [p0[0] + k, p0[1]], [p1[0] - k, p1[1]], p1)}" stroke-width="${2 * s}" style="${e.unsure ? `stroke-dasharray:${6 * s} ${5 * s}` : ''}" marker-end="url(#m-${e.kind})"/>`;
    ls += `<text class="elabel" x="${outer[0] - e.side * 8 * s}" y="${outer[1] - 6 * s}" text-anchor="${e.side < 0 ? 'start' : 'end'}" font-size="${10.5 * s}" style="fill:${ECOL[e.kind]};stroke-width:${5 * s}px">${EDGE[e.kind] || e.kind}${e.unsure ? ' ?' : ''}</text>`;
  }
  h += `<g class="fe">${es}${ls}</g>`;
  for (const n of F.nodes) h += `<g class="node fc${n.center ? ' sel' : ''}" data-id="${esc(n.id)}" data-tx="${n.x}" data-ty="${n.y}" style="transform:translate(${F.animate ? n.ox : n.x}px,${F.animate ? n.oy : n.y}px)">${focusNode(n, s)}</g>`;
  g.innerHTML = h;
  const anim = F.animate;
  const go = () => { g.querySelectorAll('.fc').forEach(el => { el.style.transform = `translate(${el.dataset.tx}px,${el.dataset.ty}px)`; }); setTimeout(() => { const fe = g.querySelector('.fe'); if (fe) fe.classList.add('in'); }, anim ? 330 : 0); };
  if (anim) { F.animate = false; requestAnimationFrame(() => requestAnimationFrame(go)); } else go();
}

function fit() { const r = $('#fsvg').getBoundingClientRect(), b = L.bounds, s = Math.min(r.width / b.w, r.height / b.h), w = r.width / s, h = r.height / s; VB = { x: b.x - (w - b.w) / 2, y: b.y - (h - b.h) / 2, w, h }; L.fitW = w; apply(); }
function apply() { const svg = $('#fsvg'); if (svg && VB) svg.setAttribute('viewBox', `${VB.x} ${VB.y} ${VB.w} ${VB.h}`); }
function zoomAt(f, cx, cy) {
  const r = $('#fsvg').getBoundingClientRect(), px = (cx - r.left) / r.width, py = (cy - r.top) / r.height, wx = VB.x + px * VB.w, wy = VB.y + py * VB.h;
  const k = L.fitW / (VB.w / f); if (k < 0.5 || k > 9) return; VB.w /= f; VB.h /= f; VB.x = wx - px * VB.w; VB.y = wy - py * VB.h; apply();
}
/** Przybliża do obiektu (albo jego domeny), tak żeby nazwy były czytelne. */
function zoomTo(id) {
  const n = L.nodes[id] || L.nodes[M.byId.get(id)?.domain]; if (!n || !$('#fsvg')) return;
  const r = $('#fsvg').getBoundingClientRect(), pad = n.kind === 'obj' ? 170 : 50;
  const s = Math.min(r.width / (n.w + pad * 2), r.height / (n.h + pad * 2), r.width / L.fitW * 3.2), w = r.width / s, h = r.height / s;
  const p = $('#panel'), shift = p && !p.hidden && r.width > 760 ? (p.offsetWidth + 24) / 2 / r.width * w : 0;
  VB = { x: n.x + n.w / 2 - w / 2 + shift, y: n.y + n.h / 2 - h / 2, w, h }; apply();
  if (overlayId()) { L.fl = null; drawFocus(); }
}
function initMap() {
  layout(); const svg = $('#fsvg');
  { const r = svg.getBoundingClientRect(), b = L.bounds; L.fitW = r.width / Math.min(r.width / b.w, r.height / b.h); }
  if (!VB) fit(); else { const r = svg.getBoundingClientRect(); VB.h = VB.w * r.height / r.width; apply(); }
  drawMap();
  svg.addEventListener('wheel', e => { e.preventDefault(); zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX, e.clientY); }, { passive: false });
  let dr = null;
  svg.addEventListener('pointerdown', e => { dr = { x: e.clientX, y: e.clientY, vx: VB.x, vy: VB.y, m: false }; });
  svg.addEventListener('pointermove', e => {
    if (!dr) return; const dx = e.clientX - dr.x, dy = e.clientY - dr.y;
    if (!dr.m) { if (Math.hypot(dx, dy) < 4) return; dr.m = true; svg.setPointerCapture(e.pointerId); svg.classList.add('grabbing'); }
    const r = svg.getBoundingClientRect(); VB.x = dr.vx - dx / r.width * VB.w; VB.y = dr.vy - dy / r.height * VB.h; apply();
  });
  const end = () => { if (dr && dr.m) { L.dragged = true; setTimeout(() => { L.dragged = false; }, 0); svg.classList.remove('grabbing'); } dr = null; };
  svg.addEventListener('pointerup', end); svg.addEventListener('pointercancel', end);
  svg.addEventListener('pointerover', e => { if ((dr && dr.m) || S.sel) return; const n = e.target.closest('#ffront .node'); const id = n && M.byId.has(n.dataset.id) ? n.dataset.id : null; if (id !== S.hover) { S.hover = id; drawEdges(); } });
  svg.addEventListener('pointerleave', () => { if (S.hover) { S.hover = null; drawEdges(); } });
}

function mapView() {
  return `<div class="canvas"><div class="bar">
      <input class="search" id="search" type="search" placeholder="Szukaj tabeli, procedury…" value="${esc(S.q)}" aria-label="Szukaj">
      <span class="spacer"></span><button class="btn small" data-fit>Dopasuj</button></div>
    <div class="map"><svg id="fsvg" role="img" aria-label="Mapa">${markers()}<g id="fback"></g><g id="fedges"></g><g id="ffront"></g><g id="ffocus"></g></svg><aside class="panel" id="panel" ${S.sel ? '' : 'hidden'}></aside></div>
    <div class="legend"><span>${badge('T')} tabela</span><span>${badge('V')} widok</span><span>${badge('P')} procedura</span><span>${badge('F')} funkcja</span>
      <span><i style="border-color:var(--t-table)"></i>odczyt</span><span><i style="border-color:var(--orange)"></i>zapis</span><span><i style="border-color:var(--t-proc)"></i>wywołanie</span><span><i style="border-color:var(--muted)"></i>klucz obcy</span>
      <span class="hint">Kliknij obiekt, aby zobaczyć jego relacje · kółko myszy przybliża</span></div></div>`;
}

/* ================= Panel szczegółów ================= */
function originTxt(o) { return o === 'user' ? '<span class="origin user">poprawione przez Ciebie</span>' : o === 'claude' ? '<span class="origin">od Claude\'a</span>' : ''; }
function descBlock(key, value, origin) {
  if (S.editing === key) return `<div class="sec"><div class="sec-h">Opis</div><textarea class="input" id="edit-${esc(key)}">${esc(value || '')}</textarea><div class="row"><button class="btn primary small" data-save="${esc(key)}">Zapisz</button><button class="btn small" data-cancel>Anuluj</button></div></div>`;
  return `<div class="sec"><div class="sec-h">Opis<span class="r">${originTxt(origin)}${origin === 'user' ? ` · <button class="link" data-revert="${esc(key)}">przywróć</button>` : ''} · <button class="link" data-edit="${esc(key)}">Edytuj</button></span></div><p>${value ? esc(value) : '<span class="muted">Brak opisu.</span>'}</p></div>`;
}
function relGroup(title, list) {
  if (!list.length) return '';
  const item = (r) => `<li>${r.ext ? '<span class="badge" style="background:var(--faint)">↗</span>' : badge(r.t || '?')}<button data-goto="${esc(r.id)}">${esc(r.ext ? r.name : (r.project && M.byId.get(S.sel.id)?.project !== r.project ? r.project + '.' : '') + r.name)}</button><span class="r" style="color:${ECOL[r.kind] || 'var(--muted)'}">${EDGE[r.kind] || r.kind}${r.unsure ? ' ?' : ''}</span></li>`;
  return `<div class="sec"><div class="sec-h">${title}<span class="r num">${list.length}</span></div>${FAMILY.map(([f, lbl]) => { const g = list.filter(r => (r.ext ? 'ext' : r.t) === f); return g.length ? `<div class="sub-h">${lbl}</div><ul class="list">${g.map(item).join('')}</ul>` : ''; }).join('')}</div>`;
}
async function loadPanel() {
  const p = $('#panel'); if (!p) return;
  p.hidden = !S.sel;
  p.innerHTML = S.sel ? await panelHTML() : '';
}
async function objectDetail(id) {
  let d = CACHE.obj.get(id);
  if (!d) { d = await api('object?id=' + encodeURIComponent(id)); CACHE.obj.set(id, d); }
  return d;
}
async function panelHTML() {
  const s = S.sel, close = '<button class="close" data-close aria-label="Zamknij">×</button>';
  if (s.kind === 'project') {
    const p = M.projByName.get(s.id.slice(5)); if (!p) return close;
    const doms = M.domains.filter(d => d.project === p.name);
    return `<div class="p-head">${close}<span class="kind">Baza danych</span><h3>${esc(p.name)}</h3><span class="meta">${tabsTxt(p.tables)} · ${lgcTxt(p.count - p.tables)}</span></div>
      ${descBlock('note:project:' + p.name, p.description, p.descOrigin)}
      <div class="sec"><div class="sec-h">Domeny</div><ul class="list">${doms.map(d => `<li><span class="ddot" style="background:${d.colorVar}"></span><button data-goto="${esc(d.id)}">${esc(d.name)}</button><span class="r muted num">${d.count}</span></li>`).join('')}</ul></div>
      <div class="sec"><div class="sec-h">Projekt</div><dl class="kv2"><dt>Plik</dt><dd class="mono">${esc(p.file)}</dd><dt>Odwołania</dt><dd>${esc((p.info.references || []).map(r => r.name + (r.variable ? ` ($(${r.variable}))` : '')).join(', ') || 'brak')}</dd></dl></div>`;
  }
  if (s.kind === 'ext') {
    const x = M.extById.get(s.id); if (!x) return close;
    const users = M.edges.filter(e => e.dst === s.id).map(e => { const o = M.byId.get(e.src); return { id: e.src, name: objName(o), project: o.project, t: o.t, kind: e.kind }; });
    return `<div class="p-head">${close}<span class="kind">System zewnętrzny</span><h3>${esc(x.name)}</h3></div>${descBlock('note:' + s.id, x.description, x.descOrigin)}${relGroup('Używają go', users)}`;
  }
  if (s.kind === 'dom') {
    const d = M.domById.get(s.id); if (!d) return close;
    const list = M.domObjs.get(d.id) || [], nameEdit = S.editing === 'domname:' + d.id;
    return `<div class="p-head">${close}<span class="kind"><span class="ddot" style="background:${d.colorVar}"></span> Domena w bazie ${esc(d.project)}</span>
      ${nameEdit ? `<div class="row" style="margin-top:4px"><input class="input" id="edit-domname" value="${esc(d.name)}" style="flex:1;min-width:0"><button class="btn primary small" data-save="domname:${esc(d.id)}">Zapisz</button><button class="btn small" data-cancel>Anuluj</button></div>` : `<h3>${esc(d.name)}</h3><span class="meta">${originTxt(d.nameOrigin)} · <button class="link" data-edit="domname:${esc(d.id)}">Zmień nazwę</button></span>`}</div>
      ${descBlock('dom:' + d.id, d.description, d.descOrigin)}
      ${schemasOf(d.id).map(sc => { const ls = list.filter(o => o.schema === sc); return `<div class="sec"><div class="sec-h">Schemat ${esc(sc)}<span class="r num">${ls.length}</span></div>${FAMILY.map(([f, lbl]) => { const g = ls.filter(o => o.t === f); return g.length ? `<div class="sub-h">${lbl}</div><ul class="list">${g.map(o => `<li>${badge(o.t)}<button data-goto="${esc(o.id)}">${esc(o.name)}</button></li>`).join('')}</ul>` : ''; }).join('')}</div>`; }).join('')}`;
  }
  const d = await objectDetail(s.id);
  if (d.error) return close + '<div class="sec">Nie znaleziono obiektu.</div>';
  const doms = M.domains.filter(x => x.project === d.project), domEdit = S.editing === 'objdom:' + d.id;
  const open = d.issues.filter(i => i.status === 'open');
  return `<div class="p-head">${close}<span class="kind">${badge(d.t)} ${TYPE[d.t]?.n || d.type} · ${esc(d.project)}</span><h3>${esc(d.schema)}.${esc(d.name)}</h3>
    ${domEdit ? `<div class="selectrow" style="margin-top:6px"><select class="input" id="edit-objdom">${doms.map(x => `<option value="${esc(x.id)}" ${x.id === d.domain ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}<option value="__new">+ Nowa domena…</option></select><button class="btn primary small" data-save="objdom:${esc(d.id)}">Zapisz</button><button class="btn small" data-cancel>Anuluj</button></div>${d.domainOrigin === 'user' ? `<button class="link small" data-revertdom="${esc(d.id)}">Przywróć domenę od Claude'a</button>` : ''}`
      : `<span class="meta"><span class="ddot" style="background:${domColor(d.domain)}"></span> ${esc(d.domainName)} ${originTxt(d.domainOrigin)} · <button class="link" data-edit="objdom:${esc(d.id)}">Zmień domenę</button></span>`}</div>
    ${open.length ? `<div class="sec"><div class="note">${open.length} ${plural(open.length, 'pozycja', 'pozycje', 'pozycji')} do wyjaśnienia. <button class="link" data-v="issues">Przejdź</button></div></div>` : ''}
    ${descBlock('obj:' + d.id, d.description, d.descOrigin)}
    ${d.columns ? `<div class="sec"><div class="sec-h">Kolumny<span class="r num">${d.columns.length}</span></div><table class="tbl">${d.columns.map(c => `<tr><td><span class="mono">${esc(c.name)}</span>${c.pk ? '<span class="tag">PK</span>' : ''}${c.fk ? '<span class="tag">FK</span>' : ''}</td><td class="mono">${esc(c.type)}${c.nullable ? ' null' : ''}</td></tr>`).join('')}</table></div>` : ''}
    ${d.params?.length ? `<div class="sec"><div class="sec-h">Parametry</div><table class="tbl">${d.params.map(c => `<tr><td class="mono">${esc(c.name)}</td><td class="mono">${esc(c.type)}${c.output ? ' OUTPUT' : ''}</td></tr>`).join('')}</table></div>` : ''}
    ${relGroup('Korzysta z', d.uses)}${relGroup('Korzystają z niego', d.usedBy)}
    <div class="sec"><div class="sec-h">Definicja<span class="r mono">${esc(d.file || '')}</span></div><div class="def">${esc(d.definition)}</div></div>`;
}

/* ================= Zmiany: „było → jest” ================= */
const TYPE_TX = { added: 'nowy', modified: 'zmieniony', removed: 'usunięty', renamed: 'zmiana nazwy' };
const RISK_RANK = { high: 0, med: 1, low: 2 };
const riskBadge = (r) => r === 'high' ? '<span class="risk high">wymaga uwagi</span>' : r === 'med' ? '<span class="risk med">sprawdź</span>' : '';
function autoNote(c) {
  const d = c.details, parts = [];
  if (c.type === 'renamed' && c.oldName) parts.push(`Poprzednia nazwa: ${c.oldName}.`);
  if (d.columnsAdded?.length) parts.push('Dodane kolumny: ' + d.columnsAdded.join(', ') + '.');
  if (d.columnsRemoved?.length) parts.push('Usunięte kolumny: ' + d.columnsRemoved.join(', ') + '.');
  if (d.columnsChanged?.length) parts.push('Zmienione kolumny: ' + d.columnsChanged.join(', ') + '.');
  if (d.paramsAdded?.length) parts.push('Nowe parametry: ' + d.paramsAdded.join(', ') + '.');
  if (d.paramsRemoved?.length) parts.push('Usunięte parametry: ' + d.paramsRemoved.join(', ') + '.');
  if (d.newDynamic) parts.push('Zawiera dynamiczny SQL.');
  return parts.join(' ');
}
const nameOf = (id) => { const o = M.byId.get(id); return o ? `${o.project}.${objName(o)}` : id.replace('|', '.'); };
/** Szczegóły jednej zmiany: opis od Claude'a, kolumny i parametry, wpływ i różnica definicji „było → jest”. */
function changeDetail(c) {
  const d = c.details;
  return `${c.summary ? `<p>${esc(c.summary)}</p>` : ''}${autoNote(c) ? `<p class="muted small">${esc(autoNote(c))}</p>` : ''}
    ${d.brokenBy?.length ? `<p class="small"><span style="color:var(--red);font-weight:600">Przestaną działać:</span> ${d.brokenBy.map(b => esc(nameOf(b.object)) + (b.line ? ` (linia ${b.line})` : '')).join(', ')}</p>` : ''}
    ${d.dependents?.length ? `<p class="small muted">Korzystają z niego: ${d.dependents.slice(0, 15).map(x => esc(nameOf(x))).join(', ')}${d.dependents.length > 15 ? ` i ${d.dependents.length - 15} innych` : ''}</p>` : ''}
    ${c.diff?.length ? `<div class="diff">${c.diff.map(l => `<div class="${l[0]}">${esc(l[1])}</div>`).join('')}</div>` : ''}`;
}
async function changesView() {
  const cs = CACHE.changes || (CACHE.changes = await api('changes'));
  if (!cs) return `<div class="empty-state"><h2>Brak zmian do pokazania</h2><p>Zmiany pojawią się po odświeżeniu analizy: w Claude Code uruchom <code>/ssdt-atlas:refresh</code>.</p></div>`;
  const commits = cs.stats.commitList || [];
  const total = cs.changes.length;
  if (S.openChange === null && total) S.openChange = cs.changes[0].objectId;  // najważniejsza zmiana rozwinięta
  return `<div class="page" style="max-width:980px">
    <div class="lead"><p class="muted">Od analizy z ${fmtDate(cs.from?.createdAt)} do ${fmtDate(cs.to?.createdAt)}: ${objs(total)} ${plural(total, 'zmieniony', 'zmienione', 'zmienionych')}, ${commits.length} ${plural(commits.length, 'commit', 'commity', 'commitów')} w repozytorium.</p>
      ${cs.summary ? `<p>${esc(cs.summary)}</p>` : total ? '<p class="muted">Claude nie opisał jeszcze tych zmian. W Claude Code uruchom <span class="mono-inline">/ssdt-atlas:refresh</span>.</p>' : '<p>Od poprzedniej analizy żaden obiekt się nie zmienił.</p>'}</div>
    ${total ? `<div class="box">${cs.changes.map(c => {
      const open = S.openChange === c.objectId;
      return `<div class="fold${open ? ' open' : ''}"><button class="fold-h" data-change="${esc(c.objectId)}" aria-expanded="${open}">
          <span class="chev">${open ? '▾' : '▸'}</span>${badge(c.t)}<span class="name">${esc(c.project)}.${esc(c.name)}</span>
          <span class="muted">· ${TYPE_TX[c.type]}</span><span class="spacer"></span>${riskBadge(c.risk)}<span class="muted small">${esc(c.domainName)}</span></button>
        ${open ? `<div class="fold-b">${changeDetail(c)}</div>` : ''}</div>`;
    }).join('')}</div>` : ''}
    ${commits.length ? `<div class="sec-h" style="margin-top:6px">Commity od poprzedniej analizy<span class="r num">${commits.length}</span></div>
      <div class="box clist">${commits.map(c => `<div class="commit"><span class="mono sha">${esc(c.sha.slice(0, 7))}</span><span class="msg">${esc(c.message)}</span><span class="spacer"></span><span class="muted small">${esc(c.author)} · ${fmtDate(c.date)}</span></div>`).join('')}</div>` : ''}
  </div>`;
}

/* ================= Do wyjaśnienia ================= */
const UKIND = { dynamic: 'dynamiczny SQL', unresolved: 'nierozpoznane odwołanie', external: 'system zewnętrzny', parse: 'błąd składni' };
const UCLS = { dynamic: 'k-dyn', unresolved: 'k-ref', external: 'k-ext', parse: 'k-ref' };
/** Pełna definicja obiektu z numerami linii; podświetlona linia problemu i wystąpienia odwołania. */
function definitionView(d, u) {
  const lines = (d.definition || '').replace(/\r/g, '').split('\n');
  const target = u.line && d.line ? u.line - d.line : -1;
  const token = u.kind === 'dynamic' ? null : String(u.ref).split('.').pop().replace(/[()[\]]/g, '');
  const rx = token && token.length > 1 ? new RegExp('(' + token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi') : null;
  return `<div class="deflines">${lines.map((t, i) => {
    let html = esc(t);
    if (rx) html = html.replace(rx, '<mark>$1</mark>');
    return `<div class="${i === target ? 'hl' : ''}"><span class="ln">${d.line + i}</span>${html || ' '}</div>`;
  }).join('')}</div>`;
}
async function issuesView() {
  const list = CACHE.issues || (CACHE.issues = await api('issues'));
  const cnt = (s) => list.filter(i => i.status === s).length, n = cnt('open');
  const shown = list.filter(i => i.status === S.utab);
  const cards = [];
  for (const u of shown) {
    let details = '';
    if (S.openIssue === u.key) {
      if (M.byId.has(u.objectId)) { const d = await objectDetail(u.objectId); details = `<div class="sec-h">Pełna definicja · <span class="mono">${esc(d.file || '')}</span></div>${definitionView(d, u)}`; }
      else details = `<p class="muted">Plik nie został sparsowany: ${esc(u.ref)}</p>`;
    }
    cards.push(`<div class="box" data-u="${esc(u.key)}"><div class="box-b">
      <div class="row"><span class="kindtag ${UCLS[u.kind] || 'k-ref'}">${UKIND[u.kind] || u.kind}</span>${u.t ? badge(u.t) : ''}<b style="font-weight:600">${esc(u.project)}.${esc(u.object)}</b><span class="muted">· ${esc(trunc(String(u.ref), 80))}</span></div>
      ${u.message ? `<div class="muted small">${esc(u.message)}${u.line ? ` (linia ${u.line})` : ''}</div>` : ''}
      ${u.status === 'open' ? `${u.proposal ? `<p><span class="muted">Claude proponuje:</span> ${esc(u.proposal.text)}${u.proposal.confidence != null ? ` <span class="muted">(pewność ${u.proposal.confidence}%)</span>` : ''}</p>` : u.extNote?.description ? `<p><span class="muted">Claude opisuje system:</span> ${esc(u.extNote.description)}</p>` : ''}
        ${S.uform === u.key ? `<div class="inline-form">${u.kind === 'external' ? `<label class="sec-h" for="uf-text">Czym jest system „${esc(u.ref)}”?</label><textarea class="input" id="uf-text" placeholder="np. CRM firmy (Salesforce)"></textarea>` : `
          <label class="sec-h" for="uf-target">Czego dotyczy (opcjonalnie)</label><input class="input" id="uf-target" list="objList" placeholder="np. Sales|sales.orders">
          <datalist id="objList">${M.objects.slice(0, 2000).map(o => `<option value="${esc(o.id)}">${esc(o.project + '.' + objName(o))}</option>`).join('')}</datalist>
          <label class="sec-h" for="uf-kind">Rodzaj relacji</label><select class="input" id="uf-kind"><option value="reads">odczyt</option><option value="writes">zapis</option><option value="calls">wywołanie</option></select>
          <label class="sec-h" for="uf-text">Opis</label><textarea class="input" id="uf-text" placeholder="np. tabela wybierana z cfg.PurgeTables"></textarea>`}
          <div class="row"><button class="btn primary small" data-uact="save">Zapisz</button><button class="btn small" data-uact="cancel">Anuluj</button></div></div>`
        : `<div class="row">${u.proposal ? '<button class="btn primary small" data-uact="accept">Zatwierdź propozycję</button>' : u.kind === 'external' && u.extNote?.description ? '<button class="btn primary small" data-uact="acceptext">Zatwierdź opis</button>' : ''}<button class="btn small" data-uact="form">${u.kind === 'external' ? 'Opisz system' : 'Uzupełnij ręcznie'}</button><button class="btn small" data-uact="skip">Pomiń</button><span class="spacer"></span><button class="link" data-uact="details">${S.openIssue === u.key ? 'Ukryj szczegóły' : 'Szczegóły'}</button></div>`}`
      : `<div class="row"><span class="muted">${esc(u.resolution || '')}</span><span class="spacer"></span><button class="link" data-uact="details">${S.openIssue === u.key ? 'Ukryj szczegóły' : 'Szczegóły'}</button><button class="link" data-uact="reopen">Cofnij</button></div>`}
      ${details}</div></div>`);
  }
  return `<div class="page" style="max-width:980px">
    <div class="lead"><p class="muted">${n ? `${n} ${plural(n, 'miejsce, którego', 'miejsca, których', 'miejsc, których')} silnik nie rozpoznał. Zatwierdź propozycję Claude'a albo uzupełnij ręcznie.` : 'Wszystko wyjaśnione.'}</p></div>
    <div class="tabs" role="group" aria-label="Filtr"><button data-utab="open" aria-pressed="${S.utab === 'open'}">Do zrobienia ${n}</button><button data-utab="done" aria-pressed="${S.utab === 'done'}">Rozwiązane ${cnt('done')}</button><button data-utab="skip" aria-pressed="${S.utab === 'skip'}">Pominięte ${cnt('skip')}</button></div>
    ${cards.length ? cards.join('') : '<p class="muted">Nic tu nie ma.</p>'}
  </div>`;
}

/* ================= Wskazówki ================= */
function parseGuides(md) {
  const secs = []; let cur = null;
  for (const line of (md || '').replace(/\r/g, '').split('\n')) {
    const m = /^##\s+(.*)$/.exec(line);
    if (m) { cur = { title: m[1].trim(), body: [] }; secs.push(cur); }
    else if (cur) cur.body.push(line);
    else if (line.trim() && !/^#\s/.test(line)) { cur = { title: 'Ogólne', body: [line] }; secs.push(cur); }
  }
  return secs.map(s => ({ title: s.title, body: s.body.join('\n').trim() }));
}
function buildGuides(secs) { return '# Wskazówki dla SSDT Atlas\n\n' + secs.map(s => `## ${s.title}\n\n${s.body}\n`).join('\n'); }
async function guidesView() {
  const g = CACHE.guides || (CACHE.guides = await api('guidelines'));
  const secs = parseGuides(g.markdown);
  return `<div class="page" style="max-width:820px">
    <div class="lead"><p class="muted">Claude korzysta z tych wskazówek przy każdej analizie. Plik: <span class="mono-inline">.claude/data-architect/guidelines.md</span>.</p></div>
    ${secs.length ? `<div class="box">${secs.map((s, i) => { const k = 'g' + i, ed = S.editing === k; return `<div class="guide"><div class="sec-h">${esc(s.title)}<span class="r">${ed ? '' : `<button class="link" data-edit="${k}">Edytuj</button>`}</span></div>
      ${ed ? `<textarea class="input" id="edit-${k}">${esc(s.body)}</textarea><div class="row"><button class="btn primary small" data-save="${k}">Zapisz</button><button class="btn small" data-cancel>Anuluj</button></div>` : `<p style="white-space:pre-wrap">${esc(s.body)}</p>`}</div>`; }).join('')}</div>`
      : '<p class="muted">Brak wskazówek. Powstaną podczas <span class="mono-inline">/ssdt-atlas:init</span>.</p>'}
    ${S.editing === 'gnew' ? `<div class="box"><div class="box-b"><input class="input" id="edit-gnew-title" placeholder="Tytuł"><textarea class="input" id="edit-gnew" placeholder="Treść wskazówki"></textarea><div class="row"><button class="btn primary small" data-save="gnew">Dodaj</button><button class="btn small" data-cancel>Anuluj</button></div></div></div>` : '<button class="btn small" style="align-self:flex-start" data-edit="gnew">Dodaj wskazówkę</button>'}
  </div>`;
}

/* ================= Akcje ================= */
async function select(id) {
  S.sel = id ? { id, kind: kindOf(id) } : null; S.editing = null; S.hover = null;
  if (S.view === 'map') { await loadPanel(); drawMap(); }
  writeHash();
}
function askName() {
  return new Promise((resolve) => {
    const box = document.createElement('div');
    box.className = 'sec';
    box.innerHTML = '<div class="sec-h">Nazwa nowej domeny</div><input class="input" id="newDomName"><div class="row"><button class="btn primary small" id="newDomOk">Utwórz</button><button class="btn small" id="newDomCancel">Anuluj</button></div>';
    $('#panel').prepend(box); $('#newDomName').focus();
    $('#newDomOk').onclick = () => resolve($('#newDomName').value.trim());
    $('#newDomCancel').onclick = () => { box.remove(); resolve(''); };
  });
}
async function saveEdit(key) {
  const val = (sel) => { const f = document.getElementById(sel); return f ? f.value.trim() : ''; };
  try {
    if (key.startsWith('obj:')) await api('object', { id: key.slice(4), description: val('edit-' + key) || null });
    else if (key.startsWith('dom:')) await api('domain', { id: key.slice(4), description: val('edit-' + key) || null });
    else if (key.startsWith('note:')) await api('note', { target: key.slice(5), description: val('edit-' + key) || null });
    else if (key.startsWith('domname:')) await api('domain', { id: key.slice(8), name: val('edit-domname') || null });
    else if (key.startsWith('objdom:')) {
      let dom = $('#edit-objdom').value;
      if (dom === '__new') {
        const nm = await askName(); if (!nm) return;
        dom = (await api('domain/create', { project: M.byId.get(key.slice(7)).project, name: nm })).id;
      }
      await api('object', { id: key.slice(7), domain: dom });
    } else if (key === 'gnew') { const secs = parseGuides(CACHE.guides.markdown); secs.push({ title: val('edit-gnew-title') || 'Wskazówka', body: val('edit-gnew') }); await api('guidelines', { markdown: buildGuides(secs) }); }
    else if (/^g\d+$/.test(key)) { const secs = parseGuides(CACHE.guides.markdown); secs[+key.slice(1)].body = val('edit-' + key); await api('guidelines', { markdown: buildGuides(secs) }); }
    S.editing = null;
    toast('Zapisano. Kolejne analizy tego nie nadpiszą.');
    await loadAll(); L.bounds = null; await render();
  } catch (e) { toast('Nie udało się zapisać: ' + e.message); }
}

document.addEventListener('click', async (e) => {
  const t = e.target, c = (s) => t.closest(s); let x;
  if ((x = c('[data-v]'))) { S.view = x.dataset.v; S.editing = null; S.uform = null; await render(); return; }
  if (c('#fsvg')) { if (L.dragged) return; const n = c('.node'); if (n) { const id = n.dataset.id; await select(S.sel && S.sel.id === id ? null : id); } else if (S.sel) await select(null); return; }
  if (c('[data-fit]')) { fit(); return; }
  if (c('[data-close]')) { await select(null); return; }
  if ((x = c('[data-goto]'))) { const id = x.dataset.goto; await select(id); if (M.byId.has(id)) zoomTo(id); return; }
  if ((x = c('[data-edit]'))) { S.editing = x.dataset.edit; if (S.view === 'map') await loadPanel(); else await render(); const f = document.querySelector('[id^="edit-"]'); if (f) f.focus(); return; }
  if (c('[data-cancel]')) { S.editing = null; if (S.view === 'map') await loadPanel(); else await render(); return; }
  if ((x = c('[data-save]'))) { await saveEdit(x.dataset.save); return; }
  if ((x = c('[data-revert]'))) {
    const k = x.dataset.revert;
    if (k.startsWith('obj:')) await api('object', { id: k.slice(4), description: null });
    else if (k.startsWith('dom:')) await api('domain', { id: k.slice(4), description: null });
    else if (k.startsWith('note:')) await api('note', { target: k.slice(5), description: null });
    toast('Przywrócono wersję od Claude\'a.'); await loadAll(); await render(); return;
  }
  if ((x = c('[data-revertdom]'))) { await api('object', { id: x.dataset.revertdom, domain: null }); toast('Przywrócono domenę od Claude\'a.'); await loadAll(); L.bounds = null; await render(); return; }
  if ((x = c('[data-change]'))) { S.openChange = S.openChange === x.dataset.change ? '' : x.dataset.change; await render(); return; }
  if ((x = c('[data-utab]'))) { S.utab = x.dataset.utab; S.uform = null; S.openIssue = null; await render(); return; }
  if ((x = c('[data-uact]'))) {
    const key = c('[data-u]').dataset.u, a = x.dataset.uact, u = CACHE.issues.find(i => i.key === key);
    try {
      if (a === 'details') { S.openIssue = S.openIssue === key ? null : key; await render(); return; }
      if (a === 'form') { S.uform = key; await render(); return; }
      if (a === 'cancel') { S.uform = null; await render(); return; }
      if (a === 'accept') { await api('issue', { key, action: 'accept' }); toast('Zatwierdzono.'); }
      if (a === 'acceptext') { await api('issue', { key, action: 'resolve', text: u.extNote.description, ext: true }); toast('Zatwierdzono opis systemu.'); }
      if (a === 'skip') await api('issue', { key, action: 'skip' });
      if (a === 'reopen') await api('issue', { key, action: 'reopen' });
      if (a === 'save') {
        const text = $('#uf-text')?.value.trim() || '';
        if (u.kind === 'external') await api('issue', { key, action: 'resolve', text, ext: true });
        else { const target = $('#uf-target')?.value.trim() || ''; await api('issue', { key, action: 'resolve', text, target, kind: $('#uf-kind')?.value, targetIsObject: M.byId.has(target) }); }
        toast('Zapisano.');
      }
      S.uform = null; await loadAll(); await render();
    } catch (err) { toast('Błąd: ' + err.message); }
    return;
  }
});
document.addEventListener('input', (e) => { if (e.target.id === 'search') { S.q = e.target.value; drawMap(); } });
document.addEventListener('keydown', async (e) => {
  if (e.target.id === 'search' && e.key === 'Enter') {
    const q = S.q.trim().toLowerCase(); if (!q) return;
    const m = M.objects.find(o => objName(o).toLowerCase() === q || o.name.toLowerCase() === q) || M.objects.find(o => objName(o).toLowerCase().includes(q));
    if (m) { await select(m.id); zoomTo(m.id); } else toast('Nie znaleziono: ' + S.q);
  }
  if (e.key === 'Escape' && S.view === 'map') { if (S.editing) { S.editing = null; await loadPanel(); } else if (S.sel) await select(null); }
});
window.addEventListener('hashchange', async () => { const before = S.view + (S.sel ? '/' + S.sel.id : ''); readHash(); if (location.hash.slice(1) !== before) { await render(); if (S.sel && $('#fsvg')) zoomTo(S.sel.id); } });
window.addEventListener('resize', () => { if (S.view === 'map' && $('#fsvg') && VB) { const r = $('#fsvg').getBoundingClientRect(); VB.h = VB.w * r.height / r.width; apply(); } });

/* ================= Start ================= */
(async function start() {
  readHash();
  try { await loadAll(); } catch (e) { $('#view').innerHTML = `<div class="empty-state"><h2>Brak połączenia z serwerem</h2><p>${esc(e.message)}</p></div>`; return; }
  await render();
  if (S.sel && $('#fsvg')) zoomTo(S.sel.id);
  setInterval(poll, 3000);
})();
