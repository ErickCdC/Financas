"use strict";
// Textos que vêm do banco ou digitados por você SEMPRE passam por esc() antes de ir pro HTML.
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const brl = n => (n < 0 ? "−" : "") + "R$ " + Math.abs(Math.round(n)).toLocaleString("pt-BR");
const brl2 = n => (n < 0 ? "−" : "") + "R$ " + Math.abs(n).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const cls = n => (n < 0 ? "neg" : "pos");
const fmtDay = iso => iso.slice(8, 10) + "/" + iso.slice(5, 7);
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const TABS = [["mes", "Mês"], ["semana", "Semana"], ["parcelas", "Parcelas"], ["proj", "Projeção"], ["config", "Ajustes"]];
const S = { tab: "mes", month: null, d: null };

function addMonths(ym, n) {
  const [y, m] = ym.split("-").map(Number);
  const t = y * 12 + (m - 1) + n;
  return String(Math.floor(t / 12)).padStart(4, "0") + "-" + String((t % 12) + 1).padStart(2, "0");
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.classList.add("on");
  clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("on"), 3200);
}
async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("login"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || "Erro " + r.status);
  return j;
}
function showLogin() { $("#login").hidden = false; $("#pw").focus(); }

async function load() {
  S.d = await api("/api/summary" + (S.month ? "?month=" + S.month : ""));
  S.month = S.d.mes.ym;
  render();
}

// ---------- formulário em folha ----------
function openForm({ title, fields, values = {}, onSave, onDelete, saveLabel = "Salvar" }) {
  const ov = document.createElement("div"); ov.className = "overlay";
  const box = document.createElement("form"); box.className = "sheet";
  const h = document.createElement("h3"); h.textContent = title; box.append(h);
  const inputs = {};
  for (const f of fields) {
    const lab = document.createElement("label");
    const sp = document.createElement("span"); sp.textContent = f.label;
    let inp;
    if (f.type === "select") {
      inp = document.createElement("select");
      for (const o of f.options) { const op = document.createElement("option"); op.value = o; op.textContent = o; inp.append(op); }
    } else {
      inp = document.createElement("input"); inp.type = f.type || "text";
      if (f.type === "number") { inp.step = "0.01"; inp.inputMode = "decimal"; }
    }
    if (f.type === "checkbox") { lab.className = "chk"; inp.checked = !!values[f.name]; lab.append(inp, sp); }
    else { inp.value = values[f.name] ?? f.value ?? ""; lab.append(sp, inp); }
    inputs[f.name] = inp; box.append(lab);
  }
  const btns = document.createElement("div"); btns.className = "btns";
  const ok = document.createElement("button"); ok.type = "submit"; ok.className = "primary"; ok.textContent = saveLabel;
  btns.append(ok);
  if (onDelete) {
    const del = document.createElement("button"); del.type = "button"; del.className = "danger"; del.textContent = "Excluir";
    del.onclick = async () => { if (!confirm("Excluir?")) return; try { await onDelete(); ov.remove(); await load(); } catch (e) { toast(e.message); } };
    btns.append(del);
  }
  const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "ghost"; cancel.textContent = "Cancelar";
  cancel.onclick = () => ov.remove(); btns.append(cancel);
  box.append(btns);
  box.onsubmit = async e => {
    e.preventDefault();
    const v = {};
    for (const f of fields) {
      const i = inputs[f.name];
      v[f.name] = f.type === "checkbox" ? i.checked : f.type === "number" ? (i.value === "" ? null : Number(i.value)) : i.value;
    }
    try { await onSave(v); ov.remove(); await load(); } catch (err) { toast(err.message); }
  };
  ov.addEventListener("click", e => { if (e.target === ov) ov.remove(); });
  ov.append(box); document.body.append(ov);
  const first = box.querySelector("input,select"); if (first && first.type !== "checkbox") first.focus();
}

