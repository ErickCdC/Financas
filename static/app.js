"use strict";
/* Finanças — interface. Todo texto vindo do servidor passa por esc() antes de ir para o HTML. */

// ---------- utilidades ----------
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const CUR = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const CUR0 = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const brl = v => CUR.format(v || 0);
const brl0 = v => CUR0.format(Math.round(v || 0));
const icon = n => `<svg class="i"><use href="#i-${n}"/></svg>`;
const MES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
const DSEM = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];
const DSEM_LONG = ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"];
const parse = iso => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
const ddmm = iso => iso.slice(8, 10) + "/" + iso.slice(5, 7);
const daysBetween = (a, b) => Math.round((parse(b) - parse(a)) / 86400000);
const monthName = ym => { const [y, m] = ym.split("-").map(Number); return `${MES[m - 1]} ${y}`; };
const addMonths = (ym, n) => { const [y, m] = ym.split("-").map(Number); const t = y * 12 + (m - 1) + n; return `${Math.floor(t / 12)}-${String(t % 12 + 1).padStart(2, "0")}`; };
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* sem armazenamento */ } },
};
const CATS = [["mercado", "cart"], ["comida", "food"], ["transporte", "car"], ["casa", "house"], ["lazer", "sparkle"], ["compras", "tag"], ["saude", "tag"], ["outros", "tag"]];
const CAT_ICON_EXTRA = { estornado: "refresh" };
function parseMoney(v) {
  let s = String(v ?? "").trim().replace(/\s|R\$/g, "");
  if (s === "") return null;
  if (s.includes(",")) s = s.replace(/\./g, "").replace(",", ".");
  else if (!/^-?\d+\.\d{1,2}$/.test(s)) s = s.replace(/\./g, "");
  const n = Number(s);
  return isNaN(n) ? NaN : n;
}
const catIcon = c => CAT_ICON_EXTRA[c] || (CATS.find(x => x[0] === String(c || "").toLowerCase()) || [0, "tag"])[1];

const NAV = [
  ["inicio", "Início", "home"], ["contas", "Contas", "bills"], ["gastos", "Gastos", "wallet"],
  ["plano", "Planejamento", "chart"], ["ajustes", "Ajustes", "settings"],
];
const S = { view: "inicio", month: null, d: null, planTab: "proj", filter: "semana", simTeto: null, offline: false, installEvt: null, pushOn: false };

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.classList.add("on");
  clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("on"), 3000);
}
const buzz = () => { try { navigator.vibrate && navigator.vibrate(12); } catch (_) { /* opcional */ } };

