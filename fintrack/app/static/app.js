'use strict';
// ---------------------------------------------------------------- Helfer
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' });
const fmt0 = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
const eur = c => fmt.format((c || 0) / 100);
const eur0 = c => fmt0.format((c || 0) / 100);
const pct = v => v == null ? '–' : (v * 100).toLocaleString('de-DE', { maximumFractionDigits: 0 }) + ' %';
const ymNow = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; };
const isYear = ym => /^\d{4}$/.test(ym);
const shiftYm = (ym, n) => { if (isYear(ym)) return String(Number(ym) + n);  const [y, m] = ym.split('-').map(Number); const i = y * 12 + m - 1 + n; return `${Math.floor(i / 12)}-${String(i % 12 + 1).padStart(2, '0')}`; };
const monthName = ym => isYear(ym) ? ym : new Date(ym + '-01T12:00:00').toLocaleDateString('de-DE', { month: 'long', year: 'numeric' });
const monthShort = ym => new Date(ym + '-01T12:00:00').toLocaleDateString('de-DE', { month: 'short' }).replace('.', '');
const dateDe = iso => iso ? iso.split('-').reverse().join('.') : '';
const sgn = c => `<span class="${c > 0 ? 'pos' : 'neg'}">${c > 0 ? '+' : ''}${eur(c)}</span>`;

const state = { month: ymNow(), accountId: '', cats: [], accounts: [], fintsEnabled: false, comdirectApi: false, presets: {} };

async function api(path, opts = {}) {
  const o = { credentials: 'same-origin', ...opts };
  if (o.json !== undefined) {
    o.method = o.method || 'POST';
    o.headers = { 'Content-Type': 'application/json' };
    o.body = JSON.stringify(o.json);
  }
  const r = await fetch('api' + path, o);  // relativ: funktioniert auch unter dem Ingress-Pfad von Home Assistant
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && !path.startsWith('/login')) { showLogin(); throw new Error('Nicht angemeldet'); }
  if (!r.ok) { const e = new Error(data.error || `Fehler ${r.status}`); e.data = data; throw e; }
  return data;
}

let toastTimer;
function toast(msg, bad = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast show' + (bad ? ' bad' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), bad ? 6000 : 3000);
}

function openDialog(html) {
  const d = $('#dlg');
  d.innerHTML = html;
  if (!d.open) d.showModal();
  return d;
}
function closeDialog() { const d = $('#dlg'); if (d.open) d.close(); }

async function loadMeta() {
  const [c, a] = await Promise.all([api('/categories'), api('/accounts')]);
  state.cats = c.categories;
  state.accounts = a.accounts;
  state.fintsEnabled = a.fints_enabled;
  state.comdirectApi = a.comdirect_api_enabled;
  state.presets = a.presets;
}

const KIND_ORDER = { expense: 0, income: 1, transfer: 2 };
const KIND_LABEL = { expense: 'Ausgaben', income: 'Einnahmen', transfer: 'Umbuchungen / Sparen' };
function catTree(kinds) {
  const cats = state.cats.filter(c => !kinds || kinds.includes(c.kind));
  const byName = (a, b) => a.name.localeCompare(b.name, 'de');
  return cats.filter(c => !c.parent_id)
    .sort((a, b) => (KIND_ORDER[a.kind] - KIND_ORDER[b.kind]) || byName(a, b))
    .map(c => ({ ...c, children: cats.filter(x => x.parent_id === c.id).sort(byName) }));
}
const catOptions = (sel, kinds) => {
  const opt = (c, label) => `<option value="${c.id}" ${String(c.id) === String(sel) ? 'selected' : ''}>${esc(label || c.name)}</option>`;
  return catTree(kinds).map(t => t.children.length
    ? `<optgroup label="${esc(t.name)}">${opt(t, t.name + ' (allgemein)')}${t.children.map(c => opt(c)).join('')}</optgroup>` : opt(t)).join('');
};

function monthToolbar(withAccount = true, withYear = false) {
  const yr = isYear(state.month);
  return `<div class="toolbar">
    <button class="btn" id="m-prev" aria-label="${yr ? 'Vorheriges Jahr' : 'Vorheriger Monat'}">‹</button>
    <button class="btn picker" id="m-pick" data-year="${withYear ? 1 : 0}" aria-haspopup="dialog" aria-label="Zeitraum wählen: ${esc(monthName(state.month))}"><span>${esc(monthName(state.month))}</span><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/></svg></button>
    <button class="btn" id="m-next" aria-label="${yr ? 'Nächstes Jahr' : 'Nächster Monat'}">›</button>
    <button class="btn ghost" id="m-now">Heute</button>
    <span class="spacer"></span>
  </div>${withAccount && state.accounts.length > 1 ? accountTags() : ''}`;
}

// Zeitraum-Auswahl: Monate eines Jahres oder das ganze Jahr
function periodPicker(withYear, rerender) {
  let y = Number((state.month || ymNow()).slice(0, 4));
  const now = ymNow(), curY = Number(now.slice(0, 4));
  const draw = () => {
    const sel = state.month, yrSel = isYear(sel) && Number(sel) === y;
    const mnames = Array.from({ length: 12 }, (_, i) => new Date(2000, i, 1).toLocaleDateString('de-DE', { month: 'short' }).replace('.', ''));
    const d = openDialog(`<div class="pp"><div class="pp-year"><button type="button" class="btn" id="pp-py" aria-label="Vorjahr">‹</button>${withYear ? `<button type="button" class="pp-yearbtn ${yrSel ? 'on' : ''}" data-ym="${y}" aria-pressed="${yrSel}" title="Ganzes Jahr ${y} auswählen">${y}</button>` : `<b id="pp-y">${y}</b>`}<button type="button" class="btn" id="pp-ny" aria-label="Nächstes Jahr">›</button></div>
      <div class="pp-grid" role="group" aria-label="Monat in ${y}">${mnames.map((n, i) => { const ym = `${y}-${String(i + 1).padStart(2, '0')}`;
        return `<button type="button" class="pp-m ${sel === ym ? 'on' : ''} ${ym === now ? 'cur' : ''}" data-ym="${ym}" aria-pressed="${sel === ym}">${n}</button>`; }).join('')}</div>
      ${withYear ? '<p class="muted pp-hint">Jahreszahl antippen = ganzes Jahr</p>' : ''}
      <div class="actions"><button type="button" class="btn ghost" id="pp-now">Heute</button><button type="button" class="btn" id="pp-close">Schließen</button></div></div>`);
    $('#pp-py', d).onclick = () => { y--; draw(); };
    $('#pp-ny', d).onclick = () => { y++; draw(); };
    $('#pp-close', d).onclick = closeDialog;
    $('#pp-now', d).onclick = () => { state.month = now; closeDialog(); rerender(); };
    $$('[data-ym]', d).forEach(b => b.onclick = () => { state.month = b.dataset.ym; closeDialog(); rerender(); });
  };
  draw();
}
// Konten als wählbare Tags (Mehrfachauswahl; keine Auswahl = alle Konten)
const selAccIds = () => String(state.accountId || '').split(',').filter(Boolean);
function accountTags() {
  const sel = selAccIds();
  return `<div class="acc-tags" role="group" aria-label="Konten filtern"><button type="button" class="tag ${sel.length ? '' : 'on'}" data-acc="" aria-pressed="${!sel.length}">Alle Konten</button>${state.accounts.map(a =>
    `<button type="button" class="tag ${sel.includes(String(a.id)) ? 'on' : ''}" data-acc="${a.id}" aria-pressed="${sel.includes(String(a.id))}">${esc(a.name)}</button>`).join('')}</div>`;
}
function accountLabel() {
  return selAccIds().map(i => state.accounts.find(a => String(a.id) === i)?.name).filter(Boolean).join(', ');
}
function wireToolbar(rerender) {
  $('#m-prev').onclick = () => { state.month = shiftYm(state.month, -1); rerender(); };
  $('#m-next').onclick = () => { state.month = shiftYm(state.month, 1); rerender(); };
  $('#m-now').onclick = () => { state.month = isYear(state.month) ? ymNow().slice(0, 4) : ymNow(); rerender(); };
  $('#m-pick').onclick = () => periodPicker($('#m-pick').dataset.year === '1', rerender);
  $$('.acc-tags .tag').forEach(b => b.onclick = () => {
    const id = b.dataset.acc;
    if (!id) state.accountId = '';
    else {
      const s = selAccIds(), i = s.indexOf(id);
      if (i >= 0) s.splice(i, 1); else s.push(id);
      state.accountId = s.length >= state.accounts.length ? '' : s.join(',');   // alle gewählt = kein Filter
    }
    rerender();
  });
}