// ---------- telas ----------
function viewMes(d) {
  const m = d.mes;
  let h = `<div class="monthnav"><button data-act="mnav" data-d="-1" aria-label="Mês anterior">‹</button><div class="m">${esc(m.label)}</div><button data-act="mnav" data-d="1" aria-label="Próximo mês">›</button></div>`;
  h += `<div class="acc"><div class="acc-label">Falta pagar</div><div class="acc-value ${m.falta > 0 ? "gold" : "pos"}">${brl2(m.falta)}</div>
    <div class="acc-note">Já pago: ${brl2(m.pago)}${m.sem_valor ? ` · ${m.sem_valor} conta(s) sem valor ainda` : ""}</div></div>`;
  const group = (title, g) => {
    const its = m.items.filter(i => i.group === g);
    if (!its.length) return "";
    return `<h2>${title}</h2>` + its.map(i => `<div class="row">
      <input type="checkbox" data-chg="paid" data-key="${esc(i.key)}" ${i.paid ? "checked" : ""} aria-label="Pago">
      <div class="grow"><div class="nm ${i.paid ? "done" : ""}">${esc(i.name)}</div>
        ${i.parts && i.parts.length ? `<div class="parts">${i.parts.map(p => esc(p.name) + " " + brl2(p.amount)).join("<br>")}</div>` : ""}
        ${i.used != null && !i.paid ? `<div class="parts">Usado no cartão agora: ${brl2(i.used)}</div>` : ""}</div>
      <button class="amt ${i.estimated ? "est" : ""}" data-act="editamt" data-key="${esc(i.key)}" data-name="${esc(i.name)}" data-amount="${i.amount ?? ""}" data-est="${i.estimated ? 1 : 0}">${i.amount == null ? "informar" : brl2(i.amount)}</button></div>`).join("");
  };
  h += group("Contas fixas", "fixa") + group("Faturas dos cartões", "fatura");
  h += `<div class="footer">Valor tracejado = previsto. Toque nele pra informar o valor real da fatura.</div>`;
  return h;
}

function viewSemana(d) {
  const w = d.semana, pct = Math.min(100, w.teto ? (w.gasto / w.teto) * 100 : 0);
  const bad = w.gasto > w.teto ? "bad" : pct > 75 ? "warn" : "";
  let h = `<div class="eyebrow">Semana ${fmtDay(w.inicio)} a ${fmtDay(w.fim)}</div><h1>Teto semanal</h1><div class="sub">Gasto livre = tudo fora das contas fixas e parcelas.</div>`;
  h += `<div class="acc"><div class="acc-label">${w.estourou > 0 ? "Passou do teto em" : "Ainda pode gastar esta semana"}</div>
    <div class="acc-value ${w.estourou > 0 ? "neg" : "pos"}">${brl2(w.estourou > 0 ? w.estourou : w.restante)}</div>
    <div class="bar ${bad}"><i style="width:${pct}%"></i></div>
    <div class="acc-note">Gasto ${brl2(w.gasto)} de ${brl2(w.teto)} · Semana que vem: ${brl2(w.proxima)}</div></div>`;
  h += `<div class="acc"><div class="acc-label">Gasto desde o salário (${fmtDay(w.desde)})</div><div class="acc-value">${brl2(w.desde_total)}</div></div>`;
  h += `<button class="primary block" data-act="addspend">+ Lançar gasto</button><h2>Lançamentos desde o salário</h2>`;
  h += w.lista.length ? w.lista.map(s => `<div class="spend ${s.ignored ? "off" : ""}" data-act="editspend" data-id="${s.id}">
      <div class="d">${fmtDay(s.date)}</div><div class="n">${esc(s.description)}${s.source === "banco" ? '<span class="tag">banco</span>' : ""}</div>
      <div class="${s.amount < 0 ? "pos" : ""}">${brl2(s.amount)}</div></div>`).join("") : `<div class="sub">Nada lançado ainda.</div>`;
  h += `<div class="footer">Toque num lançamento pra editar, ignorar ou excluir.</div>`;
  return h;
}