// ---------- API ----------
async function api(path, body) {
  const opt = { credentials: "same-origin" };
  if (body !== undefined) Object.assign(opt, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let r;
  try { r = await fetch(path, opt); } catch (_) { throw new Error("Sem conexão com o servidor"); }
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("Faça login"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || "Erro " + r.status);
  return j;
}
async function cacheSummary(d) {
  try { const c = await caches.open("fin-data"); await c.put("/api/summary", new Response(JSON.stringify(d), { headers: { "Content-Type": "application/json" } })); } catch (_) { /* opcional */ }
}
async function cachedSummary() {
  try { const c = await caches.open("fin-data"); const r = await c.match("/api/summary"); return r ? r.json() : null; } catch (_) { return null; }
}

async function load({ silent = false } = {}) {
  const btn = $("#refreshbtn"); btn.classList.add("spin");
  const slow = setTimeout(() => { const b = $("#bootmsg"); if (b) b.textContent = "Acordando o servidor… no plano grátis isso leva até 1 minuto."; }, 3500);
  try {
    const d = await api("/api/summary" + (S.month ? "?month=" + S.month : ""));
    S.d = d; S.month = d.mes.ym; S.offline = false;
    if (!location.search.includes("month")) cacheSummary(d);
    render();
    syncPush();
  } catch (e) {
    if (e.message === "Faça login") return;
    if (!S.d) { const c = await cachedSummary(); if (c) { S.d = c; S.month = c.mes.ym; S.offline = true; render(); } }
    else if (!silent) toast(e.message);
    if (S.d) { S.offline = true; $("#offline").hidden = false; }
  } finally { clearTimeout(slow); btn.classList.remove("spin"); }
}

function showLogin() { $("#login").hidden = false; setTimeout(() => $("#pw").focus(), 50); }

// ---------- navegação ----------
function route() {
  const [v, sub] = (location.hash.replace("#", "") || "inicio").split("/");
  S.view = NAV.some(n => n[0] === v) ? v : "inicio";
  if (S.d) render();
  window.scrollTo(0, 0);
  if (sub === "novo" && S.d) { history.replaceState(null, "", "#" + S.view); A.addspend(); }
}
window.addEventListener("hashchange", route);

function render() {
  if (!S.d) return;
  const navHtml = NAV.map(([k, label, ic]) => `<a href="#${k}" class="${S.view === k ? "on" : ""}" ${S.view === k ? 'aria-current="page"' : ""}><span class="pill">${icon(ic)}</span><span>${label}</span></a>`).join("");
  $("#bottomnav").innerHTML = navHtml;
  $("#sidenav").innerHTML = NAV.map(([k, label, ic]) => `<a href="#${k}" class="${S.view === k ? "on" : ""}">${icon(ic)}<span>${label}</span></a>`).join("");
  $("#sidefoot").innerHTML = S.d.config.last_sync ? `Banco sincronizado ${esc(S.d.config.last_sync.slice(8, 10) + "/" + S.d.config.last_sync.slice(5, 7) + " " + S.d.config.last_sync.slice(11))}` : "Uso pessoal";
  const title = NAV.find(n => n[0] === S.view)[1];
  $("#pagetitle").textContent = S.view === "inicio" ? "" : title;
  $("#topbar").dataset.home = S.view === "inicio" ? "1" : "0";
  document.title = S.view === "inicio" ? "Finanças" : `${title} · Finanças`;
  $("#offline").hidden = !S.offline;
  $("#fab").hidden = !["inicio", "gastos"].includes(S.view);
  const v = $("#view");
  v.classList.remove("view"); void v.offsetWidth; v.classList.add("view");
  v.innerHTML = VIEWS[S.view](S.d) + `<p class="foot">Estimativa pessoal · confira sempre com o extrato</p>`;
  applySizes(document);
}

function applySizes(root) {
  root.querySelectorAll("[data-w]").forEach(el => { el.style.width = Math.max(0, Math.min(100, Number(el.dataset.w))) + "%"; });
  root.querySelectorAll("[data-h]").forEach(el => { el.style.height = Math.max(3, Math.min(100, Number(el.dataset.h))) + "%"; });
}

// ---------- componentes ----------
function dueChip(i, today) {
  if (i.paid) return `<span class="chip pos">${icon("check")}pago</span>`;
  if (!i.due) return "";
  const n = daysBetween(today, i.due);
  if (n < 0) return `<span class="chip neg">atrasada ${-n}d</span>`;
  if (n === 0) return `<span class="chip warn">vence hoje</span>`;
  if (n === 1) return `<span class="chip warn">vence amanhã</span>`;
  if (n <= 5) return `<span class="chip blue">em ${n} dias</span>`;
  return `<span class="chip">dia ${i.due.slice(8, 10)}</span>`;
}
function relDay(iso, today) {
  const n = daysBetween(iso, today);
  if (n === 0) return "Hoje";
  if (n === 1) return "Ontem";
  const dt = parse(iso);
  return `${DSEM_LONG[dt.getDay()][0].toUpperCase() + DSEM_LONG[dt.getDay()].slice(1)}, ${ddmm(iso)}`;
}
function spendRow(s) {
  return `<button class="item ${s.ignored ? "off" : ""}" data-act="editspend" data-id="${s.id}">
    <span class="ic">${icon(catIcon(s.category))}</span>
    <span class="body"><span class="t">${esc(s.description)}</span>
      <span class="sub">${esc(s.category || "sem categoria")}${s.source === "banco" ? " · banco" : ""}${s.ignored && s.category !== "estornado" ? " · ignorado" : ""}</span></span>
    <span class="amt num ${s.amount < 0 ? "pos" : ""}">${brl(s.amount)}</span></button>`;
}
function weekBars(w) {
  const max = Math.max(...w.dias.map(d => d.total), w.teto / 7, 1);
  return `<div class="daybars">${w.dias.map(d => {
    const cls = d.date === w.hoje ? "today" : d.date > w.hoje ? "future" : "";
    return `<button type="button" class="d ${cls}" data-act="day" data-date="${d.date}" aria-label="${DSEM_LONG[parse(d.date).getDay()]} ${ddmm(d.date)}: ${brl(d.total)}"><div class="b" data-h="${(d.total / max) * 100}"></div><div class="l">${DSEM[parse(d.date).getDay()]}</div></button>`;
  }).join("")}</div>`;
}
function notice(kind, ic, html, action) {
  return `<div class="notice ${kind}">${icon(ic)}<div class="body">${html}${action || ""}</div></div>`;
}

// ---------- telas ----------
const VIEWS = {};

VIEWS.inicio = d => {
  const w = d.semana, t = d.hoje, dt = parse(t);
  const over = w.gasto > w.teto;
  const pct = w.teto ? (w.gasto / w.teto) * 100 : 0;
  const m0 = d.proj.months[0];
  const nextPay = daysBetween(t, w.proximo_salario);
  const alerts = [];
  if (d.config.caixa_desatualizado) alerts.push(notice("warn", "alert", `<b>Atualize seu caixa.</b> O saldo informado é de outro mês, então a projeção está sem ele.`, `<div><a class="btn sm secondary" href="#ajustes">Atualizar caixa</a></div>`));
  if (!S.pushOn && "Notification" in window && Notification.permission !== "denied") alerts.push(notice("", "bell", `<b>Ative as notificações</b> para receber lembretes de contas e do teto da semana.`, `<div><button class="btn sm" data-act="pushon">Ativar neste aparelho</button></div>`));
  if (!d.backup.ativo) alerts.push(notice("warn", "cloud", `<b>Backup automático desligado.</b> No Render grátis os dados podem sumir quando o servidor reinicia.`, `<div><a class="btn sm secondary" href="#ajustes">Ver como ligar</a></div>`));
  else if (d.backup.pausado) alerts.push(notice("warn", "cloud", `<b>Backup pausado:</b> ${esc(d.backup.erro || "erro ao ler o GitHub")}. Evite editar até voltar.`));

  const venc = d.vencimentos.slice(0, 5);
  const recentes = w.lista.filter(s => !s.ignored).slice(0, 5);
  return `
  <div class="page-head"><div class="eyebrow">${DSEM_LONG[dt.getDay()]}, ${dt.getDate()} de ${MES[dt.getMonth()]}</div><h1>Olá! Aqui está seu mês</h1></div>
  <div class="grid dash">
    <div class="stack">
      <section class="card hero" aria-label="Teto da semana">
        <div class="label">${over ? "Passou do teto da semana em" : "Você ainda pode gastar esta semana"}</div>
        <div class="big num">${brl(over ? w.estourou : w.restante)}</div>
        <div class="meta">${brl(w.gasto)} de ${brl(w.teto)} · semana ${ddmm(w.inicio)} a ${ddmm(w.fim)}</div>
        <div class="bar ${over ? "over" : ""}"><i data-w="${pct}"></i></div>
        <div class="meta">${over ? "Segure os gastos até domingo." : `≈ ${brl(w.por_dia_restante)} por dia até domingo (${w.dias_restantes} ${w.dias_restantes === 1 ? "dia" : "dias"})`}</div>
        <div class="hero-actions"><button class="btn light sm" data-act="addspend">${icon("plus")}Lançar gasto</button><a class="btn glass sm" href="#gastos">Ver gastos</a></div>
      </section>
      ${alerts.length ? `<div class="stack">${alerts.join("")}</div>` : ""}
      <div class="grid two">
        <a class="card stat" href="#contas"><div class="k">${icon("bills")}Falta pagar</div><div class="v num">${brl0(d.mes.falta)}</div><div class="s">${esc(monthName(d.mes.ym))}</div></a>
        <a class="card stat" href="#plano"><div class="k">${icon("piggy")}Caixinha prevista</div><div class="v num ${m0.caixinha < 0 ? "neg" : ""}">${brl0(m0.caixinha)}</div><div class="s">${esc(m0.label)} · teto ${brl0(d.config.teto_semanal)}/sem</div></a>
        <div class="card stat"><div class="k">${icon("wallet")}Desde o salário</div><div class="v num">${brl0(w.desde_total)}</div><div class="s">desde ${ddmm(w.desde)}</div></div>
        <div class="card stat"><div class="k">${icon("calendar")}Próximo salário</div><div class="v num">${ddmm(w.proximo_salario)}</div><div class="s">${nextPay <= 0 ? "hoje" : `em ${nextPay} ${nextPay === 1 ? "dia" : "dias"}`}</div></div>
      </div>
    </div>
    <div class="stack">
      <section class="card"><div class="card-head"><h3>Gastos da semana</h3><span class="chip ${over ? "neg" : pct > 80 ? "warn" : "blue"}">${Math.round(pct)}% do teto</span></div>${weekBars(w)}<div class="tap-hint">Toque num dia para ver os gastos</div></section>
      <section class="card flush"><div class="card-head"><h3>Próximos vencimentos</h3></div>
        <div class="list">${venc.length ? venc.map(i => `<a class="item tap" href="#contas"><span class="ic ${i.group === "fatura" ? "blue" : ""}">${icon(i.group === "fatura" ? "card" : "bills")}</span>
          <span class="body"><span class="t">${esc(i.name)}</span><span class="sub">${ddmm(i.due)}${i.estimated ? " · previsto" : ""}</span></span>
          <span class="amt num">${brl(i.amount)}</span>${dueChip(i, t)}</a>`).join("") : `<div class="empty">${icon("check")}<div>Nada vencendo nos próximos dias.</div></div>`}</div>
      </section>
      <section class="card flush"><div class="card-head"><h3>Últimos lançamentos</h3><a class="btn ghost sm" href="#gastos">Ver todos</a></div>
        <div class="list">${recentes.length ? recentes.map(spendRow).join("") : `<div class="empty">${icon("wallet")}<div>Nenhum gasto lançado.</div></div>`}</div>
      </section>
    </div>
  </div>`;
};

VIEWS.contas = d => {
  const m = d.mes, t = d.hoje;
  const pct = m.total ? (m.pago / m.total) * 100 : 0;
  const group = (title, g) => {
    const its = m.items.filter(i => i.group === g);
    if (!its.length) return "";
    return `<div class="group-label">${title}</div>` + its.map(i => `<div class="item">
      <input type="checkbox" class="check" data-chg="paid" data-key="${esc(i.key)}" ${i.paid ? "checked" : ""} aria-label="Marcar ${esc(i.name)} como paga">
      <div class="body"><div class="t">${esc(i.name)}</div>
        <div class="sub">${dueChip(i, t)}${i.estimated && i.amount != null ? ` <span class="chip">previsto</span>` : ""}${i.used != null && !i.paid ? ` <span class="muted">· usado no cartão ${brl(i.used)}</span>` : ""}</div>
        ${i.parts && i.parts.length ? `<details class="parts"><summary>Ver composição (${i.parts.length})</summary><div class="pl">${i.parts.map(p => `<div><span>${esc(p.name)}</span><span class="num">${brl(p.amount)}</span></div>`).join("")}</div></details>` : ""}
      </div>
      <button class="btn ghost sm num" data-act="editamt" data-key="${esc(i.key)}" data-name="${esc(i.name)}" data-amount="${i.amount ?? ""}" data-est="${i.estimated ? 1 : 0}"><span class="${i.estimated ? "est" : ""}">${i.amount == null ? "informar" : brl(i.amount)}</span></button>
    </div>`).join("");
  };
  return `
  <div class="monthnav"><button class="iconbtn" data-act="mnav" data-d="-1" aria-label="Mês anterior">${icon("left")}</button>
    <div class="m">${esc(monthName(m.ym))}</div><button class="iconbtn" data-act="mnav" data-d="1" aria-label="Próximo mês">${icon("right")}</button></div>
  <div class="grid cols2">
    <div class="stack">
      <section class="card">
        <div class="eyebrow">Falta pagar</div>
        <div class="num"><span class="v-big">${brl(m.falta)}</span></div>
        <div class="bar ok"><i data-w="${pct}"></i></div>
        <div class="lead">Pago ${brl(m.pago)} de ${brl(m.total)}${m.sem_valor ? ` · ${m.sem_valor} sem valor` : ""}</div>
      </section>
      ${notice("", "info", "Valores com <b>previsto</b> são estimativas. Toque no valor para informar o real da fatura.")}
    </div>
    <section class="card flush"><div class="list">${group("Contas fixas", "fixa")}${group("Faturas dos cartões", "fatura")}</div></section>
  </div>`;
};

VIEWS.gastos = d => {
  const w = d.semana, t = d.hoje;
  const over = w.gasto > w.teto, pct = w.teto ? (w.gasto / w.teto) * 100 : 0;
  const since30 = (() => { const x = parse(t); x.setDate(x.getDate() - 30); return x.toISOString().slice(0, 10); })();
  const filt = { semana: s => s.date >= w.inicio && s.date <= w.fim, salario: s => s.date >= w.desde, "30d": s => s.date >= since30 }[S.filter];
  const list = w.lista.filter(filt);
  const total = list.filter(s => !s.ignored).reduce((a, s) => a + s.amount, 0);
  const byDay = {};
  list.forEach(s => (byDay[s.date] = byDay[s.date] || []).push(s));
  const maxCat = Math.max(...w.categorias.map(c => c.total), 1);
  return `
  <div class="grid cols2">
    <div class="stack">
      <section class="card">
        <div class="card-head"><div><div class="eyebrow">Semana ${ddmm(w.inicio)} – ${ddmm(w.fim)}</div><h3 class="num">${over ? `Passou ${brl(w.estourou)}` : `Restam ${brl(w.restante)}`}</h3></div>
          <span class="chip ${over ? "neg" : pct > 80 ? "warn" : "blue"}">${Math.round(pct)}%</span></div>
        <div class="bar ${over ? "over" : pct > 80 ? "warn" : ""}"><i data-w="${pct}"></i></div>
        <div class="lead num">${brl(w.gasto)} de ${brl(w.teto)} · semana passada ${brl(w.semana_passada)}</div>
        ${weekBars(w)}
      </section>
      <section class="card"><div class="card-head"><h3>Por categoria</h3><span class="muted num">desde ${ddmm(w.desde)}</span></div>
        ${w.categorias.length ? w.categorias.map(c => `<button type="button" class="catrow" data-act="cat" data-cat="${esc(c.cat)}"><span class="n">${esc(c.cat)}</span><div class="bar"><i data-w="${(c.total / maxCat) * 100}"></i></div><span class="num">${brl0(c.total)}</span></button>`).join("") : `<div class="muted">Sem gastos ainda.</div>`}
      </section>
    </div>
    <section class="card flush">
      <div class="card-head"><div class="seg" role="tablist">${[["semana", "Semana"], ["salario", "Desde o salário"], ["30d", "30 dias"]].map(([k, l]) => `<button data-act="filter" data-f="${k}" class="${S.filter === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <div class="kv inset"><span class="muted">${list.length} lançamento(s)</span><b class="num">${brl(total)}</b></div>
      <div class="list">${Object.keys(byDay).length ? Object.entries(byDay).map(([day, items]) => `<div class="group-label">${relDay(day, t)} · <span class="num">${brl(items.filter(s => !s.ignored).reduce((a, s) => a + s.amount, 0))}</span></div>${items.map(spendRow).join("")}`).join("") : `<div class="empty">${icon("wallet")}<div>Nenhum lançamento neste período.</div></div>`}</div>
    </section>
  </div>`;
};

function projChart(months, simTeto, teto) {
  const vals = months.map(m => simCaixinha(m, simTeto, teto));
  let acc = 0; const accs = vals.map(v => (acc += v));
  const W = 640, H = 230, P = 28, top = 22, bottom = H - 30;
  const max = Math.max(...vals.map(Math.abs), ...accs.map(Math.abs), 1);
  const hasNeg = vals.some(v => v < 0) || accs.some(v => v < 0);
  const zero = hasNeg ? top + (bottom - top) * (Math.max(...vals, ...accs, 0) / (Math.max(...vals, ...accs, 0) - Math.min(...vals, ...accs, 0) || 1)) : bottom;
  const scale = (bottom - top) / (hasNeg ? (Math.max(...vals, ...accs, 0) - Math.min(...vals, ...accs, 0)) || 1 : max);
  const step = (W - P * 2) / months.length, bw = Math.min(46, step * 0.5);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Caixinha prevista por mês"><line class="axis" x1="${P}" x2="${W - P}" y1="${zero}" y2="${zero}"/>`;
  const pts = [];
  months.forEach((m, i) => {
    const cx = P + step * i + step / 2, v = vals[i], h = Math.abs(v) * scale;
    const y = v >= 0 ? zero - h : zero;
    svg += `<rect class="${v >= 0 ? "barp" : "barn"}" x="${cx - bw / 2}" y="${y}" width="${bw}" height="${Math.max(h, 2)}" rx="6"/>`;
    svg += `<rect class="hit" x="${cx - step / 2}" y="0" width="${step}" height="${H}" data-act="month" data-ym="${m.ym}"><title>${esc(monthName(m.ym))}</title></rect>`;
    svg += `<text class="val" x="${cx}" y="${v >= 0 ? y - 6 : y + h + 13}" text-anchor="middle">${esc(brl0(v).replace("R$", "").trim())}</text>`;
    svg += `<text class="lbl" x="${cx}" y="${H - 8}" text-anchor="middle">${esc(m.label)}</text>`;
    pts.push([cx, zero - accs[i] * scale]);
  });
  svg += `<polyline class="acc" points="${pts.map(p => p.join(",")).join(" ")}"/>` + pts.map(p => `<circle class="accdot" cx="${p[0]}" cy="${p[1]}" r="4"/>`).join("");
  return svg + "</svg>";
}
function simCaixinha(m, simTeto, teto) {
  const weeks = teto > 0 ? m.livre / teto : m.semanas;
  return m.sobra - simTeto * weeks;
}

VIEWS.plano = d => {
  const seg = `<div class="seg">${[["proj", "Projeção"], ["parcelas", "Parcelas"]].map(([k, l]) => `<button data-act="plantab" data-t="${k}" class="${S.planTab === k ? "on" : ""}">${l}</button>`).join("")}</div>`;
  if (S.planTab === "parcelas") {
    const ps = d.parcelas;
    return `<div class="monthnav">${seg}<button class="btn sm" data-act="addinst">${icon("plus")}Nova</button></div>
    <div class="grid two"><div class="card stat"><div class="k">Total restante</div><div class="v num">${brl0(d.parcelas_total_restante)}</div></div>
      <div class="card stat"><div class="k">Por mês agora</div><div class="v num">${brl0(d.parcelas_mensal)}</div></div></div>
    <h2 class="sec">Parcelas</h2>
    <section class="card flush"><div class="list">${ps.length ? ps.map(p => `<div class="item">
      <span class="ic blue">${icon("card")}</span>
      <div class="body"><div class="t">${esc(p.name)}</div>
        <div class="sub">${esc(p.card)} · ${p.paid_count}/${p.total} pagas${p.restante > 0 ? ` · termina na fatura de ${esc(p.ultima_fatura)}` : " · quitada"}</div>
        <div class="bar ok"><i data-w="${(p.paid_count / p.total) * 100}"></i></div></div>
      <div class="ctrl"><div class="amt num">${brl(p.amount)}</div>
        <div class="btn-row">${p.restante > 0 && (d.config.auto || {}).parcelas === false ? `<button class="btn sm secondary" data-act="instpay" data-id="${p.id}" data-n="${p.paid_count}">+1 paga</button>` : ""}<button class="btn sm ghost" data-act="editinst" data-id="${p.id}">Editar</button></div></div>
    </div>`).join("") : `<div class="empty">${icon("card")}<div>Nenhuma parcela.</div></div>`}</div></section>`;
  }
  const c = d.config, sim = S.simTeto ?? c.teto_semanal;
  const P = simParts(d, sim);
  return `<div class="monthnav">${seg}</div>
  <div class="grid cols2">
    <div class="stack">
      <section class="card">
        <div class="card-head"><div><div class="eyebrow">Caixinha em ${d.proj.months.length} meses</div><h3 class="num" id="simacc">${P.acc}</h3></div>
          <span class="chip ${c.aumento_on ? "blue" : ""}">${c.aumento_on ? "com aumento" : "sem aumento"}</span></div>
        <div class="chart" id="simchart">${P.chart}</div>
        <div class="legend"><span><i class="lg-bar"></i>Caixinha do mês</span><span><i class="lg-acc"></i>Acumulado</span><span>Toque num mês</span></div>
      </section>
      <section class="card">
        <div class="card-head"><h3>Simular teto semanal</h3><b class="num" id="simlabel">${brl0(sim)}/sem</b></div>
        <input class="range" type="range" min="0" max="200" step="5" value="${sim}" data-act-input="sim" aria-label="Teto semanal simulado">
        <div class="lead">Arraste para ver quanto sobra. O teto atual é ${brl0(c.teto_semanal)}.</div>
        <div id="simbtns">${P.btns}</div>
        <div class="setting"><div class="body"><div class="t">Contar com o aumento</div><div class="sub">${c.aumento_pct}% a partir de ${esc(monthName(c.aumento_from))}</div></div>
          <label class="switch"><input type="checkbox" data-set="aumento_on" data-kind="bool" ${c.aumento_on ? "checked" : ""}><span></span></label></div>
      </section>
    </div>
    <div class="stack">
      <div class="stack" id="simmonths">${P.months}</div>
      <section class="card flush"><div class="card-head"><h3>Faturas informadas</h3><button class="btn sm ghost" data-act="setov">${icon("plus")}Informar</button></div>
        <div class="list">${d.overrides.length ? d.overrides.map(o => `<button class="item" data-act="editov" data-card="${esc(o.card)}" data-month="${esc(o.month)}" data-amount="${o.amount}" data-note="${esc(o.note || "")}">
          <span class="ic blue">${icon("card")}</span><span class="body"><span class="t">${esc(o.card)}</span><span class="sub">paga com o dinheiro de ${esc(monthName(o.month))}</span></span><span class="amt num">${brl(o.amount)}</span></button>`).join("") : `<div class="empty">Nenhuma. As faturas usam a previsão.</div>`}</div></section>
    </div>
  </div>`;
};

function simParts(d, sim) {
  const c = d.config, M = d.proj.months;
  const weeks = m => (c.teto_semanal ? m.livre / c.teto_semanal : m.semanas);
  const acc = M.reduce((a, m) => a + simCaixinha(m, sim, c.teto_semanal), 0);
  const open = new Set($$("#simmonths details[open]").map(x => x.dataset.ym));
  return {
    acc: `<span class="${acc < 0 ? "neg" : ""}">${brl(acc)}</span>`,
    chart: projChart(M, sim, c.teto_semanal),
    btns: sim === c.teto_semanal ? "" : `<div class="btn-row"><button class="btn sm" data-act="usesim">Usar ${brl0(sim)} como teto</button><button class="btn sm ghost" data-act="resetsim">Voltar ao atual</button></div>`,
    months: M.map(m => { const cx = simCaixinha(m, sim, c.teto_semanal); return `<details class="card month" data-ym="${m.ym}" ${open.has(m.ym) ? "open" : ""}>
        <summary class="card-head"><div><div class="eyebrow">${esc(monthName(m.ym))}</div><b class="num ${cx < 0 ? "neg" : "pos"}">${brl(cx)}</b> <span class="muted">para a caixinha</span></div>${icon("right")}</summary>
        <div class="kv"><span>Entra${m.decimo ? " (inclui 13º)" : ""}${m.caixa ? " + caixa" : ""}${m.recebido ? " · salário já no caixa" : ""}</span><span class="num">${brl(m.entra)}</span></div>
        ${m.cartoes.map(k => `<div class="kv"><span class="muted">Fatura ${esc(k.card)}${k.source === "informado" ? " · informada" : ""}</span><span class="num">−${brl(k.total)}</span></div>`).join("")}
        <div class="kv"><span class="muted">Contas fixas</span><span class="num">−${brl(m.fixas)}</span></div>
        <div class="kv"><span class="muted">Gasto livre (${weeks(m).toFixed(1)} sem × ${brl0(sim)})</span><span class="num">−${brl(sim * weeks(m))}</span></div>
        <div class="kv total"><span>Caixinha</span><span class="num ${cx < 0 ? "neg" : "pos"}">${brl(cx)}</span></div>
        <div class="lead">Teto máximo sem ficar negativo: <b class="num">${brl(m.teto_max)}/sem</b></div>
      </details>`; }).join(""),
  };
}

function fmtStamp(st) { return st ? `${st.slice(8, 10)}/${st.slice(5, 7)} ${st.slice(11, 16)}` : ""; }
function lastIncome(c) {
  const e = c.entradas || {}; const k = Object.keys(e).sort().pop();
  return k ? `Último: ${brl(e[k].total)} em ${ddmm(e[k].itens[0].date)}` : "";
}
function sw(path, on) { return `<label class="switch"><input type="checkbox" data-set="${path}" data-kind="bool" ${on ? "checked" : ""}><span></span></label>`; }
function timeIn(path, v) { return `<input type="time" data-set="${path}" data-kind="text" value="${esc(v)}">`; }
function moneyIn(path, v, label) { return `<label class="field"><span>${label}</span><div class="money"><input inputmode="decimal" data-set="${path}" data-kind="num" value="${esc(String(v ?? "").replace(".", ","))}"></div></label>`; }

VIEWS.ajustes = d => {
  const c = d.config, n = c.notif, r = n.rules, sa = c.sync_auto || {}, au = c.auto || {};
  const perm = "Notification" in window ? Notification.permission : "unsupported";
  const pushState = perm === "unsupported" ? "Este navegador não suporta notificações." :
    perm === "denied" ? "Bloqueadas no navegador. Libere em Configurações do site › Notificações." :
    S.pushOn ? "Ativadas neste aparelho." : "Desativadas neste aparelho.";
  const standalone = matchMedia("(display-mode: standalone)").matches;
  const months = MES.map((m, i) => `<option value="${i + 1}">${m}</option>`).join("");
  const sel = (path, v, opts) => `<select data-set="${path}" data-kind="int">${opts.replace(`value="${v}"`, `value="${v}" selected`)}</select>`;
  const lastTick = c.last_tick || null;
  return `
  <div class="grid cols2">
  <div class="stack">
    <h2 class="sec">Notificações</h2>
    <section class="card flush">
      <div class="setting"><span class="ic blue">${icon("bell")}</span><div class="body"><div class="t">Este aparelho</div><div class="sub">${pushState}</div></div>
        <div class="ctrl">${S.pushOn ? `<button class="btn sm secondary" data-act="pushtest">Testar</button><button class="btn sm ghost" data-act="pushoff">Desativar</button>` : perm !== "denied" && perm !== "unsupported" ? `<button class="btn sm" data-act="pushon">Ativar</button>` : ""}</div></div>
      ${d.aparelhos.length ? `<div class="setting"><div class="body"><div class="t">Aparelhos inscritos</div><div class="sub">${d.aparelhos.map(a => `${esc(a.label)}${a.last_ok ? ` (último aviso ${esc(a.last_ok.slice(8, 10) + "/" + a.last_ok.slice(5, 7))})` : ""}`).join(" · ")}</div></div></div>` : ""}
      <div class="setting"><div class="body"><div class="t">Receber notificações</div><div class="sub">Liga ou desliga todos os avisos</div></div>${sw("notif.enabled", n.enabled)}</div>
      <div class="setting"><div class="body"><div class="t">Silêncio</div><div class="sub">Nada é enviado nesse horário</div></div><div class="ctrl">${timeIn("notif.quiet_start", n.quiet_start)}<span class="muted">até</span>${timeIn("notif.quiet_end", n.quiet_end)}</div></div>
      <div class="group-label">Avisos</div>
      <div class="setting"><div class="body"><div class="t">Lembrete diário de gastos</div><div class="sub">Quanto ainda pode gastar na semana</div></div><div class="ctrl">${timeIn("notif.rules.daily_log.time", r.daily_log.time)}${sw("notif.rules.daily_log.on", r.daily_log.on)}</div></div>
      <div class="setting"><div class="body"><div class="t">Alerta do teto semanal</div><div class="sub">Quando chegar a estes percentuais</div></div><div class="ctrl"><input class="sm-in" data-set="notif.rules.teto_alert.pcts" data-kind="list" value="${esc(r.teto_alert.pcts.join(", "))}" aria-label="Percentuais">%${sw("notif.rules.teto_alert.on", r.teto_alert.on)}</div></div>
      <div class="setting"><div class="body"><div class="t">Contas a vencer</div><div class="sub">Avisar com antecedência</div></div><div class="ctrl">${sel("notif.rules.bills_due.days_before", r.bills_due.days_before, [0, 1, 2, 3, 5, 7].map(x => `<option value="${x}">${x === 0 ? "no dia" : x + " dia(s) antes"}</option>`).join(""))}${timeIn("notif.rules.bills_due.time", r.bills_due.time)}${sw("notif.rules.bills_due.on", r.bills_due.on)}</div></div>
      <div class="setting"><div class="body"><div class="t">Resumo semanal</div><div class="sub">Como foi a semana anterior</div></div><div class="ctrl">${sel("notif.rules.weekly_summary.weekday", r.weekly_summary.weekday, DSEM_LONG.map((x, i) => `<option value="${(i + 6) % 7}">${x}</option>`).join(""))}${timeIn("notif.rules.weekly_summary.time", r.weekly_summary.time)}${sw("notif.rules.weekly_summary.on", r.weekly_summary.on)}</div></div>
      <div class="setting"><div class="body"><div class="t">Dia do salário</div><div class="sub">Quanto guardar e o que pagar</div></div><div class="ctrl">${timeIn("notif.rules.payday.time", r.payday.time)}${sw("notif.rules.payday.on", r.payday.on)}</div></div>
      ${d.sync_disponivel ? `<div class="setting"><div class="body"><div class="t">Eventos do banco</div><div class="sub">Salário recebido, fatura ou conta paga</div></div>${sw("notif.rules.bank_events.on", (r.bank_events || { on: true }).on)}</div>` : ""}
      <div class="setting"><div class="body"><div class="t">Agendador</div><div class="sub">${d.agendador ? `Configurado${lastTick ? ` · última verificação ${esc(lastTick.slice(8, 10) + "/" + lastTick.slice(5, 7) + " " + lastTick.slice(11))}` : ""}` : "Sem CRON_TOKEN: avisos só saem com o servidor acordado. Veja o README."}</div></div>
        <button class="btn sm secondary" data-act="notifrun">Verificar agora</button></div>
    </section>
    ${d.notificacoes.filter(x => x.title).length ? `<section class="card flush"><div class="card-head"><h3>Últimos avisos</h3></div><div class="list">${d.notificacoes.filter(x => x.title).slice(0, 6).map(x => `<div class="item"><span class="ic">${icon("bell")}</span><span class="body"><span class="t">${esc(x.title)}</span><span class="sub">${esc(x.sent_at.slice(8, 10) + "/" + x.sent_at.slice(5, 7) + " " + x.sent_at.slice(11))} · ${esc(x.body)}</span></span></div>`).join("")}</div></section>` : ""}

    <h2 class="sec">Orçamento</h2>
    <section class="card"><div class="form-grid two">
      ${moneyIn("salario", c.salario, "Salário líquido")}
      ${moneyIn("teto_semanal", c.teto_semanal, "Teto semanal")}
      <label class="field"><span>Dia do salário</span><input type="number" inputmode="numeric" min="1" max="31" data-set="salario_dia" data-kind="int" value="${c.salario_dia}"></label>
      <div class="field"><span>Caixa hoje</span><div class="money"><input inputmode="decimal" data-set="caixa" data-kind="num" value="${esc(String(c.caixa).replace(".", ","))}"></div>
        <small>${c.caixa_desatualizado ? '<span class="warn">Desatualizado — informe o saldo de agora.</span>' : `Saldo nas contas (${esc(monthName(c.caixa_ref || d.hoje.slice(0, 7)))})`}${c.caixa_banco != null ? ` · <button class="btn ghost sm" data-act="usebank">usar ${brl(c.caixa_banco)} do banco</button>` : ""}</small></div>
      ${moneyIn("decimo1", c.decimo1, "13º — 1ª parcela")}
      <label class="field"><span>Mês da 1ª parcela</span>${sel("decimo1_mes", c.decimo1_mes, months)}</label>
      ${moneyIn("decimo2", c.decimo2, "13º — 2ª parcela")}
      <label class="field"><span>Mês da 2ª parcela</span>${sel("decimo2_mes", c.decimo2_mes, months)}</label>
      <label class="field"><span>Aumento (%)</span><input inputmode="decimal" data-set="aumento_pct" data-kind="num" value="${c.aumento_pct}"></label>
      <label class="field"><span>Aumento a partir de</span><input type="month" data-set="aumento_from" data-kind="text" value="${esc(c.aumento_from)}"></label>
    </div></section>
  </div>
  <div class="stack">
    <h2 class="sec">Cartões <button class="btn ghost sm" data-act="addcard">${icon("plus")}Cartão</button></h2>
    <section class="card flush">${c.cartoes.map(k => `<div class="setting"><span class="ic blue">${icon("card")}</span><div class="body"><div class="t">${esc(k)}</div><div class="sub">Vence dia ${esc((c.cartao_venc || {})[k] ?? "—")} · compras desde o fechamento${((c.ciclo_info || {})[k] || {}).fechou ? ` (${ddmm(c.ciclo_info[k].fechou)})` : ""}: ${brl((c.ciclo_extra || {})[k] || 0)}</div></div>
      <button class="btn sm ghost" data-act="editcard" data-card="${esc(k)}">Editar</button></div>`).join("")}</section>

    <h2 class="sec">Contas fixas e assinaturas <button class="btn ghost sm" data-act="addrec">${icon("plus")}Nova</button></h2>
    <section class="card flush"><div class="list">${d.recorrentes.map(x => `<button class="item" data-act="editrec" data-id="${x.id}">
      <span class="ic ${x.card ? "blue" : ""}">${icon(x.card ? "card" : "bills")}</span>
      <span class="body"><span class="t">${esc(x.name)}</span><span class="sub">${x.card ? "na fatura " + esc(x.card) : "paga direto"}${x.due_day ? ` · vence dia ${x.due_day}` : x.card ? "" : " · sem dia de vencimento"}${!x.card && x.match ? " · automático" : ""}</span></span>
      <span class="amt num">${brl(x.amount)}</span></button>`).join("")}</div></section>

    <h2 class="sec">Banco</h2>
    <section class="card flush">
      <div class="setting"><span class="ic">${icon("bank")}</span><div class="body"><div class="t">Banco MCP</div><div class="sub">${d.sync_disponivel ? `Última sincronização: ${esc(fmtStamp(c.last_sync) || "nunca")}${c.caixa_banco != null ? ` · saldo ${brl(c.caixa_banco)}` : ""}` : "Sem BANCO_MCP_KEY no servidor. O app funciona só com lançamentos manuais."}</div></div>
        ${d.sync_disponivel ? `<button class="btn sm" data-act="sync">Sincronizar</button>` : ""}</div>
      ${d.sync_disponivel && (c.sync_detalhe || []).length ? `<div class="setting"><div class="body"><div class="t">Lido na última sincronização</div><div class="sub">${c.sync_detalhe.map(x => `${esc(x.banco)} ${esc(x.tipo)}: ${x.lidas} lidas, ${x.no_app} no app`).join(" · ")}</div></div></div>` : ""}
      ${d.sync_disponivel ? `
      <div class="group-label">Rotina automática</div>
      <div class="setting"><div class="body"><div class="t">Sincronizar sozinho</div><div class="sub">Lê o banco a cada intervalo, dentro do horário</div></div>${sw("sync_auto.on", sa.on)}</div>
      <div class="setting"><div class="body"><div class="t">Horário</div></div><div class="ctrl">${timeIn("sync_auto.inicio", sa.inicio)}<span class="muted">até</span>${timeIn("sync_auto.fim", sa.fim)}</div></div>
      <div class="setting"><div class="body"><div class="t">Intervalo</div></div>${sel("sync_auto.intervalo", sa.intervalo, [15, 30, 60, 120].map(x => `<option value="${x}">a cada ${x} min</option>`).join(""))}</div>
      <div class="setting"><div class="body"><div class="t">Pedir dados novos ao banco</div><div class="sub">O provedor permite no máximo 1 vez por hora por banco${c.last_force ? ` · último pedido ${esc(fmtStamp(c.last_force.replace("T", " ")))}` : ""}</div></div>${sw("sync_auto.forcar", sa.forcar)}</div>
      <div class="group-label">O que fazer sozinho</div>
      <div class="setting"><div class="body"><div class="t">Caixa pelo saldo das contas</div><div class="sub">Saldo menos as contas do mês ainda não pagas</div></div>${sw("auto.caixa", au.caixa)}</div>
      <div class="setting"><div class="body"><div class="t">Salário real</div><div class="sub">${lastIncome(c) || "Detecta o crédito do salário perto do dia " + c.salario_dia}</div></div>${sw("auto.salario", au.salario)}</div>
      <div class="setting"><div class="body"><div class="t">Marcar contas e faturas pagas</div><div class="sub">Pelo pagamento no extrato. Nas contas fixas, use “Como aparece no extrato”</div></div>${sw("auto.pagamentos", au.pagamentos)}</div>
      <div class="setting"><div class="body"><div class="t">Valor real das faturas</div><div class="sub">Usa a fatura fechada e a aberta do banco no lugar da previsão</div></div>${sw("auto.faturas", au.faturas)}</div>
      <div class="setting"><div class="body"><div class="t">Avançar parcelas</div><div class="sub">Conta a parcela quando a fatura é paga ou vence</div></div>${sw("auto.parcelas", au.parcelas)}</div>` : ""}
      <div class="setting stack"><div class="body"><div class="t">Ignorar na sincronização</div><div class="sub">Palavras separadas por vírgula</div></div><textarea class="input" rows="3" data-set="ignore_terms" data-kind="list">${esc(c.ignore_terms.join(", "))}</textarea></div>
      <div class="setting"><div class="body"><div class="t">Pix até este valor vira gasto</div><div class="sub">Acima disso é tratado como conta</div></div><div class="money"><input class="sm-in" inputmode="decimal" data-set="pix_max" data-kind="num" value="${c.pix_max}"></div></div>
    </section>

    <h2 class="sec">Dados</h2>
    <section class="card flush">
      <div class="setting"><span class="ic ${d.backup.ativo && !d.backup.pausado ? "blue" : ""}">${icon("cloud")}</span><div class="body"><div class="t">Backup automático no GitHub</div>
        <div class="sub">${d.backup.ativo ? (d.backup.pausado ? `<span class="warn">Pausado: ${esc(d.backup.erro)}</span>` : `Ativo em ${esc(d.backup.repo)}${d.backup.ultimo ? ` · último ${esc(d.backup.ultimo.slice(11))}` : ""}${d.backup.erro ? ` · <span class="warn">${esc(d.backup.erro)}</span>` : ""}`) : "Desligado. Configure GITHUB_TOKEN e GITHUB_REPO no Render (README)."}</div></div>
        ${d.backup.ativo ? `<button class="btn sm secondary" data-act="backupnow">Salvar agora</button>` : ""}</div>
      <div class="setting"><span class="ic">${icon("download")}</span><div class="body"><div class="t">Cópia manual</div><div class="sub">Baixe ou restaure um arquivo .json</div></div>
        <div class="ctrl"><a class="btn sm secondary" href="/api/export" download>Baixar</a><button class="btn sm ghost" data-act="import">Restaurar</button></div></div>
      <input id="impfile" type="file" accept="application/json" hidden>
    </section>

    <h2 class="sec">Aparelho</h2>
    <section class="card flush">
      <div class="setting"><span class="ic">${icon("moon")}</span><div class="body"><div class="t">Tema</div></div>
        <div class="seg">${[["auto", "Auto"], ["light", "Claro"], ["dark", "Escuro"]].map(([k, l]) => `<button data-act="theme" data-t="${k}" class="${(store.get("fin-theme") || "auto") === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
      ${!standalone ? `<div class="setting"><span class="ic">${icon("install")}</span><div class="body"><div class="t">Instalar o app</div><div class="sub">${S.installEvt ? "Abre em tela cheia, com ícone na tela inicial" : "No Chrome: menu ⋮ › Instalar app (ou Adicionar à tela inicial)"}</div></div>${S.installEvt ? `<button class="btn sm" data-act="install">Instalar</button>` : ""}</div>` : ""}
      <div class="setting"><span class="ic">${icon("logout")}</span><div class="body"><div class="t">Sair</div><div class="sub">Encerra a sessão neste aparelho</div></div><button class="btn sm danger" data-act="logout">Sair</button></div>
    </section>
  </div>
  </div>`;
};

// ---------- folhas (formulários) ----------
function openSheet({ title, desc, fields, values = {}, onSave, onDelete, saveLabel = "Salvar" }) {
  const ov = document.createElement("div"); ov.className = "overlay";
  const box = document.createElement("form"); box.className = "sheet"; box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true");
  box.innerHTML = `<div class="grab"></div><h3>${esc(title)}</h3>${desc ? `<p class="desc">${esc(desc)}</p>` : ""}<div class="form-grid"></div>
    <div class="actions">${onDelete ? `<button type="button" class="btn danger" data-x="del">Excluir</button>` : ""}<button type="button" class="btn secondary" data-x="cancel">Cancelar</button><button type="submit" class="btn">${esc(saveLabel)}</button></div>`;
  const grid = box.querySelector(".form-grid");
  const get = {};
  for (const f of fields) {
    const wrap = document.createElement("label"); wrap.className = "field";
    const v = values[f.name] ?? f.value ?? "";
    if (f.type === "checkbox") {
      wrap.className = "setting"; wrap.innerHTML = `<div class="body"><div class="t">${esc(f.label)}</div>${f.hint ? `<div class="sub">${esc(f.hint)}</div>` : ""}</div><span class="switch"><input type="checkbox" ${v ? "checked" : ""}><span></span></span>`;
      const i = wrap.querySelector("input"); get[f.name] = () => i.checked;
    } else if (f.type === "chips") {
      wrap.innerHTML = `<span>${esc(f.label)}</span><div class="chips-pick">${f.options.map(o => `<button type="button" data-v="${esc(o)}" class="${o === v ? "on" : ""}">${esc(o)}</button>`).join("")}</div>`;
      let cur = v;
      wrap.querySelectorAll("button").forEach(b => b.onclick = () => { cur = cur === b.dataset.v ? "" : b.dataset.v; wrap.querySelectorAll("button").forEach(x => x.classList.toggle("on", x.dataset.v === cur)); });
      get[f.name] = () => cur || null;
    } else if (f.type === "select") {
      wrap.innerHTML = `<span>${esc(f.label)}</span><select>${f.options.map(o => { const [val, lab] = Array.isArray(o) ? o : [o, o]; return `<option value="${esc(val)}" ${String(val) === String(v) ? "selected" : ""}>${esc(lab)}</option>`; }).join("")}</select>`;
      const s = wrap.querySelector("select"); get[f.name] = () => s.value;
    } else {
      const money = f.type === "money";
      wrap.innerHTML = `<span>${esc(f.label)}</span>${money ? '<div class="money">' : ""}<input ${f.big ? 'class="amount-input"' : ""}>${money ? "</div>" : ""}${f.hint ? `<small>${esc(f.hint)}</small>` : ""}`;
      const i = wrap.querySelector("input");
      i.type = money || f.type === "decimal" ? "text" : (f.type || "text");
      if (money || f.type === "decimal") { i.inputMode = "decimal"; i.value = v === "" || v == null ? "" : String(v).replace(".", ","); }
      else i.value = v;
      if (f.type === "number") { i.inputMode = "numeric"; if (f.min != null) i.min = f.min; if (f.max != null) i.max = f.max; }
      if (f.required) i.required = true;
      if (f.placeholder) i.placeholder = f.placeholder;
      get[f.name] = () => {
        if (money || f.type === "decimal") return parseMoney(i.value);
        if (f.type === "number") return i.value === "" ? null : Number(i.value);
        return i.value;
      };
    }
    grid.append(wrap);
  }
  const close = () => { ov.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = e => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  box.querySelector('[data-x="cancel"]').onclick = close;
  if (onDelete) box.querySelector('[data-x="del"]').onclick = async () => {
    if (!(await confirmSheet("Excluir este item?", "Excluir"))) return;
    try { await onDelete(); close(); toast("Excluído"); await load({ silent: true }); } catch (e) { toast(e.message); }
  };
  box.onsubmit = async e => {
    e.preventDefault();
    const v = {}; for (const f of fields) v[f.name] = get[f.name]();
    for (const f of fields) if ((f.type === "money" || f.type === "decimal") && f.required && (v[f.name] == null || isNaN(v[f.name]))) { toast("Informe um valor válido"); return; }
    const btn = box.querySelector('[type="submit"]'); btn.disabled = true;
    try { const msg = await onSave(v); close(); buzz(); toast(typeof msg === "string" ? msg : "Salvo"); await load({ silent: true }); }
    catch (err) { toast(err.message); btn.disabled = false; }
  };
  ov.addEventListener("click", e => { if (e.target === ov) close(); });
  ov.append(box); document.body.append(ov);
  const first = box.querySelector("input:not([type=checkbox]),select"); if (first && matchMedia("(pointer:fine)").matches) first.focus();
  else if (first && fields[0] && fields[0].big) first.focus();
}

function openDetail({ eyebrow, title, sub, body, actions = "" }) {
  const ov = document.createElement("div"); ov.className = "overlay detail";
  ov.innerHTML = `<div class="sheet" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="grab"></div>
    ${eyebrow ? `<div class="eyebrow">${esc(eyebrow)}</div>` : ""}<h3 class="num">${title}</h3>${sub ? `<p class="desc">${sub}</p>` : ""}
    <div class="detail-body">${body}</div>
    <div class="actions">${actions}<button type="button" class="btn secondary" data-x="close">Fechar</button></div></div>`;
  const close = () => { ov.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = e => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  ov.addEventListener("click", e => { if (e.target === ov || e.target.closest('[data-x="close"]')) close(); });
  document.body.append(ov); applySizes(ov);
  return ov;
}
const sumSp = list => list.filter(s => !s.ignored).reduce((a, s) => a + s.amount, 0);
function spendList(list, empty) {
  return list.length ? `<div class="list card flush">${list.map(spendRow).join("")}</div>` : `<div class="empty">${icon("wallet")}<div>${empty}</div></div>`;
}

function confirmSheet(msg, ok = "Confirmar") {
  return new Promise(res => {
    const ov = document.createElement("div"); ov.className = "overlay";
    ov.innerHTML = `<div class="sheet" role="alertdialog" aria-modal="true"><div class="grab"></div><h3>${esc(msg)}</h3><div class="actions"><button class="btn secondary" data-x="n">Cancelar</button><button class="btn" data-x="y">${esc(ok)}</button></div></div>`;
    const done = v => { ov.remove(); res(v); };
    ov.addEventListener("click", e => { if (e.target === ov) done(false); const x = e.target.closest("[data-x]"); if (x) done(x.dataset.x === "y"); });
    document.body.append(ov);
  });
}

// ---------- notificações (Web Push) ----------
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
function keyBytes(b64) {
  const s = (b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(s), c => c.charCodeAt(0));
}
function deviceLabel() {
  const ua = navigator.userAgent;
  const os = /Android/i.test(ua) ? "Android" : /iPhone|iPad/i.test(ua) ? "iPhone" : /Windows/i.test(ua) ? "Windows" : /Mac/i.test(ua) ? "Mac" : /Linux/i.test(ua) ? "Linux" : "Aparelho";
  const br = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "";
  return `${os}${br ? " · " + br : ""}`;
}
async function currentSub() {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}
async function syncPush() {
  // Mantém o servidor sabendo deste aparelho (por exemplo, depois de uma restauração).
  try {
    const sub = await currentSub();
    const was = S.pushOn;
    S.pushOn = !!sub && Notification.permission === "granted";
    if (sub && S.pushOn && !syncPush.done) { syncPush.done = true; await api("/api/push/subscribe", { subscription: sub.toJSON(), label: deviceLabel() }); }
    if (was !== S.pushOn) render();
  } catch (_) { /* sem suporte */ }
}
async function enablePush() {
  if (!pushSupported()) { toast("Este navegador não suporta notificações"); return; }
  const perm = await Notification.requestPermission();
  if (perm !== "granted") { toast("Permissão negada. Libere nas configurações do site."); render(); return; }
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  const key = keyBytes(S.d.push_key);
  try {
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  } catch (_) {
    if (sub) await sub.unsubscribe();
    sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  }
  await api("/api/push/subscribe", { subscription: sub.toJSON(), label: deviceLabel() });
  S.pushOn = true; buzz(); toast("Notificações ativadas");
  await load({ silent: true });
}
async function disablePush() {
  const sub = await currentSub();
  if (sub) { await api("/api/push/unsubscribe", { endpoint: sub.endpoint }).catch(() => {}); await sub.unsubscribe(); }
  S.pushOn = false; toast("Notificações desativadas neste aparelho"); await load({ silent: true });
}

// ---------- ações ----------
const spendFields = [
  { name: "amount", label: "Valor", type: "money", required: true, big: true },
  { name: "description", label: "Descrição", placeholder: "Ex.: mercado, Uber, lanche", required: true },
  { name: "category", label: "Categoria", type: "chips", options: CATS.map(c => c[0]) },
  { name: "date", label: "Data", type: "date" },
];
const instFields = () => [
  { name: "name", label: "Nome", required: true }, { name: "card", label: "Cartão", type: "select", options: S.d.config.cartoes },
  { name: "amount", label: "Valor da parcela", type: "money", required: true },
  { name: "total", label: "Total de parcelas", type: "number", min: 1 }, { name: "paid_count", label: "Já pagas", type: "number", min: 0 },
];
const recFields = () => [
  { name: "name", label: "Nome", required: true }, { name: "amount", label: "Valor mensal", type: "money", required: true },
  { name: "card", label: "Como paga", type: "select", options: [["", "Direto da conta (boleto, Pix, débito)"], ...S.d.config.cartoes.map(c => [c, "Na fatura " + c])] },
  { name: "due_day", label: "Dia de vencimento", type: "number", min: 1, max: 31, hint: "Usado nos lembretes de contas a vencer" },
  { name: "match", label: "Como aparece no extrato", placeholder: "Ex.: enel, ampla", hint: "Palavras do boleto ou Pix (separe por vírgula). Marca a conta como paga sozinha" },
];

const A = {
  refresh: () => load(),
  day: el => {
    const w = S.d.semana, date = el.dataset.date, dt = parse(date);
    const list = w.lista.filter(s => s.date === date);
    const total = sumSp(list), media = w.teto / 7;
    const future = date > w.hoje;
    const ratio = media ? (total / media) * 100 : 0;
    const sub = future ? "Dia ainda não chegou." :
      `${list.filter(s => !s.ignored).length} lançamento(s) · média do teto por dia ${brl(media)}`;
    const bar = future ? "" : `<div class="bar ${ratio > 100 ? "over" : ratio > 80 ? "warn" : ""}"><i data-w="${ratio}"></i></div>
      <p class="lead">${total > media ? `${brl(total - media)} acima da média diária` : `${brl(media - total)} abaixo da média diária`}</p>`;
    openDetail({
      eyebrow: `${DSEM_LONG[dt.getDay()]}, ${dt.getDate()} de ${MES[dt.getMonth()]}`, title: brl(total), sub,
      body: bar + spendList(list, "Nenhum gasto neste dia."),
      actions: `<button type="button" class="btn" data-act="addspend" data-date="${date}">${icon("plus")}Lançar neste dia</button>`,
    });
  },
  cat: el => {
    const w = S.d.semana, cat = el.dataset.cat;
    const list = w.lista.filter(s => s.date >= w.desde && !s.ignored && (s.category || "outros").toLowerCase() === cat.toLowerCase());
    const total = sumSp(list), all = w.desde_total || 1;
    openDetail({
      eyebrow: `Categoria · desde ${ddmm(w.desde)}`, title: `${esc(cat[0].toUpperCase() + cat.slice(1))} · ${brl(total)}`,
      sub: `${list.length} lançamento(s) · ${Math.round((total / all) * 100)}% do que você gastou desde o salário`,
      body: spendList(list, "Nenhum gasto nesta categoria."),
    });
  },
  month: el => {
    const m = S.d.proj.months.find(x => x.ym === el.dataset.ym); if (!m) return;
    const c = S.d.config, sim = S.simTeto ?? c.teto_semanal;
    const weeks = c.teto_semanal ? m.livre / c.teto_semanal : m.semanas;
    const cx = simCaixinha(m, sim, c.teto_semanal);
    const parts = m.cartoes.map(k => `<div class="kv"><span class="muted">Fatura ${esc(k.card)}${k.source === "informado" ? " · informada" : ""}</span><span class="num">−${brl(k.total)}</span></div>
      ${(k.parts || []).map(p => `<div class="kv sub"><span>${esc(p.name)}</span><span class="num">${brl(p.amount)}</span></div>`).join("")}`).join("");
    openDetail({
      eyebrow: monthName(m.ym), title: `<span class="${cx < 0 ? "neg" : "pos"}">${brl(cx)}</span>`, sub: "para a caixinha",
      body: `<div class="card">
        <div class="kv"><span>Entra${m.decimo ? " (inclui 13º)" : ""}${m.caixa ? " + caixa" : ""}${m.recebido ? " · salário já no caixa" : ""}</span><span class="num">${brl(m.entra)}</span></div>
        ${parts}
        <div class="kv"><span class="muted">Contas fixas</span><span class="num">−${brl(m.fixas)}</span></div>
        <div class="kv"><span class="muted">Gasto livre (${weeks.toFixed(1)} sem × ${brl0(sim)})</span><span class="num">−${brl(sim * weeks)}</span></div>
        <div class="kv total"><span>Caixinha</span><span class="num ${cx < 0 ? "neg" : "pos"}">${brl(cx)}</span></div></div>
        <p class="lead">Teto máximo sem ficar negativo neste mês: <b class="num">${brl(m.teto_max)}/sem</b></p>`,
    });
  },
  mnav: async el => { S.month = addMonths(S.month, Number(el.dataset.d)); await load(); },
  filter: el => { S.filter = el.dataset.f; render(); },
  plantab: el => { S.planTab = el.dataset.t; render(); },
  resetsim: () => { S.simTeto = null; render(); },
  usesim: async () => { try { await api("/api/settings", { teto_semanal: S.simTeto }); toast("Teto atualizado"); S.simTeto = null; await load({ silent: true }); } catch (e) { toast(e.message); } },
  addspend: el => openSheet({ title: "Lançar gasto", desc: "Entra no teto da semana.", fields: spendFields, values: { date: (el && el.dataset && el.dataset.date) || S.d.hoje }, saveLabel: "Lançar",
    onSave: async v => { await api("/api/spends", v); return "Gasto lançado"; } }),
  editspend: el => {
    const s = S.d.semana.lista.find(x => x.id === Number(el.dataset.id)); if (!s) return;
    openSheet({ title: "Editar gasto", fields: [...spendFields, { name: "ignored", label: "Ignorar no teto", type: "checkbox", hint: "Ex.: reembolso ou gasto que já é conta fixa" }],
      values: s, onSave: v => api("/api/spends", { ...v, id: s.id }), onDelete: () => api("/api/spends", { id: s.id, delete: true }) });
  },
  editamt: el => openSheet({
    title: el.dataset.name, desc: "Informe o valor real. Deixe vazio para voltar à previsão.", fields: [{ name: "amount", label: "Valor", type: "money", big: true }],
    values: { amount: el.dataset.amount === "" ? "" : Number(el.dataset.amount) },
    onSave: v => api("/api/payments", { month: S.month, key: el.dataset.key, amount: v.amount }),
    onDelete: el.dataset.est === "0" ? () => api("/api/payments", { month: S.month, key: el.dataset.key, amount: null }) : null,
  }),
  instpay: async el => { try { await api("/api/installments", { id: Number(el.dataset.id), paid_count: Number(el.dataset.n) + 1 }); buzz(); toast("Parcela marcada"); await load({ silent: true }); } catch (e) { toast(e.message); } },
  addinst: () => openSheet({ title: "Nova parcela", fields: instFields(), values: { card: S.d.config.cartoes[0], total: 2, paid_count: 0 }, onSave: v => api("/api/installments", v) }),
  editinst: el => { const p = S.d.parcelas.find(x => x.id === Number(el.dataset.id)); openSheet({ title: p.name, fields: instFields(), values: p, onSave: v => api("/api/installments", { ...v, id: p.id }), onDelete: () => api("/api/installments", { id: p.id, delete: true }) }); },
  addrec: () => openSheet({ title: "Nova conta ou assinatura", fields: recFields(), values: { card: "" }, onSave: v => api("/api/recurring", v) }),
  editrec: el => { const r = S.d.recorrentes.find(x => x.id === Number(el.dataset.id)); openSheet({ title: r.name, fields: recFields(), values: { ...r, card: r.card || "", due_day: r.due_day ?? "" }, onSave: v => api("/api/recurring", { ...v, id: r.id }), onDelete: () => api("/api/recurring", { id: r.id, delete: true }) }); },
  setov: () => openSheet({
    title: "Informar fatura", desc: "Substitui a previsão daquele mês.",
    fields: [{ name: "card", label: "Cartão", type: "select", options: S.d.config.cartoes }, { name: "month", label: "Paga com o dinheiro de", type: "select", options: S.d.proj.months.map(m => [m.ym, monthName(m.ym)]) }, { name: "amount", label: "Valor", type: "money", required: true }, { name: "note", label: "Observação" }],
    values: { card: S.d.config.cartoes[0], month: S.d.proj.months[0].ym }, onSave: v => api("/api/overrides", v),
  }),
  editov: el => openSheet({ title: `${el.dataset.card} · ${monthName(el.dataset.month)}`, fields: [{ name: "amount", label: "Valor", type: "money", required: true }, { name: "note", label: "Observação" }],
    values: { amount: Number(el.dataset.amount), note: el.dataset.note }, onSave: v => api("/api/overrides", { card: el.dataset.card, month: el.dataset.month, ...v }),
    onDelete: () => api("/api/overrides", { card: el.dataset.card, month: el.dataset.month, delete: true }) }),
  addcard: () => openSheet({ title: "Novo cartão", fields: [{ name: "name", label: "Nome", required: true }, { name: "venc", label: "Dia de vencimento", type: "number", min: 1, max: 31 }],
    onSave: async v => { const c = S.d.config; await api("/api/settings", { cartoes: [...c.cartoes, v.name], cartao_venc: { ...(c.cartao_venc || {}), ...(v.venc ? { [v.name]: v.venc } : {}) } }); } }),
  editcard: el => {
    const k = el.dataset.card, c = S.d.config;
    openSheet({ title: k, fields: [{ name: "venc", label: "Dia de vencimento", type: "number", min: 1, max: 31 }, { name: "extra", label: "Compras do ciclo atual", type: "money", hint: "Gastos já feitos neste cartão que vão para a próxima fatura (fora parcelas e assinaturas)" }],
      values: { venc: (c.cartao_venc || {})[k] ?? "", extra: (c.ciclo_extra || {})[k] ?? 0 },
      onSave: v => { const venc = { ...(c.cartao_venc || {}) }; if (v.venc) venc[k] = v.venc; else delete venc[k]; return api("/api/settings", { cartao_venc: venc, ciclo_extra: { ...(c.ciclo_extra || {}), [k]: v.extra || 0 } }); },
      onDelete: c.cartoes.length > 1 ? () => api("/api/settings", { cartoes: c.cartoes.filter(x => x !== k) }) : null });
  },
  usebank: async () => { try { await api("/api/settings", { caixa: S.d.config.caixa_banco }); toast("Caixa atualizado"); await load({ silent: true }); } catch (e) { toast(e.message); } },
  sync: async el => {
    el.disabled = true; el.textContent = "Sincronizando…";
    try {
      const r = await api("/api/sync", {});
      if (r.ocupado) { toast("Já está sincronizando, aguarde um instante"); el.disabled = false; el.textContent = "Sincronizar"; return; }
      const bits = [`${r.novos} gasto(s) novo(s)`];
      if (r.estornos) bits.push(`${r.estornos} estorno(s) casado(s)`);
      if (r.eventos) bits.push(`${r.eventos} atualização(ões) de contas`);
      toast(bits.join(" · ")); await load({ silent: true });
    }
    catch (e) { toast(e.message); el.disabled = false; el.textContent = "Sincronizar"; }
  },
  backupnow: async el => { el.disabled = true; try { await api("/api/backup/now", {}); toast("Backup salvo no GitHub"); await load({ silent: true }); } catch (e) { toast(e.message); el.disabled = false; } },
  import: () => $("#impfile").click(),
  pushon: async el => { if (el) el.disabled = true; try { await enablePush(); } catch (e) { toast(e.message); if (el) el.disabled = false; } },
  pushoff: async () => { try { await disablePush(); } catch (e) { toast(e.message); } },
  pushtest: async el => { el.disabled = true; try { await api("/api/push/test", {}); toast("Enviado. Deve chegar em segundos."); } catch (e) { toast(e.message); } el.disabled = false; },
  notifrun: async el => { el.disabled = true; try { const r = await api("/api/notify/run", {}); toast(r.enviadas && r.enviadas.length ? `${r.enviadas.length} aviso(s) enviado(s)` : r.silencio ? "Horário de silêncio: nada enviado" : "Nada para avisar agora"); await load({ silent: true }); } catch (e) { toast(e.message); } el.disabled = false; },
  theme: el => { store.set("fin-theme", el.dataset.t); applyTheme(); render(); },
  install: async () => { if (!S.installEvt) return; S.installEvt.prompt(); await S.installEvt.userChoice.catch(() => {}); S.installEvt = null; render(); },
  logout: async () => {
    if (!(await confirmSheet("Sair deste aparelho?", "Sair"))) return;
    await api("/api/logout", {}).catch(() => {});
    try { await caches.delete("fin-data"); } catch (_) { /* opcional */ }
    S.d = null; $("#view").innerHTML = ""; showLogin();
  },
};

document.addEventListener("click", e => {
  const el = e.target.closest("[data-act]");
  if (el && A[el.dataset.act]) {
    e.preventDefault();
    const det = el.closest(".overlay.detail");
    if (det) det.remove();
    A[el.dataset.act](el);
  }
});
document.addEventListener("input", e => {
  if (e.target.dataset.actInput === "sim") {
    S.simTeto = Number(e.target.value);
    const P = simParts(S.d, S.simTeto);
    $("#simacc").innerHTML = P.acc; $("#simchart").innerHTML = P.chart; $("#simbtns").innerHTML = P.btns;
    $("#simmonths").innerHTML = P.months; $("#simlabel").textContent = brl0(S.simTeto) + "/sem";
  }
});

// Ajustes salvam sozinhos ao mudar um campo.
function parseKind(el) {
  const k = el.dataset.kind, v = el.value;
  if (k === "bool") return el.checked;
  if (k === "num") { const n = parseMoney(v); if (n == null || isNaN(n)) throw new Error("Número inválido"); return n; }
  if (k === "int") return parseInt(v, 10);
  if (k === "list") return v;
  return v;
}
function nest(path, value) {
  const parts = path.split("."); const out = {}; let cur = out;
  parts.forEach((p, i) => { if (i === parts.length - 1) cur[p] = value; else cur = cur[p] = {}; });
  return out;
}
document.addEventListener("change", async e => {
  const el = e.target;
  if (el.dataset.set) {
    try { await api("/api/settings", nest(el.dataset.set, parseKind(el))); toast("Salvo"); await load({ silent: true }); }
    catch (err) { toast(err.message); }
    return;
  }
  if (el.dataset.chg === "paid") {
    buzz();
    try { await api("/api/payments", { month: S.month, key: el.dataset.key, paid: el.checked }); await load({ silent: true }); } catch (err) { toast(err.message); }
  }
  if (el.id === "impfile" && el.files[0]) {
    if (await confirmSheet("Restaurar substitui todos os dados atuais. Continuar?", "Restaurar")) {
      try { await api("/api/import", JSON.parse(await el.files[0].text())); toast("Backup restaurado"); await load(); } catch (err) { toast(err.message); }
    }
    el.value = "";
  }
});

// ---------- tema, instalação, service worker ----------
function applyTheme() {
  const t = store.get("fin-theme") || "auto";
  if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.dataset.theme = t;
}
applyTheme();
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); S.installEvt = e; if (S.view === "ajustes") render(); });
window.addEventListener("scroll", () => $("#topbar").classList.toggle("scrolled", window.scrollY > 4), { passive: true });
window.addEventListener("online", () => load({ silent: true }));
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && S.d) load({ silent: true }); });
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
  navigator.serviceWorker.addEventListener("message", e => {
    if (e.data && e.data.type === "navigate") { const u = new URL(e.data.url); location.hash = u.hash || "#inicio"; load({ silent: true }); }
  });
}

$("#loginform").addEventListener("submit", async e => {
  e.preventDefault();
  const b = $("#loginbtn"); b.disabled = true; b.textContent = "Entrando…";
  try { await api("/api/login", { password: $("#pw").value }); $("#login").hidden = true; $("#pw").value = ""; $("#loginerr").textContent = ""; await load(); route(); }
  catch (err) { $("#loginerr").textContent = err.message; }
  b.disabled = false; b.textContent = "Entrar";
});

route();
load().then(route);