// ---------------------------------------------------------------- Diagramme (reines SVG, keine Abhängigkeiten)
function chartW(max) { const w = ($('#view') || {}).clientWidth || max; return Math.max(280, Math.min(max, w - 40)); }
function barChart(months, selected) {
  const W = chartW(720), H = W < 480 ? 200 : 230, pad = { l: 52, r: 8, t: 10, b: 24 };
  if (W < 480 && !isYear(selected)) months = months.slice(-6);
  const max = Math.max(1, ...months.flatMap(m => [m.income_cents, m.expenses_cents]));
  const nice = Math.ceil(max / 100 / 500) * 500 * 100 || 10000;
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b, gw = iw / months.length, bw = Math.min(16, gw / 3);
  const y = v => pad.t + ih - (v / nice) * ih;
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Einnahmen und Ausgaben der letzten Monate">`;
  for (let i = 0; i <= 4; i++) {
    const v = nice * i / 4;
    s += `<line class="gridline" x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${pad.l - 6}" y="${y(v) + 4}" text-anchor="end">${eur0(v)}</text>`;
  }
  months.forEach((m, i) => {
    const cx = pad.l + gw * i + gw / 2, cls = (m.month === selected || (isYear(selected) && m.month.startsWith(selected))) ? 'sel' : 'dim';
    s += `<g class="${cls}"><title>${esc(monthName(m.month))}: Einnahmen ${eur(m.income_cents)}, Ausgaben ${eur(m.expenses_cents)}</title>
      <rect class="inc" x="${cx - bw - 1}" y="${y(Math.max(0, m.income_cents))}" width="${bw}" height="${Math.max(0, ih - (y(Math.max(0, m.income_cents)) - pad.t))}" rx="3"/>
      <rect class="exp" x="${cx + 1}" y="${y(m.expenses_cents)}" width="${bw}" height="${Math.max(0, ih - (y(m.expenses_cents) - pad.t))}" rx="3"/></g>
      <text x="${cx}" y="${H - 7}" text-anchor="middle">${esc(monthShort(m.month))}</text>`;
  });
  return s + '</svg><div class="legend"><span><span class="dot" style="background:var(--good)"></span>Einnahmen</span><span><span class="dot" style="background:var(--accent)"></span>Ausgaben</span></div>';
}

function lineChart(o) {
  const W = chartW(520), H = 200, pad = { l: 52, r: 10, t: 10, b: 22 };
  const cum = o.cumulative_cents, n = o.days_in_month;
  const top = Math.max(1, o.total_budget_cents || 0, o.projected_expenses_cents || 0, o.expenses_cents) * 1.08;
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const x = d => pad.l + (d - 1) / Math.max(1, n - 1) * iw, y = v => pad.t + ih - v / top * ih;
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Kumulierte Ausgaben im Monat">`;
  for (let i = 0; i <= 4; i++) {
    const v = top * i / 4;
    s += `<line class="gridline" x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${pad.l - 6}" y="${y(v) + 4}" text-anchor="end">${eur0(v)}</text>`;
  }
  if (o.is_year) {
    const yy = Number(o.month); let day = 1;
    for (let mo = 0; mo < 12; mo++) {
      if (mo % (W < 480 ? 2 : 1) === 0) s += `<text x="${x(day)}" y="${H - 6}" text-anchor="middle">${monthShort(`${yy}-${String(mo + 1).padStart(2, '0')}`)}</text>`;
      day += new Date(yy, mo + 1, 0).getDate();
    }
  } else [1, 8, 15, 22, n].forEach(d => { s += `<text x="${x(d)}" y="${H - 6}" text-anchor="middle">${d}.</text>`; });
  if (o.total_budget_cents) s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(o.total_budget_cents)}" y2="${y(o.total_budget_cents)}" stroke="var(--warn)" stroke-dasharray="5 4"/><text x="${W - pad.r}" y="${y(o.total_budget_cents) - 5}" text-anchor="end">Budget ${eur0(o.total_budget_cents)}</text>`;
  if (cum.length) {
    const pts = cum.map((v, i) => `${x(i + 1)},${y(v)}`).join(' ');
    s += `<polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2.2" stroke-linejoin="round"/>`;
    if (o.is_current && cum.length < n) {
      s += `<line x1="${x(cum.length)}" y1="${y(cum[cum.length - 1])}" x2="${x(n)}" y2="${y(o.projected_expenses_cents)}" stroke="var(--accent)" stroke-dasharray="4 4" opacity=".6"/><circle cx="${x(n)}" cy="${y(o.projected_expenses_cents)}" r="3.5" fill="var(--accent)" opacity=".6"/>`;
    }
  }
  return s + '</svg>';
}

// Sankey: Einnahmequellen -> Einnahmen -> Verwendung (Kategorien, Sparen, Übrig)
function sankeyChart(o) {
  const grp = (list, total) => {
    list = list.filter(x => x.value > 0).sort((a, b) => b.value - a.value);
    const small = list.filter(x => x.value / total < 0.025), big = list.filter(x => x.value / total >= 0.025);
    if (small.length >= 2) big.push({ name: 'Weitere', value: small.reduce((s, x) => s + x.value, 0), color: '#9aa0a6' }); else big.push(...small);
    if (big.length > 9) { const rest = big.splice(8); big.push({ name: 'Weitere', value: rest.reduce((s, x) => s + x.value, 0), color: '#9aa0a6' }); }
    return big;
  };
  const exps = o.categories.filter(c => c.spent_cents > 0).map(c => ({ id: c.id, name: c.name, value: c.spent_cents, color: c.color }));
  if (o.transfers_cents > 0) exps.push({ name: 'Sparen & Umbuchungen', value: o.transfers_cents, color: '#3f8f5f' });
  const outTotal = exps.reduce((s, x) => s + x.value, 0);
  const left = o.income_categories.map(c => ({ name: c.name, value: c.amount_cents, color: c.color }));
  const inTotal = left.reduce((s, x) => s + x.value, 0);
  const diff = inTotal - outTotal;
  if (diff > 0) exps.push({ name: 'Übrig', value: diff, color: 'var(--good)' });
  else if (diff < 0) left.push({ name: 'Mehr ausgegeben als eingenommen', value: -diff, color: 'var(--warn)' });
  const total = Math.max(inTotal, outTotal);
  if (total <= 0) return '';
  const L = grp(left, total), R = grp(exps, total);
  const W = chartW(1000), narrow = W < 480, nw = 10, g = 8;
  const H = Math.max(260, Math.min(460, Math.max(L.length, R.length) * 34));
  const scale = Math.min((H - g * (L.length - 1)) / total, (H - g * (R.length - 1)) / total);
  const cx = Math.round(W / 2 - nw / 2), ch = total * scale, cy = (H - ch) / 2;
  const cut = (s, n) => s.length > n ? s.slice(0, n - 1) + '…' : s;
  const maxc = narrow ? 13 : 30;
  const place = list => { const colH = list.reduce((s, x) => s + x.value * scale, 0) + g * (list.length - 1); let y = (H - colH) / 2; return list.map(x => { const h = x.value * scale, n = { ...x, y, h }; y += h + g; return n; }); };
  const Ls = place(L), Rs = place(R);
  let s = `<svg class="sk" viewBox="0 0 ${W} ${H + 24}" width="100%" role="img" aria-label="Sankey-Diagramm: Wohin fließen die Einnahmen">`;
  s += `<g transform="translate(0,22)">`;
  const ribbon = (x0, y0, x1, y1, h, color, title) => `<path d="M${x0},${y0} C${(x0 + x1) / 2},${y0} ${(x0 + x1) / 2},${y1} ${x1},${y1}" fill="none" stroke="${color}" stroke-width="${Math.max(1, h)}" opacity=".32"><title>${esc(title)}</title></path>`;
  let off = cy; Ls.forEach(n => { s += ribbon(nw, n.y + n.h / 2, cx, off + n.h / 2, n.h, n.color, `${n.name}: ${eur(n.value)}`); off += n.h; });
  off = cy; Rs.forEach(n => { s += ribbon(cx + nw, off + n.h / 2, W - nw, n.y + n.h / 2, n.h, n.color, `${n.name}: ${eur(n.value)}`); off += n.h; });
  s += `<rect x="${cx}" y="${cy}" width="${nw}" height="${ch}" rx="3" fill="var(--accent)"/>`;
  const lab = (n, x, anchor) => {
    const two = n.h >= 30, mid = n.y + n.h / 2;
    return two ? `<text x="${x}" y="${mid - 2}" text-anchor="${anchor}">${esc(cut(n.name, maxc))}</text><text class="v" x="${x}" y="${mid + 12}" text-anchor="${anchor}">${eur0(n.value)}</text>`
      : `<text x="${x}" y="${mid + 4}" text-anchor="${anchor}">${esc(cut(n.name, maxc))} · ${eur0(n.value)}</text>`;
  };
  Ls.forEach(n => { s += `<g><title>${esc(n.name)}: ${eur(n.value)}</title><rect x="0" y="${n.y}" width="${nw}" height="${n.h}" rx="3" fill="${n.color}"/>${lab(n, nw + 6, 'start')}</g>`; });
  Rs.forEach(n => { s += `<g ${n.id ? `class="sk-link" data-sk-cat="${n.id}"` : ''}><title>${esc(n.name)}: ${eur(n.value)}</title><rect x="${W - nw}" y="${n.y}" width="${nw}" height="${n.h}" rx="3" fill="${n.color}"/>${lab(n, W - nw - 6, 'end')}</g>`; });
  s += `<text class="h" x="${cx + nw / 2}" y="-8" text-anchor="middle">Einnahmen ${eur0(inTotal)}</text></g></svg>`;
  return s;
}