function viewParcelas(d) {
  let h = `<div class="eyebrow">Parcelas ativas</div><h1>O que falta pagar</h1><div class="sub">Total restante: <b>${brl2(d.parcelas_total_restante)}</b></div>`;
  h += d.parcelas.map(p => {
    const pct = (p.paid_count / p.total) * 100;
    return `<div class="card"><div class="top"><div class="t">${esc(p.name)}</div><div class="t">${brl2(p.amount)}</div></div>
      <div class="s">${esc(p.card)} · ${p.paid_count}/${p.total} pagas${p.restante > 0 ? ` · faltam ${p.restante} (última na fatura de ${esc(p.ultima_fatura)})` : " · quitada"}</div>
      <div class="bar"><i style="width:${pct}%"></i></div>
      <div class="actions">${p.restante > 0 ? `<button class="sm" data-act="instpay" data-id="${p.id}" data-n="${p.paid_count}">+1 paga</button>` : ""}
      <button class="sm" data-act="editinst" data-id="${p.id}">Editar</button></div></div>`;
  }).join("") || `<div class="sub">Nenhuma parcela.</div>`;
  h += `<button class="primary block" data-act="addinst">+ Nova parcela</button>`;
  return h;
}

function viewProj(d) {
  const pr = d.proj, M = pr.months;
  const row = (label, fn, c = "") => `<tr class="${c}"><td>${label}</td>${M.map(fn).map(x => `<td>${x}</td>`).join("")}</tr>`;
  let h = `<div class="eyebrow">Projeção</div><h1>Teto e caixinha</h1><div class="sub">O dinheiro de cada mês paga as faturas e contas fixas do mês seguinte.${d.config.aumento_on ? " Aumento de janeiro <b>incluído</b>." : " Aumento de janeiro <b>fora</b> da conta."}</div>`;
  if (M[0].caixinha < 0) h += `<div class="alert">${esc(M[0].label)} fecha no vermelho em ${brl(-M[0].caixinha)} com teto de ${brl(d.config.teto_semanal)}/semana.</div>`;
  h += `<table><thead><tr><th></th>${M.map(m => `<th>${esc(m.label)}</th>`).join("")}</tr></thead><tbody>`;
  h += row("Entra (salário + 13º)", m => brl(m.entra));
  h += row("Faturas dos cartões", m => `<span class="muted">${brl(m.cart_total)}</span>`);
  h += row("Contas fixas", m => `<span class="muted">${brl(m.fixas)}</span>`);
  h += row("Sobra antes do gasto livre", m => `<span class="${cls(m.sobra)}">${brl(m.sobra)}</span>`, "sum");
  h += row("Gasto livre no teto", m => `<span class="muted">${brl(m.livre)}</span>`);
  h += row("Caixinha do mês", m => `<span class="${cls(m.caixinha)}">${brl(m.caixinha)}</span>`, "total");
  h += row("Teto máx./semana (zera)", m => brl(m.teto_max));
  h += `</tbody></table>`;
  h += `<div class="acc" style="margin-top:16px"><div class="acc-label">Caixinha acumulada</div><div class="acc-value ${cls(pr.acumulado)}">${brl(pr.acumulado)}</div></div>`;
  const mx = Math.max(...M.map(m => Math.abs(m.caixinha)), 1);
  h += `<div class="bars">${M.map(m => `<div class="c"><div class="v ${cls(m.caixinha)}">${brl(m.caixinha)}</div><div class="b ${m.caixinha < 0 ? "n" : ""}" style="height:${Math.max(3, (Math.abs(m.caixinha) / mx) * 90)}%"></div></div>`).join("")}</div>
    <div class="barnames">${M.map(m => `<div>${esc(m.label)}</div>`).join("")}</div>`;
  h += `<h2>Faturas previstas</h2>` + M.map(m => `<div class="card"><div class="t">${esc(m.label)}</div>` + m.cartoes.map(c => `<div class="s"><b>${esc(c.card)}</b> ${brl2(c.total)} ${c.source === "informado" ? '<span class="tag">informado</span>' : ""}<br>${c.parts.map(p => esc(p.name) + " " + brl2(p.amount)).join(" · ")}</div>`).join("") + `</div>`).join("");
  h += `<button class="ghost block" data-act="setov">Informar valor real de uma fatura</button>`;
  if (d.overrides.length) h += d.overrides.map(o => `<div class="spend" data-act="editov" data-card="${esc(o.card)}" data-month="${esc(o.month)}" data-amount="${o.amount}" data-note="${esc(o.note || "")}"><div class="n">${esc(o.card)} · ${esc(o.month)}</div><div>${brl2(o.amount)}</div></div>`).join("");
  return h;
}