// ---------------------------------------------------------------- Ansichten
async function viewOverview() {
  const v = $('#view');
  const acc = state.accountId ? `&account_id=${state.accountId}` : '';
  const [o, ms] = await Promise.all([api(`/analysis/overview?month=${state.month}${acc}`), api(`/analysis/monthly?months=12${isYear(state.month) ? `&end_month=${state.month}-12` : ''}${acc}`)]);
  if (!state.accounts.length) {
    v.innerHTML = `<div class="card empty"><h2>Willkommen 👋</h2><p>Lege zuerst ein Konto an und importiere deine Umsätze.</p><a class="btn primary" href="#/konten">Zu den Konten</a></div>`;
    return;
  }
  const maxSpent = Math.max(1, ...o.categories.map(c => Math.max(c.spent_cents, c.budget_cents || 0)));
  const hasData = o.income_cents || o.expenses_cents || o.categories.length;
  v.innerHTML = monthToolbar(true, true) + `
    <div class="grid kpis">
      <div class="card kpi"><div class="label">Einnahmen</div><div class="value pos">${eur(o.income_cents)}</div></div>
      <div class="card kpi"><div class="label">Ausgaben</div><div class="value">${eur(o.expenses_cents)}</div>
        ${o.is_current ? `<div class="sub">Prognose ${o.is_year ? 'Jahresende' : 'Monatsende'} ≈ ${eur0(o.projected_expenses_cents)}</div>` : ''}</div>
      <div class="card kpi"><div class="label">Saldo</div><div class="value ${o.net_cents >= 0 ? 'pos' : ''}">${eur(o.net_cents)}</div>
        <div class="sub">Sparquote ${pct(o.savings_rate)}</div></div>
      <div class="card kpi"><div class="label">Sparen &amp; Umbuchungen</div><div class="value">${eur(o.transfers_cents)}</div><div class="sub">zählt nicht als Ausgabe</div></div>
    </div>
    <div class="card" style="margin-bottom:1rem"><h2>Einnahmen &amp; Ausgaben · ${isYear(state.month) ? state.month : '12 Monate'}</h2>${barChart(ms.months, state.month)}</div>
    ${hasData && (o.income_cents || o.expenses_cents) ? `<div class="card" style="margin-bottom:1rem"><h2>Wohin fließen die Einnahmen?</h2>${sankeyChart(o)}</div>` : ''}
    ${hasData ? `<div class="grid two">
      <div class="card"><h2>Ausgaben nach Kategorie</h2>
        ${o.categories.length ? o.categories.map(c => {
          const row = (c, child) => {
            const w = Math.min(100, Math.max(0, c.spent_cents) / maxSpent * 100);
            const proj = o.is_current && c.budget_cents ? Math.min(100, c.projected_cents / maxSpent * 100) : null;
            const bud = c.budget_cents ? `<small>von ${eur0(c.budget_cents)} · <span class="pill ${c.status}">${{ ok: 'im Plan', warn: 'knapp', over: 'überschritten', none: '' }[c.status]}</span></small>` : (c.avg3_cents ? `<small>${o.is_year ? 'Vorjahr' : 'Ø 3 Mon.'} ${eur0(c.avg3_cents)}</small>` : '');
            const toggle = !child && c.children && c.children.length ? `<button class="chev" type="button" data-toggle="${c.id}" aria-expanded="false" aria-label="Unterkategorien von ${esc(c.name)} ein-/ausklappen">›</button>` : (child ? '' : '<span class="chev-gap"></span>');
            return `<div class="cat-row${child ? ' child' : ''}" data-cat="${c.id}" ${child ? `data-parent="${child}" hidden` : ''} title="Umsätze anzeigen">
              <span class="cat-name">${toggle}<span class="dot" style="background:${esc(c.color)}"></span>${esc(c.name)}</span>
              <div class="bar"><div class="fill ${c.status === 'none' ? '' : c.status}" style="width:${w}%;${c.status === 'none' ? `background:${esc(c.color)}` : ''}"></div>${proj != null ? `<div class="proj" style="left:${proj}%" title="Prognose ${o.is_year ? 'Jahresende' : 'Monatsende'}: ${eur(c.projected_cents)}"></div>` : ''}</div>
              <span class="amt">${eur(c.spent_cents)}${bud}</span></div>`;
          };
          const rest = c.own_cents && c.children.length ? [{ id: c.id, name: 'ohne Unterkategorie', color: c.color, spent_cents: c.own_cents, budget_cents: null, projected_cents: c.own_cents, avg3_cents: 0, status: 'none' }] : [];
          return row(c, 0) + [...c.children, ...rest].map(k => row(k, c.id)).join('');
        }).join('') : '<p class="muted">Keine Ausgaben in diesem Monat.</p>'}
      </div>
      <div style="display:grid;gap:1rem;align-content:start">
        <div class="card"><h2>Verlauf im ${o.is_year ? 'Jahr' : 'Monat'}</h2>${lineChart(o)}</div>
        <div class="card"><h2>Größte Empfänger</h2>
          ${o.top_payees.map(p => `<div class="list-item"><div class="grow"><div class="title">${esc(p.payee || '–')}</div><div class="muted">${p.count}× </div></div><div class="num">${eur(p.spent_cents)}</div></div>`).join('') || '<p class="muted">–</p>'}
        </div>
        ${o.upcoming && o.upcoming.length ? `<div class="card"><h2>Noch erwartet in diesem Monat</h2>
          ${o.upcoming.map(u => `<div class="list-item"><div class="grow"><div class="title">${esc(u.payee)}</div><div class="muted">voraussichtlich ${esc(dateDe(u.next_expected))} · ${esc(u.interval)}</div></div><div class="num">${eur(u.amount_cents)}</div></div>`).join('')}
        </div>` : ''}
      </div></div>` : `<div class="card empty">Für ${esc(monthName(state.month))} liegen keine Umsätze vor.</div>`}`;
  wireToolbar(route);
  $$('[data-sk-cat]', v).forEach(g => g.onclick = () => { location.hash = `#/umsaetze?month=${state.month}&category_id=${g.dataset.skCat}`; });
  $$('.cat-row', v).forEach(r => r.onclick = e => {
    const t = e.target.closest('[data-toggle]');
    if (t) {
      const open = t.getAttribute('aria-expanded') !== 'true';
      t.setAttribute('aria-expanded', String(open));
      $$(`.cat-row[data-parent="${t.dataset.toggle}"]`, v).forEach(x => { x.hidden = !open; });
      return;
    }
    location.hash = `#/umsaetze?month=${state.month}&category_id=${r.dataset.cat}`;
  });
}

const txFilter = { q: '', category_id: '', month: null, all: false, sort: 'date', dir: 'desc' };
const sortTh = (key, label, cls = '') => {
  const on = txFilter.sort === key;
  return `<th class="${cls}" aria-sort="${on ? (txFilter.dir === 'asc' ? 'ascending' : 'descending') : 'none'}"><button type="button" class="th-sort ${on ? 'on' : ''}" data-sort="${key}">${label}<span class="arr" aria-hidden="true">${on ? (txFilter.dir === 'asc' ? '▲' : '▼') : '↕'}</span></button></th>`;
};