function viewConfig(d) {
  const c = d.config;
  const f = (k, label, type = "number", val = c[k]) => `<label><span>${label}</span><input data-cfg="${k}" type="${type}" ${type === "number" ? 'step="0.01" inputmode="decimal"' : ""} value="${esc(val)}"></label>`;
  let h = `<div class="eyebrow">Ajustes</div><h1>Premissas</h1><div class="sub">Tudo que a projeção e o teto usam.</div>`;
  h += `<div class="grid2">${f("salario", "Salário líquido")}${f("teto_semanal", "Teto semanal")}${f("decimo1", "13º 1ª parcela")}${f("decimo2", "13º 2ª parcela (líquida)")}${f("caixa", "Caixa hoje (entra no 1º mês)")}${f("salario_data", "Data do último salário", "date")}</div>`;
  h += `<label class="chk"><input data-cfg="aumento_on" type="checkbox" ${c.aumento_on ? "checked" : ""}><span>Contar com o aumento em janeiro</span></label>`;
  h += `<div class="grid2">${f("aumento_pct", "Aumento (%)")}${f("pix_max", "PIX até (R$) vira gasto")}</div>`;
  h += `<label><span>Ignorar na sincronização (palavras separadas por vírgula)</span><textarea data-cfg="ignore_terms" rows="3">${esc(c.ignore_terms.join(", "))}</textarea></label>`;
  h += `<button class="primary block" data-act="savecfg">Salvar ajustes</button>`;
  h += `<h2>Contas fixas e assinaturas</h2>` + d.recorrentes.map(r => `<div class="spend" data-act="editrec" data-id="${r.id}"><div class="n">${esc(r.name)}${r.card ? `<span class="tag">${esc(r.card)}</span>` : ""}</div><div>${brl2(r.amount)}</div></div>`).join("");
  h += `<button class="ghost block" data-act="addrec">+ Nova conta ou assinatura</button>`;
  h += `<h2>Banco (Banco MCP)</h2>`;
  if (d.sync_disponivel) {
    h += `<div class="sub">Última sincronização: ${esc(c.last_sync || "nunca")}${c.caixa_banco != null ? ` · caixa nas contas ${brl2(c.caixa_banco)}` : ""}</div><button class="ghost block" data-act="sync">Sincronizar agora</button>`;
  } else {
    h += `<div class="sub">Sem chave configurada. Defina <b>BANCO_MCP_KEY</b> no servidor pra puxar gastos e saldos sozinho. Sem ela o app funciona só com lançamentos manuais.</div>`;
  }
  h += `<h2>Backup</h2><div class="actions"><a class="sm" style="text-decoration:none;color:inherit;border:1px solid var(--line);border-radius:10px;padding:10px 14px;font-size:13px;font-weight:600" href="/api/export" download>Baixar backup</a><button class="sm" data-act="import">Restaurar</button></div>
    <input id="impfile" type="file" accept="application/json" hidden>
    <button class="danger block" data-act="logout" style="margin-top:22px">Sair</button>`;
  return h;
}