async function viewTransactions(params) {
  if (params.get('month')) { state.month = params.get('month'); txFilter.all = false; }
  if (params.has('category_id')) txFilter.category_id = params.get('category_id');
  const v = $('#view');
  const base = () => {
    const p = new URLSearchParams();
    if (!txFilter.all) p.set('month', state.month);
    if (state.accountId) p.set('account_id', state.accountId);
    if (txFilter.category_id) p.set('category_id', txFilter.category_id);
    if (txFilter.q) p.set('q', txFilter.q);
    return p;
  };
  v.innerHTML = monthToolbar(true, true) + `
    <div class="toolbar">
      <input type="search" id="f-q" placeholder="Suchen (Empfänger, Verwendungszweck)…" value="${esc(txFilter.q)}" class="grow-in">
      <select id="f-cat"><option value="">Alle Kategorien</option><option value="none" ${txFilter.category_id === 'none' ? 'selected' : ''}>Ohne Kategorie</option>${catOptions(txFilter.category_id)}</select>
      <label style="margin:0;display:flex;gap:.4rem;align-items:center"><input type="checkbox" id="f-all" ${txFilter.all ? 'checked' : ''} style="width:auto;margin:0"> alle Monate</label>
      <select id="f-sort" class="sort-mobile" aria-label="Sortierung">${[['date|desc', 'Datum · neueste zuerst'], ['date|asc', 'Datum · älteste zuerst'], ['amount|asc', 'Betrag · Ausgaben zuerst'], ['amount|desc', 'Betrag · Einnahmen zuerst']].map(([v, l]) => `<option value="${v}" ${v === txFilter.sort + '|' + txFilter.dir ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <span class="spacer"></span><span id="tx-sum" class="muted"></span>
      <button class="btn danger" id="tx-del" disabled title="Alle Umsätze löschen, die zu den aktuellen Filtern passen">Gefilterte löschen</button>
    </div>
    <div class="card" style="padding:.4rem .6rem"><div class="table-wrap"><table><thead><tr>${sortTh('date', 'Datum')}<th>Empfänger / Zweck</th><th>Konto</th><th>Kategorie</th>${sortTh('amount', 'Betrag', 'num')}</tr></thead><tbody id="tx-body"></tbody></table></div>
    <div class="row" style="justify-content:center;padding:.6rem"><button class="btn" id="more">Mehr laden</button></div></div>`;
  wireToolbar(() => viewTransactions(new URLSearchParams()));
  let offset = 0, lastTotal = { n: 0, sum: 0 };
  const body = $('#tx-body');
  async function load(reset) {
    if (reset) { offset = 0; body.innerHTML = ''; }
    const p = base(); p.set('limit', 100); p.set('offset', offset); p.set('sort', txFilter.sort); p.set('dir', txFilter.dir);
    const d = await api('/transactions?' + p);
    $('#tx-sum').textContent = `${d.total} Umsätze · Summe ${eur(d.sum_cents)}`;
    lastTotal = { n: d.total, sum: d.sum_cents }; $('#tx-del').disabled = !d.total;
    body.insertAdjacentHTML('beforeend', d.transactions.map(t => `<tr data-id="${t.id}">
      <td class="d">${esc(dateDe(t.booking_date))}</td>
      <td class="payee"><div class="p" title="${esc(t.payee)}">${esc(t.payee || t.booking_text || '–')}</div><div class="s" title="${esc(t.purpose)}">${esc(t.purpose)}</div></td>
      <td class="muted a">${esc(t.account_name)}</td>
      <td class="c"><select class="tx-cat" data-id="${t.id}" data-payee="${esc(t.payee || t.booking_text)}">${catOptions(t.category_id)}</select></td>
      <td class="num">${sgn(t.amount_cents)}</td></tr>`).join('') || (reset ? '<tr><td colspan="5" class="empty">Keine Umsätze gefunden.</td></tr>' : ''));
    offset += d.transactions.length;
    $('#more').classList.toggle('hidden', offset >= d.total);
  }
  $('#more').onclick = () => load(false);
  $('#tx-del').onclick = () => {
    const parts = [txFilter.all ? 'alle Monate' : monthName(state.month)];
    if (state.accountId) parts.push('Konto: ' + accountLabel());
    if (txFilter.category_id) parts.push('Kategorie: ' + (txFilter.category_id === 'none' ? 'ohne Kategorie' : (state.cats.find(c => String(c.id) === String(txFilter.category_id))?.name || '')));
    if (txFilter.q) parts.push(`Suche: „${txFilter.q}“`);
    const broad = txFilter.all && !state.accountId && !txFilter.category_id && !txFilter.q;
    const d = openDialog(`<form id="del-form"><h3>${lastTotal.n} Umsätze löschen?</h3>
      <p>Summe ${eur(lastTotal.sum)} · Filter: ${esc(parts.join(' · '))}</p>
      <p class="muted" style="font-size:.86rem">Das lässt sich nicht rückgängig machen. Ein erneuter CSV-Import oder Abruf holt die Umsätze bei Bedarf wieder.</p>
      ${broad ? '<p class="err" style="min-height:0"><b>Achtung:</b> Es sind keine Filter gesetzt – das löscht alle Umsätze aller Konten.</p><label>Zur Bestätigung „LÖSCHEN“ eingeben<input name="word" autocomplete="off" required></label>' : ''}
      <div class="actions"><button type="button" class="btn" id="d-cancel">Abbrechen</button><button class="btn danger" style="border-color:var(--bad)">Endgültig löschen</button></div></form>`);
    $('#d-cancel', d).onclick = closeDialog;
    $('#del-form', d).onsubmit = async e => {
      e.preventDefault();
      if (broad && new FormData(e.target).get('word').trim().toUpperCase() !== 'LÖSCHEN') return toast('Bitte „LÖSCHEN“ eingeben.', true);
      try {
        const x = await api(`/transactions?${base()}&expected=${lastTotal.n}`, { method: 'DELETE' });
        closeDialog(); toast(`${x.deleted} Umsätze gelöscht`); load(true);
      } catch (err) { toast(err.message, true); }
    };
  };
  let t; $('#f-q').oninput = e => { clearTimeout(t); t = setTimeout(() => { txFilter.q = e.target.value; load(true); }, 250); };
  $('#f-cat').onchange = e => { txFilter.category_id = e.target.value; load(true); };
  const syncSort = () => {
    $$('.th-sort', v).forEach(b => { const on = b.dataset.sort === txFilter.sort; b.classList.toggle('on', on);
      b.querySelector('.arr').textContent = on ? (txFilter.dir === 'asc' ? '▲' : '▼') : '↕'; b.parentElement.setAttribute('aria-sort', on ? (txFilter.dir === 'asc' ? 'ascending' : 'descending') : 'none'); });
    $('#f-sort').value = txFilter.sort + '|' + txFilter.dir;
  };
  $$('.th-sort', v).forEach(b => b.onclick = () => {
    txFilter.dir = txFilter.sort === b.dataset.sort ? (txFilter.dir === 'asc' ? 'desc' : 'asc') : (b.dataset.sort === 'date' ? 'desc' : 'asc');
    txFilter.sort = b.dataset.sort; syncSort(); load(true);
  });
  $('#f-sort').onchange = e => { [txFilter.sort, txFilter.dir] = e.target.value.split('|'); syncSort(); load(true); };
  $('#f-all').onchange = e => { txFilter.all = e.target.checked; load(true); };
  body.addEventListener('change', e => { if (e.target.classList.contains('tx-cat')) askRule(e.target); });
  await load(true);
}

function askRule(sel) {
  const id = sel.dataset.id, cat = sel.value, payee = sel.dataset.payee;
  const name = state.cats.find(c => String(c.id) === cat)?.name || '';
  const d = openDialog(`<form method="dialog" id="rule-form"><h3>Kategorie „${esc(name)}“</h3>
    <p class="muted">Soll das auch für ähnliche Umsätze gelten?</p>
    <label>Regel: Wenn dieser Text vorkommt …<input id="rule-pat" value="${esc(payee.toLowerCase())}"></label>
    <p class="muted" style="font-size:.82rem">Teilstring am Wortanfang; mit <code>re:</code> beginnend als regulärer Ausdruck.</p>
    <div class="actions"><button type="button" class="btn" id="r-one">Nur dieser Umsatz</button><button type="button" class="btn primary" id="r-rule">Regel anlegen</button></div></form>`);
  const save = async createRule => {
    try {
      const r = await api(`/transactions/${id}`, { method: 'PATCH', json: { category_id: Number(cat), create_rule: createRule, rule_pattern: $('#rule-pat', d).value } });
      closeDialog();
      toast(createRule ? `Regel angelegt · ${r.recategorized} Umsätze neu kategorisiert` : 'Kategorie gespeichert');
      if (createRule) route();
    } catch (e) { toast(e.message, true); }
  };
  $('#r-one', d).onclick = () => save(false);
  $('#r-rule', d).onclick = () => save(true);
}

async function viewBudgets() {
  const v = $('#view');
  if (isYear(state.month)) state.month = state.month === ymNow().slice(0, 4) ? ymNow() : `${state.month}-12`;   // Budgets gibt es nur je Monat
  const [o, sug] = await Promise.all([api(`/analysis/overview?month=${state.month}`), api(`/budgets/suggestions?month=${state.month}`)]);
  const spent = {};
  o.categories.forEach(c => { spent[c.id] = c; (c.children || []).forEach(k => { if (k.id !== c.id) spent[k.id] = k; }); });
  const exp = catTree(['expense']);
  v.innerHTML = monthToolbar(false) + `
    <div class="card"><div class="row" style="margin-bottom:.6rem"><h2 style="margin:0">Monatsbudgets je Kategorie</h2><span class="spacer"></span>
      <button class="btn" id="b-sug" ${sug.suggestions.length ? '' : 'disabled'}>Vorschläge übernehmen (Ø 3 Monate)</button></div>
      <p class="muted" style="margin-top:0">Gesamtbudget: <b>${eur0(o.total_budget_cents)}</b> · ausgegeben: <b>${eur0(o.expenses_cents)}</b></p>
      <div class="table-wrap"><table class="stack"><thead><tr><th>Kategorie</th><th class="num">Ø 3 Monate</th><th class="num">${esc(monthName(state.month))}</th><th class="num">Budget / Monat (€)</th><th>Status</th></tr></thead><tbody>
      ${exp.flatMap(t => [[t, false], ...t.children.map(k => [k, true])]).map(([c, child]) => { const s = spent[c.id] || {}; const b = c.budget_cents;
        const hasKids = !child && c.children.length; const eff = hasKids && !b && s.budget_cents ? s.budget_cents : null;
        const open = child && (s.spent_cents || b || (s.avg3_cents || 0));
        const chev = hasKids ? `<button class="chev" type="button" data-btoggle="${c.id}" aria-expanded="${c.children.some(k => (spent[k.id] || {}).spent_cents || k.budget_cents) ? 'true' : 'false'}" aria-label="Unterkategorien von ${esc(c.name)} ein-/ausklappen">›</button>` : '';
        return `<tr class="${child ? 'sub' : ''}" ${child ? `data-bparent="${c.parent_id}" ${open ? '' : 'hidden'}` : ''}><td class="nm">${chev}<span class="dot" style="background:${esc(c.color)}"></span><span class="nmtxt">${esc(c.name)}${hasKids ? ' <span class="muted">· inkl. Unterkategorien</span>' : ''}</span></td>
        <td class="num muted" data-l="Ø 3 Monate">${s.avg3_cents ? eur0(s.avg3_cents) : '–'}</td><td class="num" data-l="${esc(monthName(state.month))}">${eur(s.spent_cents || 0)}</td>
        <td class="num" data-l="Budget (€)"><input class="bud" type="number" min="0" step="5" data-id="${c.id}" value="${b ? (b / 100) : ''}" placeholder="${eff ? eur0(eff) + ' (Summe)' : '–'}" style="text-align:right"></td>
        <td class="st">${s.status && s.status !== 'none' ? `<span class="pill ${s.status}">${{ ok: 'im Plan', warn: 'knapp', over: 'überschritten' }[s.status]}</span>` : ''}</td></tr>`; }).join('')}
      </tbody></table></div></div>`;
  wireToolbar(viewBudgets);
  $$('[data-btoggle]', v).forEach(t => t.onclick = () => {
    const openNow = t.getAttribute('aria-expanded') !== 'true';
    t.setAttribute('aria-expanded', String(openNow));
    $$(`tr[data-bparent="${t.dataset.btoggle}"]`, v).forEach(r => { r.hidden = !openNow; });
  });
  $$('.bud', v).forEach(i => i.onchange = async () => {
    const cents = i.value === '' ? null : Math.round(parseFloat(i.value) * 100);
    try { await api(`/budgets/${i.dataset.id}`, { method: 'PUT', json: { monthly_cents: cents } }); await loadMeta(); toast('Budget gespeichert'); viewBudgets(); } catch (e) { toast(e.message, true); }
  });
  $('#b-sug').onclick = async () => {
    for (const s of sug.suggestions) await api(`/budgets/${s.category_id}`, { method: 'PUT', json: { monthly_cents: s.suggested_cents } });
    await loadMeta(); toast(`${sug.suggestions.length} Budgets gesetzt`); viewBudgets();
  };
}

async function viewRecurring() {
  const d = await api('/analysis/recurring');
  const act = d.recurring.filter(r => r.active), old = d.recurring.filter(r => !r.active);
  const item = r => `<div class="list-item"><span class="dot" style="background:${esc(r.color || '#999')}"></span>
    <div class="grow"><div class="title">${esc(r.payee)}</div><div class="muted">${esc(r.interval)} · ${r.occurrences}× · zuletzt ${esc(dateDe(r.last_date))}${r.active ? ` · nächste ≈ ${esc(dateDe(r.next_expected))}` : ''} · ${esc(r.category || '')}</div></div>
    <div class="num">${sgn(r.amount_cents)}<div class="muted" style="font-size:.78rem">${eur0(r.monthly_cents)} / Monat</div></div></div>`;
  $('#view').innerHTML = `<div class="toolbar"><h1>Fixkosten &amp; Abos</h1></div>
    <div class="grid kpis"><div class="card kpi"><div class="label">Feste Ausgaben / Monat</div><div class="value">${eur(-d.monthly_fixed_cents)}</div><div class="sub">hochgerechnet aus aktiven Verträgen</div></div>
    <div class="card kpi"><div class="label">Feste Einnahmen / Monat</div><div class="value pos">${eur(d.monthly_fixed_income_cents)}</div></div>
    <div class="card kpi"><div class="label">Verbleibt nach Fixkosten</div><div class="value">${eur(d.monthly_fixed_income_cents + d.monthly_fixed_cents)}</div></div></div>
    <div class="card"><h2>Aktiv (${act.length})</h2>${act.map(item).join('') || '<p class="muted">Noch nichts erkannt – mindestens 3 Monate Umsätze importieren.</p>'}</div>
    ${old.length ? `<div class="card" style="margin-top:1rem"><h2>Vermutlich beendet (${old.length})</h2>${old.map(item).join('')}</div>` : ''}
    <p class="muted">Erkennung automatisch: gleicher Empfänger, ähnlicher Betrag (±15 %), regelmäßiger Abstand.</p>`;
}

const syncAvailable = a => (a.bank === 'comdirect' && state.comdirectApi) || state.fintsEnabled;

async function viewAccounts() {
  await loadMeta();
  const v = $('#view');
  v.innerHTML = `<div class="toolbar"><h1>Konten</h1><span class="spacer"></span><button class="btn" id="dedupe" title="Entfernt doppelte Umsätze, die durch FinTS- plus CSV-Import entstanden sind">Duplikate bereinigen</button><button class="btn primary" id="add-acc">Konto hinzufügen</button></div>
    ${state.fintsEnabled && state.comdirectApi ? '' : `<div class="note"><b>CSV-Import funktioniert sofort.</b> Automatischer Abruf: ${state.comdirectApi ? '' : 'comdirect-API nicht aktiviert (<code>COMDIRECT_CLIENT_ID</code>/<code>COMDIRECT_CLIENT_SECRET</code>). '}${state.fintsEnabled ? '' : 'FinTS nicht aktiviert (<code>FINTS_PRODUCT_ID</code>). '}Siehe README.</div>`}
    <div class="card">${state.accounts.map(a => `<div class="list-item acc">
      <div class="grow"><div class="title">${esc(a.name)} <span class="pill">${esc(a.bank)}</span></div>
      <div class="muted">${esc(a.iban || 'keine IBAN')} · ${a.tx_count} Umsätze · ${a.last_sync ? 'letzter Abruf ' + esc(a.last_sync.replace('T', ' ')) : 'noch kein FinTS-Abruf'}</div></div>
      <span class="icons"><button class="ib" data-act="edit" data-id="${a.id}" title="Bearbeiten" aria-label="${esc(a.name)} bearbeiten">✎</button><button class="ib danger" data-act="del" data-id="${a.id}" title="Löschen" aria-label="${esc(a.name)} löschen"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V5h6v2M6.5 7l.8 12h9.4l.8-12M10 11v5M14 11v5"/></svg></button></span>
      <div class="acts"><button class="btn small" data-act="csv" data-id="${a.id}">CSV importieren</button>${a.bank === 'other' ? `<button class="btn small ghost" data-act="map" data-id="${a.id}">Spalten-Zuordnung${a.has_mapping ? '' : ' (fehlt)'}</button>` : ''}
      <button class="btn small" data-act="sync" data-id="${a.id}">${a.bank === 'comdirect' && state.comdirectApi ? 'Per comdirect-API abrufen' : 'Per FinTS abrufen'}</button></div></div>`).join('') || '<p class="empty">Noch kein Konto angelegt.</p>'}</div>
    <input type="file" id="csv-file" accept=".csv,text/csv" class="hidden">
    <div class="card" style="margin-top:1rem"><h2>So kommst du an deine Umsätze</h2>
      <p><b>ING:</b> Banking → Umsatzanzeige → Export (CSV). &nbsp; <b>comdirect:</b> Umsätze → Download → CSV.<br>Mehrfach-Import überlappender Zeiträume ist unbedenklich, Duplikate werden erkannt.</p></div>`;
  $('#add-acc').onclick = () => accountDialog();
  $('#dedupe').onclick = async () => {
    if (!confirm('Doppelte Umsätze entfernen, die durch FinTS- plus CSV-Import (leicht abweichender Text) entstanden sind? Identische Buchungen aus derselben Quelle, etwa zwei gleiche Käufe am selben Tag, bleiben erhalten.')) return;
    try { const x = await api('/dedupe', { json: {} }); await loadMeta(); toast(x.removed ? `${x.removed} Duplikate entfernt` : 'Keine Duplikate gefunden'); viewAccounts(); } catch (e) { toast(e.message, true); }
  };
  let csvFor = null, csvMapOnly = false;
  const fi = $('#csv-file');
  v.onclick = e => {
    const b = e.target.closest('button[data-act]'); if (!b) return;
    const a = state.accounts.find(x => String(x.id) === b.dataset.id);
    if (b.dataset.act === 'csv') { csvFor = a.id; csvMapOnly = false; fi.value = ''; fi.click(); }
    if (b.dataset.act === 'sync') {
      if (!syncAvailable(a)) return toast('Automatischer Abruf ist für dieses Konto nicht aktiviert (siehe Hinweis oben).', true);
      if (!a.fints_user) { toast('Bitte zuerst die Zugangsnummer / den Login-Namen eintragen.', true); return accountDialog(a); }
      syncDialog(a);
    }
    if (b.dataset.act === 'map') { csvFor = a.id; csvMapOnly = true; fi.value = ''; fi.click(); }
    if (b.dataset.act === 'edit') accountDialog(a);
    if (b.dataset.act === 'del' && confirm(`Konto „${a.name}“ mit allen ${a.tx_count} Umsätzen löschen?`)) api(`/accounts/${a.id}`, { method: 'DELETE' }).then(viewAccounts);
  };
  fi.onchange = async () => {
    const file = fi.files[0]; if (!file) return;
    const acc = state.accounts.find(x => x.id === csvFor);
    if (csvMapOnly) return mappingDialog(file, acc, done);
    const fd = new FormData(); fd.append('file', file); fd.append('account_id', csvFor);
    try { done(await api('/import', { method: 'POST', body: fd })); }
    catch (e) {
      if (e.data?.needs_mapping && acc?.bank === 'other') { toast(e.message); mappingDialog(file, acc, done); }
      else toast(e.message, true);
    }
  };
  function done(r) {
    const fmt = r.bank === 'ing' ? 'ING-Format erkannt' : r.bank === 'comdirect' ? 'comdirect-Format erkannt' : 'Spalten-Zuordnung angewendet';
    toast(`${r.added} neu, ${r.duplicates} bereits vorhanden (${fmt})` + (r.skipped_rows ? ` · ${r.skipped_rows} Zeilen übersprungen` : '') + (r.own_name_added ? ` · „${r.own_name_added}“ als eigener Name für Umbuchungen übernommen` : ''));
    loadMeta().then(viewAccounts);
  }
}

// Spalten-Zuordnung für beliebige CSV-Dateien (Kontotyp „Andere“)
const MAP_ROLES = [
  ['date', 'Buchungsdatum *'], ['payee', 'Empfänger / Auftraggeber'], ['purpose', 'Verwendungszweck'], ['purpose2', 'Verwendungszweck (2. Spalte)'],
  ['booking_text', 'Buchungstext / -art'], ['value_date', 'Valuta'], ['iban', 'IBAN Gegenseite'], ['currency', 'Währung'],
];
function mappingDialog(file, acc, onDone) {
  const small = file.slice(0, 262144);
  let info = null, overrides = {}, mapping = null, importing = false;
  const mkFd = (extra = {}) => {
    const fd = new FormData(); fd.append('file', small); fd.append('account_id', acc.id);
    fd.append('overrides', JSON.stringify(overrides));
    for (const [k, v] of Object.entries(extra)) fd.append(k, v);
    return fd;
  };
  const opt = (v, t, sel) => `<option value="${esc(v)}"${String(sel) === String(v) ? ' selected' : ''}>${esc(t)}</option>`;
  const colSel = (name, val) => `<select data-col="${name}">${opt('', '– nicht zuordnen –', val)}${info.headers.map(h => opt(h, h, val)).join('')}</select>`;
  function initMapping(keepOld) {
    const sug = info.suggested || {};
    const saved = info.saved && info.saved.date && [info.saved.date, info.saved.amount, info.saved.debit, info.saved.credit].filter(Boolean).every(c => info.headers.includes(c)) ? info.saved : null;
    const base = (keepOld && mapping && info.headers.includes(mapping.date)) ? mapping : (saved || sug);
    const p = base.purpose || [];
    mapping = { ...base, purpose: p, decimal: base.decimal || 'auto', date_format: base.date_format || sug.date_format || 'auto',
      invert: !!base.invert, amount_mode: base.amount_mode || 'single' };
  }
  function read() {
    const g = n => d.querySelector(`[data-col="${n}"]`)?.value || '';
    const m = { amount_mode: d.querySelector('[name=amount_mode]').value, decimal: d.querySelector('[name=decimal]').value,
      date_format: d.querySelector('[name=date_format]').value, invert: d.querySelector('[name=invert]').checked,
      encoding: overrides.encoding || 'auto', delimiter: overrides.delimiter || 'auto', header_row: overrides.header_row ?? info.header_row ?? 'none',
      purpose: [g('purpose'), g('purpose2')].filter(Boolean) };
    for (const f of ['date', 'payee', 'booking_text', 'value_date', 'iban', 'currency', 'amount', 'debit', 'credit', 'sign']) m[f] = g(f);
    return m;
  }
  const money = c => (c / 100).toLocaleString('de-DE', { style: 'currency', currency: 'EUR' });
  async function refreshResult() {
    const box = d.querySelector('#map-result'); mapping = read();
    d.querySelector('#amt-single').classList.toggle('hidden', mapping.amount_mode === 'split');
    d.querySelector('#amt-split').classList.toggle('hidden', mapping.amount_mode !== 'split');
    try {
      const r = (await api('/csv/preview', { method: 'POST', body: mkFd({ mapping: JSON.stringify(mapping) }) })).result;
      if (!r || r.error) { box.innerHTML = `<p class="muted">${esc(r?.error || 'Bitte Datum und Betrag zuordnen.')}</p>`; d.querySelector('#map-go').disabled = true; return; }
      box.innerHTML = `<p class="muted">${r.parsed} von ${r.rows} Zeilen erkannt${r.skipped ? `, ${r.skipped} übersprungen` : ''}${small.size < file.size ? ' (Vorschau der ersten Zeilen)' : ''}.${r.problems.length ? '<br>' + r.problems.map(esc).join('<br>') : ''}</p>
        <div class="tablewrap"><table class="tx"><tbody>${r.rows_preview.map(x => `<tr><td>${esc(x.date)}</td><td>${esc(x.payee || x.purpose).slice(0, 40)}</td><td class="num ${x.amount_cents < 0 ? 'neg' : 'pos'}">${money(x.amount_cents / 1)}</td></tr>`).join('')}</tbody></table></div>`;
      d.querySelector('#map-go').disabled = !r.parsed;
    } catch (e) { box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  }
  function render() {
    const m = mapping, ov = overrides;
    const d0 = openDialog(`<form id="map-form"><h3>Spalten zuordnen</h3>
      <p class="muted">Ordne die Spalten deiner CSV-Datei zu. Die Zuordnung wird für „${esc(acc.name)}“ gespeichert; künftige Dateien mit denselben Spaltenüberschriften werden automatisch erkannt.</p>
      <div class="grid2">
        <label>Trennzeichen<select name="delimiter">${[['auto', 'automatisch (' + info.delimiter + ')'], [';', 'Semikolon ;'], [',', 'Komma ,'], ['tab', 'Tabulator'], ['|', 'Senkrechtstrich |']].map(([v, t]) => opt(v, t, ov.delimiter || 'auto')).join('')}</select></label>
        <label>Zeichensatz<select name="encoding">${[['auto', 'automatisch'], ['utf-8', 'UTF-8'], ['cp1252', 'Windows-1252'], ['latin-1', 'Latin-1']].map(([v, t]) => opt(v, t, ov.encoding || 'auto')).join('')}</select></label>
        <label>Kopfzeile<select name="header_row">${opt('none', 'keine Kopfzeile', ov.header_row ?? info.header_row ?? 'none')}${[...Array(10).keys()].map(i => opt(i, 'Zeile ' + (i + 1), ov.header_row ?? info.header_row ?? 'none')).join('')}</select></label>
        <label>Datumsformat<select name="date_format">${[['auto', 'automatisch'], ['%d.%m.%Y', 'TT.MM.JJJJ'], ['%d.%m.%y', 'TT.MM.JJ'], ['%Y-%m-%d', 'JJJJ-MM-TT'], ['%d/%m/%Y', 'TT/MM/JJJJ'], ['%m/%d/%Y', 'MM/TT/JJJJ'], ['%d-%m-%Y', 'TT-MM-JJJJ'], ['%Y%m%d', 'JJJJMMTT']].map(([v, t]) => opt(v, t, m.date_format)).join('')}</select></label>
      </div>
      <details class="muted" style="margin:.5rem 0"><summary>Rohdaten der Datei</summary><div class="tablewrap"><table class="tx"><tbody>${info.raw_preview.map(r => `<tr>${r.map(c => `<td>${esc(c).slice(0, 24)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>
      ${MAP_ROLES.map(([k, t]) => `<label>${t}${colSel(k, k === 'purpose2' ? (m.purpose || [])[1] || '' : k === 'purpose' ? (m.purpose || [])[0] || '' : m[k] || '')}</label>`).join('')}
      <label>Betrag<select name="amount_mode">${opt('single', 'Eine Betragsspalte (mit Vorzeichen)', m.amount_mode)}${opt('split', 'Getrennt: Soll / Haben', m.amount_mode)}</select></label>
      <div id="amt-single" class="${m.amount_mode === 'split' ? 'hidden' : ''}"><label>Betrags-Spalte *${colSel('amount', m.amount || '')}</label>
        <label>Soll/Haben-Kennzeichen (optional, S/H bzw. D/C)${colSel('sign', m.sign || '')}</label></div>
      <div id="amt-split" class="${m.amount_mode === 'split' ? '' : 'hidden'}"><label>Soll / Belastung (Ausgaben)${colSel('debit', m.debit || '')}</label>
        <label>Haben / Gutschrift (Einnahmen)${colSel('credit', m.credit || '')}</label></div>
      <div class="grid2"><label>Dezimaltrenner<select name="decimal">${[['auto', 'automatisch'], ['comma', 'Komma (1.234,56)'], ['dot', 'Punkt (1,234.56)']].map(([v, t]) => opt(v, t, m.decimal)).join('')}</select></label>
        <label class="check" style="align-self:end"><input type="checkbox" name="invert"${m.invert ? ' checked' : ''}> Vorzeichen umkehren</label></div>
      <div id="map-result"></div>
      <div class="actions"><button type="button" class="btn ghost" id="map-cancel">Abbrechen</button><button type="submit" class="btn" id="map-go" disabled>Speichern &amp; importieren</button></div></form>`);
    d = d0;
    d.querySelector('#map-cancel').onclick = closeDialog;
    for (const n of ['delimiter', 'encoding', 'header_row']) d.querySelector(`[name=${n}]`).onchange = async e => {
      const v = e.target.value; if (v === 'auto') delete overrides[n]; else overrides[n] = v;
      mapping = read(); await analyse(true);
    };
    d.querySelectorAll('select:not([name=delimiter]):not([name=encoding]):not([name=header_row]), [name=invert]').forEach(el => el.onchange = refreshResult);
    d.querySelector('#map-form').onsubmit = async ev => {
      ev.preventDefault(); if (importing) return; importing = true;
      const fd = new FormData(); fd.append('file', file); fd.append('account_id', acc.id);
      fd.append('mapping', JSON.stringify(read())); fd.append('save_mapping', '1');
      try { const r = await api('/import', { method: 'POST', body: fd }); closeDialog(); onDone(r); }
      catch (e) { toast(e.message, true); importing = false; }
    };
    refreshResult();
  }
  let d;
  async function analyse(keep) {
    try {
      info = await api('/csv/preview', { method: 'POST', body: mkFd() });
      initMapping(keep); render();
    } catch (e) { toast(e.message, true); }
  }
  analyse(false);
}

function accountDialog(a) {
  const bank = a?.bank || 'ing';
  const d = openDialog(`<form id="acc-form"><h3>${a ? 'Konto bearbeiten' : 'Konto hinzufügen'}</h3>
    <label>Name<input name="name" required value="${esc(a?.name || '')}" placeholder="z. B. ING Girokonto"></label>
    ${a ? '' : `<label>Bank<select name="bank"><option value="ing">ING</option><option value="comdirect">comdirect</option><option value="other">Andere</option></select></label>`}
    <label>IBAN (optional, für automatische Zuordnung)<input name="iban" value="${esc(a?.iban || '')}"></label>
    <details style="margin-top:.8rem"><summary class="muted">FinTS-Zugang (optional)</summary>
      <label>Zugangsnummer / Login-Name (auch für die comdirect-API)<input name="fints_user" value="${esc(a?.fints_user || '')}" autocomplete="off"></label>
      <label>BLZ<input name="blz" value="${esc(a?.blz || state.presets[bank]?.blz || '')}"></label>
      <label>FinTS-URL<input name="fints_url" value="${esc(a?.fints_url || state.presets[bank]?.url || '')}"></label>
      <label>TAN-Verfahren (Code, leer = automatisch)<input name="fints_tan_mechanism" value="${esc(a?.fints_tan_mechanism || '')}"></label>
      <p class="muted" style="font-size:.8rem">Die PIN wird nie gespeichert – sie wird bei jedem Abruf neu abgefragt.</p></details>
    <div class="actions"><button type="button" class="btn" id="a-cancel">Abbrechen</button><button class="btn primary">Speichern</button></div></form>`);
  const sel = $('select[name=bank]', d);
  if (sel) sel.onchange = () => { $('[name=blz]', d).value = state.presets[sel.value]?.blz || ''; $('[name=fints_url]', d).value = state.presets[sel.value]?.url || ''; };
  $('#a-cancel', d).onclick = closeDialog;
  $('#acc-form', d).onsubmit = async e => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    try {
      if (a) await api(`/accounts/${a.id}`, { method: 'PATCH', json: body }); else await api('/accounts', { json: body });
      closeDialog(); viewAccounts();
    } catch (err) { toast(err.message, true); }
  };
}

function syncDialog(a) {
  const d = openDialog(`<form id="sync-form"><h3>${esc(a.name)} abrufen</h3>
    <label>Online-Banking-PIN<input type="password" name="pin" autocomplete="off" required></label>
    <label>Zeitraum<select name="days"><option value="30">letzte 30 Tage</option><option value="90" selected>letzte 90 Tage</option><option value="360">letzte 12 Monate</option></select></label>
    <p class="muted" style="font-size:.82rem">Die PIN wird nur für diesen Abruf im Arbeitsspeicher gehalten.</p>
    <div class="actions"><button type="button" class="btn" id="s-cancel">Abbrechen</button><button class="btn primary">Abrufen</button></div></form>`);
  $('#s-cancel', d).onclick = closeDialog;
  $('#sync-form', d).onsubmit = async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const job = await api(`/accounts/${a.id}/sync`, { json: { pin: f.get('pin'), days: Number(f.get('days')) } });
      pollSync(job.id, a);
    } catch (err) { toast(err.message, true); }
  };
}

async function pollSync(jobId, a) {
  let j;
  try { j = await api(`/sync/${jobId}`); } catch (e) { return toast(e.message, true); }
  const d = $('#dlg');
  const head = `<h3>${esc(a.name)}</h3>`;
  if (j.state === 'running') {
    d.innerHTML = `${head}<p><span class="spinner"></span>${esc(j.message)}</p>`;
    return setTimeout(() => pollSync(jobId, a), 1500);
  }
  if (j.state === 'need_tan' || j.state === 'need_approval') {
    const tan = j.state === 'need_tan';
    d.innerHTML = `<form id="tan-form">${head}<p>${esc(j.message)}</p>${j.challenge ? `<p class="muted">${esc(j.challenge)}</p>` : ''}${j.challenge_image && j.challenge_image.startsWith('data:image/png;base64,') ? `<p><img alt="photoTAN" src="${j.challenge_image}" style="max-width:100%;image-rendering:pixelated"></p>` : ''}
      ${tan ? '<label>TAN<input name="tan" autocomplete="one-time-code" inputmode="numeric" required autofocus></label>' : ''}
      <div class="actions"><button class="btn primary">${tan ? 'TAN senden' : 'Freigabe erfolgt'}</button></div></form>`;
    $('#tan-form', d).onsubmit = async e => {
      e.preventDefault();
      try { await api(`/sync/${jobId}/tan`, { json: { tan: tan ? new FormData(e.target).get('tan') : '' } }); } catch (err) { toast(err.message, true); }
      d.innerHTML = `${head}<p><span class="spinner"></span>Warte auf die Bank …</p>`;
      setTimeout(() => pollSync(jobId, a), 1000);
    };
    return;
  }
  if (j.state === 'done') {
    d.innerHTML = `${head}<p>✅ ${j.result.added} neue Umsätze, ${j.result.duplicates} bereits vorhanden.</p><div class="actions"><button class="btn primary" id="s-ok">Schließen</button></div>`;
    $('#s-ok', d).onclick = () => { closeDialog(); viewAccounts(); };
    return;
  }
  d.innerHTML = `${head}<p class="err">${esc(j.message)}</p><div class="actions"><button class="btn" id="s-ok">Schließen</button></div>`;
  $('#s-ok', d).onclick = closeDialog;
}

async function viewRules() {
  const [r, s] = await Promise.all([api('/rules'), api('/settings')]);
  const v = $('#view');
  v.innerHTML = `<div class="toolbar"><h1>Kategorisierung</h1><span class="spacer"></span><button class="btn" id="reapply">Alle neu kategorisieren</button></div>
    <div class="grid two">
    <div class="card"><h2>Eigene Regel hinzufügen</h2>
      <form id="rule-add"><label>Text enthält …<input name="pattern" required placeholder="z. B. netflix  oder  re:\\bdb\\b"></label>
      <label>Kategorie<select name="category_id">${catOptions('')}</select></label>
      <label>Richtung<select name="direction"><option value="any">Einnahmen &amp; Ausgaben</option><option value="out">nur Ausgaben</option><option value="in">nur Einnahmen</option></select></label>
      <div class="actions"><button class="btn primary">Regel speichern</button></div></form>
      <p class="muted" style="font-size:.82rem">Eigene Regeln haben Vorrang vor den ${r.default_rule_count} Standardregeln. Gesucht wird in Empfänger, Verwendungszweck und Buchungstext (Teilstring am Wortanfang).</p></div>
    <div class="card"><h2>Eigene Namen (Umbuchungen erkennen)</h2>
      <p class="muted" style="margin-top:0">Überweisungen an dich selbst (z. B. ING → comdirect) sollen weder Ausgaben noch Einnahmen sein. Namen kommagetrennt:</p>
      <input id="own-names" value="${esc(s.own_names)}" placeholder="Max Mustermann, M. Mustermann" style="width:100%">
      <div class="actions"><button class="btn" id="own-save">Speichern</button></div></div></div>
    <div class="card" style="margin-top:1rem"><div class="row" style="margin-bottom:.4rem"><h2 style="margin:0">Kategorien &amp; Unterkategorien</h2><span class="spacer"></span>
      <button class="btn" id="cat-tpl">Vorlage anwenden</button><button class="btn primary" id="cat-add">Neue Kategorie</button></div>
      <p class="muted" style="margin-top:0;font-size:.86rem">Unterkategorien sind optional. Eine Oberkategorie zählt ihre eigenen Umsätze plus alle Unterkategorien zusammen (Übersicht, Diagramme, Budget).</p>
      ${catTree().map(t => `<div class="cat-mgmt"><div class="list-item"><span class="dot" style="background:${esc(t.color)}"></span><div class="grow"><b>${esc(t.name)}</b> <span class="muted">${KIND_LABEL[t.kind]}${t.children.length ? ` · ${t.children.length} Unterkategorien` : ''}</span></div>
        <div class="icons"><button class="ib" data-cat-sub="${t.id}" title="Unterkategorie hinzufügen" aria-label="Unterkategorie zu ${esc(t.name)} hinzufügen">＋</button><button class="ib" data-cat-edit="${t.id}" title="Bearbeiten" aria-label="${esc(t.name)} bearbeiten">✎</button><button class="ib danger" data-cat-del="${t.id}" title="Löschen" aria-label="${esc(t.name)} löschen"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V5h6v2M6.5 7l.8 12h9.4l.8-12M10 11v5M14 11v5"/></svg></button></div></div>
        ${t.children.map(k => `<div class="list-item child-item"><div class="grow">${esc(k.name)}</div><div class="icons"><button class="ib" data-cat-edit="${k.id}" title="Bearbeiten" aria-label="${esc(k.name)} bearbeiten">✎</button><button class="ib danger" data-cat-del="${k.id}" title="Löschen" aria-label="${esc(k.name)} löschen"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V5h6v2M6.5 7l.8 12h9.4l.8-12M10 11v5M14 11v5"/></svg></button></div></div>`).join('')}</div>`).join('')}
    </div>
    <div class="card" style="margin-top:1rem"><h2>Deine Regeln (${r.rules.length})</h2>
      ${r.rules.map(x => `<div class="list-item"><div class="grow"><code>${esc(x.pattern)}</code> → <b>${esc(x.category)}</b> <span class="muted">(${{ any: 'alle', in: 'Einnahmen', out: 'Ausgaben' }[x.direction]})</span></div>
      <button class="btn small ghost danger" data-del="${x.id}">Löschen</button></div>`).join('') || '<p class="muted">Noch keine eigenen Regeln. Tipp: In der Umsatzliste eine Kategorie ändern und „Regel anlegen“ wählen.</p>'}</div>`;
  $('#rule-add').onsubmit = async e => {
    e.preventDefault();
    const b = Object.fromEntries(new FormData(e.target)); b.category_id = Number(b.category_id);
    try { const x = await api('/rules', { json: b }); toast(`Regel gespeichert · ${x.recategorized} Umsätze geändert`); viewRules(); } catch (err) { toast(err.message, true); }
  };
  $('#own-save').onclick = async () => { const x = await api('/settings', { method: 'PUT', json: { own_names: $('#own-names').value } }); toast(`Gespeichert · ${x.recategorized} Umsätze geändert`); };
  $('#reapply').onclick = async () => { const x = await api('/rules/apply', { json: {} }); toast(`${x.recategorized} Umsätze neu kategorisiert`); };
  $('#cat-add').onclick = () => categoryDialog();
  $('#cat-tpl').onclick = async () => {
    if (!confirm('Die Standardkategorien werden in die Vorlage mit Ober- und Unterkategorien überführt (u. a. „Lebensmittel“ → „Lebensmittel / Getränke“ unter „Lebenshaltung“). Umsätze, Budgets und eigene Regeln bleiben erhalten. Fortfahren?')) return;
    try { const x = await api('/categories/template', { json: {} }); await loadMeta(); toast(`Vorlage angewendet · ${x.created} neu, ${x.renamed} umbenannt`); viewRules(); } catch (err) { toast(err.message, true); }
  };
  v.onclick = async e => {
    const b = e.target.closest('[data-del]');
    if (b) { await api(`/rules/${b.dataset.del}`, { method: 'DELETE' }); return viewRules(); }
    const sub = e.target.closest('[data-cat-sub]'), ed = e.target.closest('[data-cat-edit]'), del = e.target.closest('[data-cat-del]');
    if (sub) categoryDialog(null, Number(sub.dataset.catSub));
    if (ed) categoryDialog(state.cats.find(c => String(c.id) === ed.dataset.catEdit));
    if (del) {
      const c = state.cats.find(x => String(x.id) === del.dataset.catDel);
      if (confirm(`Kategorie „${c.name}“ löschen? Ihre Umsätze werden neu zugeordnet; Unterkategorien rücken auf die oberste Ebene.`)) {
        await api(`/categories/${c.id}`, { method: 'DELETE' }); await loadMeta(); toast('Kategorie gelöscht'); viewRules();
      }
    }
  };
}

function categoryDialog(c = null, parentId = null) {
  const tops = catTree().filter(t => !c || t.id !== c.id);
  const hasKids = c && state.cats.some(x => x.parent_id === c.id);
  const parent = c ? c.parent_id : parentId;
  const d = openDialog(`<form id="cat-form"><h3>${c ? 'Kategorie bearbeiten' : (parentId ? 'Neue Unterkategorie' : 'Neue Kategorie')}</h3>
    <label>Name<input name="name" value="${esc(c?.name || '')}" required autocomplete="off"></label>
    <label>Oberkategorie (optional)<select name="parent_id" ${hasKids ? 'disabled' : ''}><option value="">– keine (oberste Ebene) –</option>${tops.filter(t => !c || t.kind === c.kind).map(t => `<option value="${t.id}" ${t.id === parent ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select></label>
    ${c ? '' : `<label>Art (bei Oberkategorie)<select name="kind"><option value="expense">Ausgabe</option><option value="income">Einnahme</option><option value="transfer">Umbuchung / Sparen</option></select></label>`}
    <label>Farbe<input type="color" name="color" value="${esc(c?.color || '#4f7cff')}" style="height:44px;padding:.2rem"></label>
    <div class="actions"><button type="button" class="btn" id="c-cancel">Abbrechen</button><button class="btn primary">Speichern</button></div></form>`);
  $('#c-cancel', d).onclick = closeDialog;
  $('#cat-form', d).onsubmit = async e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const body = { name: f.name, color: f.color };
    if (!hasKids) body.parent_id = f.parent_id ? Number(f.parent_id) : null;
    try {
      if (c) await api(`/categories/${c.id}`, { method: 'PATCH', json: body });
      else await api('/categories', { json: { ...body, kind: f.kind || 'expense' } });
      closeDialog(); await loadMeta(); toast('Gespeichert'); viewRules();
    } catch (err) { toast(err.message, true); }
  };
}

// ---------------------------------------------------------------- Router / Login
const routes = { uebersicht: viewOverview, umsaetze: viewTransactions, budgets: viewBudgets, fixkosten: viewRecurring, konten: viewAccounts, regeln: viewRules };

async function route() {
  const [path, qs] = (location.hash.replace(/^#\/?/, '') || 'uebersicht').split('?');
  const name = routes[path] ? path : 'uebersicht';
  $$('#nav a').forEach(a => { const on = a.dataset.r === name; a.classList.toggle('active', on); if (on) $('#page-title').textContent = a.textContent.replace(/^\S\s?/, ''); });
  setMenu(false);
  try {
    if (!state.cats.length) await loadMeta();
    await routes[name](new URLSearchParams(qs || ''));
  } catch (e) {
    if (e.message !== 'Nicht angemeldet') $('#view').innerHTML = `<div class="card"><p class="err">${esc(e.message)}</p></div>`;
  }
}

function showLogin() { $('#app').classList.add('hidden'); $('#login').classList.remove('hidden'); $('#login-pw').focus(); }
async function showApp() { $('#login').classList.add('hidden'); $('#app').classList.remove('hidden'); await loadMeta(); route(); }

$('#login-form').onsubmit = async e => {
  e.preventDefault();
  try { await api('/login', { json: { password: $('#login-pw').value } }); $('#login-pw').value = ''; $('#login-err').textContent = ''; showApp(); }
  catch (err) { $('#login-err').textContent = err.message; }
};
const doLogout = async () => { await api('/logout', { json: {} }); state.cats = []; setMenu(false); showLogin(); };
$('#logout').onclick = doLogout; $('#logout2').onclick = doLogout;

// Burger-Menü (mobil: Schublade von links)
function setMenu(open) {
  document.body.classList.toggle('menu-open', open);
  $('#burger').setAttribute('aria-expanded', String(open));
  $('#burger').setAttribute('aria-label', open ? 'Menü schließen' : 'Menü öffnen');
}
$('#burger').onclick = () => setMenu(!document.body.classList.contains('menu-open'));
$('#scrim').onclick = () => setMenu(false);
document.addEventListener('keydown', e => { if (e.key === 'Escape') setMenu(false); });
let lastNarrow = innerWidth < 640, rz;
window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => {
  const n = innerWidth < 640; if (n !== lastNarrow) { lastNarrow = n; setMenu(false); if (!$('#app').classList.contains('hidden') && !document.querySelector('dialog[open]')) route(); }
}, 200); });
window.addEventListener('hashchange', route);
(async () => {
  const s = await fetch('api/session').then(r => r.json());
  if (s.ingress) { $('#logout').classList.add('hidden'); $('#logout2').classList.add('hidden'); }   // Anmeldung übernimmt Home Assistant
  s.authenticated ? showApp() : showLogin();
})();