function render() {
  $("#tabs").innerHTML = `<div class="in">${TABS.map(([k, n]) => `<button data-act="tab" data-t="${k}" class="${S.tab === k ? "on" : ""}">${n}</button>`).join("")}</div>`;
  const views = { mes: viewMes, semana: viewSemana, parcelas: viewParcelas, proj: viewProj, config: viewConfig };
  $("#view").innerHTML = views[S.tab](S.d) + `<div class="footer">Estimativa pessoal · não substitui extrato nem conselho financeiro</div>`;
}

// ---------- ações ----------
const todayIso = () => S.d.hoje;
const A = {
  tab: el => { S.tab = el.dataset.t; render(); window.scrollTo(0, 0); },
  mnav: async el => { S.month = addMonths(S.month, Number(el.dataset.d)); await load(); },
  editamt: el => openForm({
    title: el.dataset.name, values: { amount: el.dataset.amount === "" ? "" : Number(el.dataset.amount) },
    fields: [{ name: "amount", label: "Valor real (R$)", type: "number" }],
    onSave: v => api("/api/payments", { month: S.month, key: el.dataset.key, amount: v.amount }),
    onDelete: el.dataset.est === "0" ? () => api("/api/payments", { month: S.month, key: el.dataset.key, amount: null }) : null,
  }),
  addspend: () => openForm({
    title: "Lançar gasto", values: { date: todayIso() },
    fields: [{ name: "amount", label: "Valor (R$)", type: "number" }, { name: "description", label: "O que foi" }, { name: "date", label: "Data", type: "date" }, { name: "category", label: "Categoria (opcional)" }],
    onSave: v => api("/api/spends", v),
  }),
  editspend: el => {
    const s = S.d.semana.lista.find(x => x.id === Number(el.dataset.id)); if (!s) return;
    openForm({
      title: "Editar gasto", values: s,
      fields: [{ name: "amount", label: "Valor (R$)", type: "number" }, { name: "description", label: "O que foi" }, { name: "date", label: "Data", type: "date" }, { name: "category", label: "Categoria" }, { name: "ignored", label: "Ignorar (não conta no teto)", type: "checkbox" }],
      onSave: v => api("/api/spends", { ...v, id: s.id }), onDelete: () => api("/api/spends", { id: s.id, delete: true }),
    });
  },
  instpay: async el => { try { await api("/api/installments", { id: Number(el.dataset.id), paid_count: Number(el.dataset.n) + 1 }); await load(); } catch (e) { toast(e.message); } },
  addinst: () => openForm({
    title: "Nova parcela", values: { card: S.d.config.cartoes[0], paid_count: 0, total: 2 },
    fields: [{ name: "name", label: "Nome" }, { name: "card", label: "Cartão", type: "select", options: S.d.config.cartoes }, { name: "amount", label: "Valor da parcela", type: "number" }, { name: "total", label: "Total de parcelas", type: "number" }, { name: "paid_count", label: "Já pagas", type: "number" }],
    onSave: v => api("/api/installments", { ...v, total: Math.round(v.total), paid_count: Math.round(v.paid_count || 0) }),
  }),
  editinst: el => {
    const p = S.d.parcelas.find(x => x.id === Number(el.dataset.id));
    openForm({
      title: p.name, values: p,
      fields: [{ name: "name", label: "Nome" }, { name: "card", label: "Cartão", type: "select", options: S.d.config.cartoes }, { name: "amount", label: "Valor da parcela", type: "number" }, { name: "total", label: "Total de parcelas", type: "number" }, { name: "paid_count", label: "Já pagas", type: "number" }],
      onSave: v => api("/api/installments", { ...v, id: p.id, total: Math.round(v.total), paid_count: Math.round(v.paid_count || 0) }),
      onDelete: () => api("/api/installments", { id: p.id, delete: true }),
    });
  },
  addrec: () => openForm({
    title: "Nova conta ou assinatura", values: { card: "" },
    fields: [{ name: "name", label: "Nome" }, { name: "amount", label: "Valor mensal", type: "number" }, { name: "card", label: "Cai em qual fatura? (vazio = paga direto da conta)", type: "select", options: ["", ...S.d.config.cartoes] }],
    onSave: v => api("/api/recurring", v),
  }),
  editrec: el => {
    const r = S.d.recorrentes.find(x => x.id === Number(el.dataset.id));
    openForm({
      title: r.name, values: { ...r, card: r.card || "" },
      fields: [{ name: "name", label: "Nome" }, { name: "amount", label: "Valor mensal", type: "number" }, { name: "card", label: "Fatura (vazio = direto da conta)", type: "select", options: ["", ...S.d.config.cartoes] }],
      onSave: v => api("/api/recurring", { ...v, id: r.id }), onDelete: () => api("/api/recurring", { id: r.id, delete: true }),
    });
  },
  setov: () => openForm({
    title: "Valor real de uma fatura", values: { card: S.d.config.cartoes[0], month: S.d.proj.months[0].ym },
    fields: [{ name: "card", label: "Cartão", type: "select", options: S.d.config.cartoes }, { name: "month", label: "Mês do dinheiro que paga (AAAA-MM)", type: "select", options: S.d.proj.months.map(m => m.ym) }, { name: "amount", label: "Valor (R$)", type: "number" }, { name: "note", label: "Observação (opcional)" }],
    onSave: v => api("/api/overrides", v),
  }),
  editov: el => openForm({
    title: el.dataset.card + " · " + el.dataset.month, values: { amount: Number(el.dataset.amount), note: el.dataset.note },
    fields: [{ name: "amount", label: "Valor (R$)", type: "number" }, { name: "note", label: "Observação" }],
    onSave: v => api("/api/overrides", { card: el.dataset.card, month: el.dataset.month, ...v }),
    onDelete: () => api("/api/overrides", { card: el.dataset.card, month: el.dataset.month, delete: true }),
  }),
  savecfg: async () => {
    const body = {};
    document.querySelectorAll("[data-cfg]").forEach(i => { body[i.dataset.cfg] = i.type === "checkbox" ? i.checked : i.value; });
    try { await api("/api/settings", body); toast("Ajustes salvos"); await load(); } catch (e) { toast(e.message); }
  },
  sync: async el => {
    el.disabled = true; el.textContent = "Sincronizando…";
    try { const r = await api("/api/sync", {}); toast(`${r.novos} novo(s) do banco` + (r.ja_lancados ? ` · ${r.ja_lancados} já estavam lançados` : "")); await load(); }
    catch (e) { toast(e.message); el.disabled = false; el.textContent = "Sincronizar agora"; }
  },
  import: () => $("#impfile").click(),
  logout: async () => { await api("/api/logout", {}); S.d = null; $("#view").innerHTML = ""; showLogin(); },
};
document.addEventListener("click", e => { const el = e.target.closest("[data-act]"); if (el && A[el.dataset.act]) A[el.dataset.act](el); });
document.addEventListener("change", async e => {
  const el = e.target;
  if (el.dataset && el.dataset.chg === "paid") {
    try { await api("/api/payments", { month: S.month, key: el.dataset.key, paid: el.checked }); await load(); } catch (err) { toast(err.message); }
  }
  if (el.id === "impfile" && el.files[0]) {
    if (!confirm("Restaurar o backup substitui TODOS os dados atuais. Continuar?")) { el.value = ""; return; }
    try { await api("/api/import", JSON.parse(await el.files[0].text())); toast("Backup restaurado"); await load(); } catch (err) { toast(err.message); }
    el.value = "";
  }
});

$("#loginform").addEventListener("submit", async e => {
  e.preventDefault();
  try { await api("/api/login", { password: $("#pw").value }); $("#login").hidden = true; $("#pw").value = ""; $("#loginerr").textContent = ""; await load(); }
  catch (err) { $("#loginerr").textContent = err.message; }
});

load().catch(() => { /* sem sessão: tela de login já aberta */ });
