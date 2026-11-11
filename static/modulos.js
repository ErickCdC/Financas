/* Módulos além das finanças: Hoje (painel), Saúde (treino, corpo, hábitos) e Agenda (próximos, notas, tarefas, faculdade).
   Usa as peças do app.js: S, VIEWS, A, api, openSheet, openDetail, toast, icon, esc… */
"use strict";

// ---------- utilidades ----------
const fmtN = v => Number(v || 0).toLocaleString("pt-BR", { maximumFractionDigits: 2 });
const withU = (v, u) => u ? `${fmtN(v)} ${esc(u)}` : fmtN(v);
const dshort = iso => { const dt = parse(iso); return `${DSEM[dt.getDay()]}, ${ddmm(iso)}`; };
function quandoTxt(iso, hora, today) {
  const n = daysBetween(today, iso);
  const d = n === 0 ? "Hoje" : n === 1 ? "Amanhã" : n === -1 ? "Ontem" : dshort(iso);
  return hora ? `${d} · ${hora}` : d;
}
function diasChip(n, kind) {
  if (n < 0) return `<span class="chip neg">${kind === "tarefa" || kind === "conta" ? "atrasada" : "passou"}</span>`;
  if (n === 0) return `<span class="chip warn">hoje</span>`;
  if (n === 1) return `<span class="chip warn">amanhã</span>`;
  if (n <= 7) return `<span class="chip blue">em ${n} dias</span>`;
  return `<span class="chip">em ${n} dias</span>`;
}
const KIND_IC = { nota: "clock", tarefa: "list", facul: "cap", conta: "bills" };
const curtoCurso = c => String(c || "").replace(/^Engenharia (de )?/i, "Eng. ");
const isCheck = h => Number(h.meta) === 1 && Number(h.passo) === 1;
const AVISOS_OPC = [[1, "1 dia"], [2, "2 dias"], [3, "3 dias"], [7, "1 semana"]];

// ---------- arquivos (fotos e laudos) ----------
function readDataURL(blob) {
  return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result)); r.onerror = () => rej(new Error("Não consegui ler o arquivo")); r.readAsDataURL(blob); });
}
async function shrinkImage(file, max = 1600) {
  const url = await readDataURL(file);  // data: (a política de segurança do app não aceita blob:)
  const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error("Imagem inválida")); i.src = url; });
  const k = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(img.naturalWidth * k)); c.height = Math.max(1, Math.round(img.naturalHeight * k));
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  const blob = await new Promise(res => c.toBlob(res, "image/jpeg", 0.82));
  return blob ? new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }) : file;
}
async function uploadPick(pick) {
  if (!pick) return null;
  if (!pick.file) return pick.keep || null;
  let f = pick.file;
  if (f.type.startsWith("image/")) f = await shrinkImage(f);
  else if (f.type !== "application/pdf") throw new Error("Envie uma foto ou um PDF");
  if (f.size > 8 * 1024 * 1024) throw new Error("Arquivo muito grande (máximo 8 MB)");
  toast("Enviando arquivo…");
  const dados = (await readDataURL(f)).split(",")[1];
  return (await api("/api/arquivos", { nome: f.name, mime: f.type, dados })).id;
}
const fileLink = (id, mime, label) => mime && mime.startsWith("image/")
  ? `<a class="thumb" href="/api/arquivo/${id}" target="_blank" rel="noopener"><img src="/api/arquivo/${id}" alt="${esc(label || "Foto")}" loading="lazy"></a>`
  : `<a class="btn secondary sm" href="/api/arquivo/${id}" target="_blank" rel="noopener">${icon("file")}${esc(label || "Abrir PDF")}</a>`;

// ---------- peças compartilhadas ----------
function weekDots(w, today) {
  return `<div class="wdots" role="img" aria-label="${w.feitos} treinos nesta semana">${w.dias.map(d => {
    const cls = d.n ? "on" : d.date > today ? "fut" : "";
    return `<div class="wd ${cls} ${d.date === today ? "today" : ""}"><i>${d.n ? icon("check") : ""}</i><span>${DSEM[parse(d.date).getDay()][0].toUpperCase()}</span></div>`;
  }).join("")}</div>`;
}
function habitRow(h, dia) {
  const v = Number((h.dias || {})[dia] || 0), meta = Number(h.meta), ok = v >= meta;
  const streak = h.sequencia > 1 ? `<span class="chip warn">${icon("flame")}${h.sequencia} dias</span>` : "";
  if (isCheck(h)) {
    return `<div class="hab"><div class="hb-body"><div class="t">${esc(h.nome)} ${streak}</div><div class="sub">${ok ? "Feito" : "Ainda não"}</div>
      ${h.detalhe ? `<div class="sub muted hab-det">${esc(h.detalhe)}</div>` : ""}</div>
      <input type="checkbox" class="check" data-chg="hab" data-id="${h.id}" data-date="${dia}" ${ok ? "checked" : ""} aria-label="${esc(h.nome)}"></div>`;
  }
  return `<div class="hab"><div class="hb-body"><div class="t">${esc(h.nome)} ${streak}</div>
      <button type="button" class="hb-v" data-act="habset" data-id="${h.id}" data-date="${dia}" aria-label="Informar ${esc(h.nome)}"><b class="num ${ok ? "pos" : ""}">${fmtN(v)}</b><span class="muted num"> / ${fmtN(meta)}${h.unidade ? " " + esc(h.unidade) : ""}</span></button>
      <div class="bar ${ok ? "ok" : ""} thin"><i data-w="${Math.min(100, v / meta * 100)}"></i></div></div>
    <div class="stepper"><button type="button" data-act="habdelta" data-id="${h.id}" data-date="${dia}" data-d="${-h.passo}" aria-label="Menos ${esc(h.nome)}" ${v <= 0 ? "disabled" : ""}>${icon("minus")}</button>
      <button type="button" class="plus" data-act="habdelta" data-id="${h.id}" data-date="${dia}" data-d="${h.passo}" aria-label="Mais ${esc(h.nome)}">${icon("plus")}</button></div></div>`;
}
function agendaItems(d, ate) {
  const t = S.m.hoje;
  const contas = (d.vencimentos || []).map(v => ({ kind: "conta", id: v.key, titulo: v.name, date: v.due, hora: null,
    sub: brl(v.amount) + (v.estimated ? " · previsto" : ""), dias: v.dias }));
  return S.m.agenda.proximos.concat(contas).filter(i => i.dias <= ate)
    .sort((a, b) => (a.date + (a.hora || "99")).localeCompare(b.date + (b.hora || "99")) || a.kind.localeCompare(b.kind))
    .map(i => ({ ...i, dias: daysBetween(t, i.date) }));
}
function proxRow(i, today) {
  const body = `<span class="body"><span class="t">${esc(i.titulo)}</span><span class="sub">${esc(quandoTxt(i.date, i.hora, today))}${i.sub ? " · " + esc(i.sub) : ""}</span></span>${diasChip(i.dias, i.kind)}`;
  if (i.kind === "tarefa") {
    return `<div class="item"><input type="checkbox" class="check" data-chg="tarefa" data-id="${i.id}" aria-label="Concluir ${esc(i.titulo)}"><button type="button" class="rowbtn" data-act="edittarefa" data-id="${i.id}">${body}</button></div>`;
  }
  return `<button type="button" class="item" data-act="openitem" data-kind="${i.kind}" data-id="${esc(String(i.id))}"><span class="ic ${i.dias < 0 ? "neg" : i.dias <= 1 ? "warn" : "blue"}">${icon(KIND_IC[i.kind])}</span>${body}</button>`;
}
function tarefaRow(x, today) {
  const n = x.prazo ? daysBetween(today, x.prazo) : null;
  return `<div class="item tarefa ${x.feito_em ? "done" : ""}">
    <input type="checkbox" class="check" data-chg="tarefa" data-id="${x.id}" ${x.feito_em ? "checked" : ""} aria-label="Concluir ${esc(x.titulo)}">
    <button type="button" class="rowbtn" data-act="edittarefa" data-id="${x.id}"><span class="body"><span class="t">${x.importante ? `<span class="star">${icon("star")}</span>` : ""}${esc(x.titulo)}</span>
      <span class="sub">${esc(x.pasta)}${x.prazo ? " · " + esc(quandoTxt(x.prazo, null, today)) : ""}${x.nota ? " · " + esc(x.nota.slice(0, 40)) : ""}</span></span>
      ${!x.feito_em && n != null && n <= 1 ? diasChip(n, "tarefa") : ""}</button></div>`;
}
function emptyBox(ic, txt, btn) { return `<div class="empty">${icon(ic)}<div>${txt}</div>${btn || ""}</div>`; }
function chipsFilter(act, cur, opts) {
  return `<div class="chips-pick filter" role="tablist">${opts.map(([v, l]) => `<button type="button" role="tab" data-act="${act}" data-v="${esc(v)}" class="${cur === v ? "on" : ""}" aria-selected="${cur === v}">${esc(l)}</button>`).join("")}</div>`;
}

// ---------- Hoje ----------
VIEWS.hoje = d => {
  const m = S.m, t = m.hoje, dt = parse(t), w = d.semana, s = m.saude, ag = m.agenda;
  const over = w.gasto > w.teto, pct = w.teto ? w.gasto / w.teto * 100 : 0;
  const venc = (d.vencimentos || [])[0];
  const prox = agendaItems(d, 7).filter(i => i.kind !== "tarefa");
  const tarefas = ag.tarefas.filter(x => !x.feito_em && (x.prazo ? x.prazo <= t : x.importante)).slice(0, 8);
  const habs = s.habitos.filter(h => h.ativo);
  const wk = s.semana;
  const pushAsk = !S.pushOn && "Notification" in window && Notification.permission !== "denied";
  return `
  <div class="page-head"><div class="eyebrow">${DSEM_LONG[dt.getDay()]}, ${dt.getDate()} de ${MES[dt.getMonth()]}</div><h1>${greet(d)}</h1></div>
  <div class="grid dash">
    <div class="stack">
      <a class="card modcard" href="#inicio">
        <div class="mc-head"><span class="mc-ic fin">${icon("wallet")}</span><span class="eyebrow">Finanças · semana</span>${icon("right")}</div>
        <div class="mc-big num ${over ? "neg" : ""}">${brl(over ? w.estourou : w.restante)}</div>
        <div class="sub muted">${over ? "acima do teto da semana" : "ainda pode gastar até domingo"} · ${brl0(w.gasto)} de ${brl0(w.teto)}</div>
        <div class="bar ${over ? "over" : ""}"><i data-w="${pct}"></i></div>
        ${venc ? `<div class="mc-foot">${icon("bills")}<span>${esc(venc.name)} ${venc.dias < 0 ? "atrasada" : venc.dias === 0 ? "vence hoje" : venc.dias === 1 ? "vence amanhã" : `vence em ${venc.dias} dias`} · <b class="num">${brl(venc.amount)}</b></span></div>` : ""}
      </a>
      <section class="card">
        <div class="card-head"><div><div class="eyebrow">Treino da semana</div><div class="v-big num">${wk.feitos} <span class="muted">de ${s.meta_semana}</span></div></div>
          ${s.sequencia ? `<span class="chip warn">${icon("flame")}${s.sequencia} ${s.sequencia === 1 ? "semana" : "semanas"}</span>` : ""}</div>
        ${weekDots(wk, t)}
        <div class="sub muted mt">${wk.faltam ? `Faltam ${wk.faltam} · restam ${wk.restam_dias} ${wk.restam_dias === 1 ? "dia" : "dias"} na semana` : "Meta da semana batida!"}</div>
        <div class="mt">${wk.hoje ? `<button type="button" class="btn secondary block" data-act="treinodet">${icon("check")}Treinou hoje${wk.hoje > 1 ? ` (${wk.hoje}x)` : ""}</button>`
          : `<button type="button" class="btn block" data-act="checkin">${icon("dumbbell")}Treinei hoje</button>`}</div>
      </section>
      ${habs.length ? `<section class="card"><div class="card-head"><h3>Hábitos de hoje</h3><a class="btn ghost sm" href="#habitos">Ver tudo</a></div>${habs.map(h => habitRow(h, t)).join("")}</section>` : ""}
    </div>
    <div class="stack">
      <section class="card flush"><div class="card-head pad"><h3>Próximos 7 dias</h3><a class="btn ghost sm" href="#lembretes">Agenda</a></div>
        ${prox.length ? `<div class="list">${prox.map(i => proxRow(i, t)).join("")}</div>` : `<div class="empty sm">Nada marcado para os próximos dias.<div class="btn-row center"><button type="button" class="btn sm secondary" data-act="addnota">${icon("plus")}Lembrete</button></div></div>`}
      </section>
      <section class="card flush"><div class="card-head pad"><h3>Tarefas</h3><a class="btn ghost sm" href="#tarefas">Todas</a></div>
        <form class="quickadd" data-form="tarefa" data-prazo="hoje"><input class="input" name="titulo" maxlength="160" placeholder="Nova tarefa para hoje…" aria-label="Nova tarefa para hoje" autocomplete="off"><button class="iconbtn" aria-label="Adicionar">${icon("plus")}</button></form>
        ${tarefas.length ? `<div class="list">${tarefas.map(x => tarefaRow(x, t)).join("")}</div>` : `<div class="empty sm">Sem tarefas para hoje.</div>`}
      </section>
      ${pushAsk ? notice("", "bell", `<b>Ative as notificações</b> para receber lembretes, avisos de treino e de contas.`, `<div><button class="btn sm" data-act="pushon">Ativar neste aparelho</button></div>`) : ""}
    </div>
  </div>`;
};

// ---------- Saúde: treino ----------
function heatmap(s) {
  const ini = parse(s.heat_inicio), max = Math.max(1, ...s.heat);
  const cells = s.heat.map((n, i) => {
    const dt = new Date(ini); dt.setDate(ini.getDate() + i);
    const iso = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
    return `<i class="l${n ? Math.min(3, Math.ceil(n / max * 3)) : 0}" title="${ddmm(iso)}: ${n ? n + " treino(s)" : "sem treino"}"></i>`;
  }).join("");
  const months = [];
  for (let wk = 0; wk * 7 < s.heat.length; wk++) {
    const dt = new Date(ini); dt.setDate(ini.getDate() + wk * 7);
    const prev = new Date(dt); prev.setDate(dt.getDate() - 7);
    months.push(wk === 0 || prev.getMonth() !== dt.getMonth() ? `<span data-col="${wk + 1}">${MES[dt.getMonth()].slice(0, 3)}</span>` : "");
  }
  return `<div class="heat-wrap" data-weeks="${Math.ceil(s.heat.length / 7)}"><div class="heat-m">${months.join("")}</div><div class="heat">${cells}</div></div>
    <div class="legend heat-leg"><span>menos</span><i class="l0"></i><i class="l1"></i><i class="l2"></i><i class="l3"></i><span>mais</span></div>`;
}
const DIAS_ROTINA = ["A", "B", "C"];
function rotinaCard() {
  const s = S.m.saude, rot = s.rotina || {};
  if (!DIAS_ROTINA.some(d => (rot[d] || []).length)) return "";
  const dia = d => `<section class="card flush"><div class="card-head pad"><h3>Dia ${d} · ${esc(s.foco_dia[d] || "")}</h3>
      <button type="button" class="btn sm ghost" data-act="addex" data-dia="${d}">${icon("plus")}</button></div>
    <div class="list">${(rot[d] || []).map(ex => `<button type="button" class="item" data-act="exdet" data-id="${ex.id}">
      <span class="body"><span class="t">${esc(ex.exercicio)}</span><span class="sub">${ex.series ? `${ex.series}x${esc(ex.reps || "")}` : ""}</span></span>
      ${ex.ultimo ? `<span class="amt num">${fmtN(ex.ultimo.carga)} kg</span>` : `<span class="sub muted">sem registro</span>`}
    </button>`).join("") || emptyBox("dumbbell", "Nenhum exercício aqui ainda.")}</div></section>`;
  return `<h2 class="sec">Minha rotina</h2><div class="grid rotina">${DIAS_ROTINA.map(dia).join("")}</div>`;
}
function checkinSheet(d) {
  const s = S.m.saude, lista = (s.rotina || {})[d] || [];
  if (!lista.length) { A._checkinRapido(); return; }
  const fields = [
    { name: "_t", type: "section", label: `Dia ${d} — ${s.foco_dia[d] || ""}` },
    ...lista.map(ex => ({ name: "ex_" + ex.id, label: `${ex.exercicio}${ex.series ? ` (${ex.series}x${ex.reps || ""})` : ""} — kg`,
                          type: "decimal", placeholder: ex.ultimo ? `últ. ${fmtN(ex.ultimo.carga)}` : "" })),
    { name: "nota", label: "Observação", type: "textarea", rows: 2, full: true },
  ];
  openSheet({ title: `Treinei hoje`, fields, pairs: true, reload: reloadM,
    onSave: async v => {
      const series = lista.filter(ex => v["ex_" + ex.id] != null && v["ex_" + ex.id] !== "")
        .map(ex => ({ exercicio: ex.exercicio, carga: v["ex_" + ex.id], reps: ex.reps }));
      await api("/api/treinos", { tipo: "Musculação", dia: d, foco: s.foco_dia[d], nota: v.nota, series });
      return `Treino dia ${d} registrado`;
    } });
}
function exercicioSheet(dia, ex) {
  openSheet({ title: ex ? "Exercício" : `Novo exercício (dia ${dia})`, reload: reloadM,
    fields: [
      { name: "exercicio", label: "Nome", required: true, full: true },
      { name: "series", label: "Séries", type: "number", min: 1, max: 20 },
      { name: "reps", label: "Repetições", placeholder: "Ex.: 8-12" },
    ],
    values: ex || {},
    onSave: v => api("/api/rotina", { ...(ex ? { id: ex.id } : { dia }), exercicio: v.exercicio, series: v.series, reps: v.reps }),
    onDelete: ex ? () => api("/api/rotina", { id: ex.id, delete: true }) : null });
}
VIEWS.treino = () => {
  const s = S.m.saude, w = s.semana, t = S.m.hoje;
  const byMonth = {};
  s.treinos.forEach(x => (byMonth[x.date.slice(0, 7)] = byMonth[x.date.slice(0, 7)] || []).push(x));
  const grupo = ([ym, l]) => `<div class="group-label row"><span>${esc(monthName(ym))}</span><span>${new Set(l.map(x => x.date)).size} dias</span></div>`
    + l.map(x => `<button type="button" class="item" data-act="edittreino" data-id="${x.id}"><span class="ic blue">${icon("dumbbell")}</span>
      <span class="body"><span class="t">${esc(x.tipo)}${x.foco ? " · " + esc(x.foco) : ""}</span><span class="sub">${esc(relDay(x.date, t))}${x.minutos ? ` · ${x.minutos} min` : ""}${x.arquivo_id ? " · foto" : ""}${x.nota ? " · " + esc(x.nota.slice(0, 40)) : ""}</span></span></button>`).join("");
  const meses = Object.entries(byMonth);
  const hist = meses.slice(0, 2).map(grupo).join("")
    + (meses.length > 2 ? `<details class="more"><summary>Meses anteriores (${meses.length - 2})</summary>${meses.slice(2).map(grupo).join("")}</details>` : "");
  const stat = (k, v, sub, ic) => `<div class="card stat"><div class="k">${ic ? icon(ic) : ""}${k}</div><div class="v num">${v}</div><div class="s">${sub}</div></div>`;
  return `<div class="grid dash">
    <div class="stack">
      <section class="card hero">
        <div class="label">Treinos nesta semana</div>
        <div class="big num">${w.feitos} <small>de ${s.meta_semana}</small></div>
        ${weekDots(w, t)}
        <div class="meta">${w.faltam ? `Faltam ${w.faltam} · restam ${w.restam_dias} ${w.restam_dias === 1 ? "dia" : "dias"} até domingo` : "Meta da semana batida!"}</div>
        ${s.proximo_dia ? `<div class="meta">Próximo: <b>dia ${s.proximo_dia}</b> — ${esc(s.foco_dia[s.proximo_dia] || "")}</div>
          <div class="chips-pick dia-pick">${DIAS_ROTINA.map(d => `<button type="button" class="${d === s.proximo_dia ? "on" : ""}" data-act="checkindia" data-dia="${d}">${d}</button>`).join("")}</div>` : ""}
        <div class="hero-actions">${w.hoje ? `<button type="button" class="btn light sm" data-act="treinodet">${icon("check")}Treinou hoje</button><button type="button" class="btn glass sm" data-act="checkin">Mais um</button>`
          : `<button type="button" class="btn light" data-act="checkin">${icon("dumbbell")}Treinei hoje</button>`}<button type="button" class="btn glass sm" data-act="addtreino">Outro dia</button></div>
      </section>
      ${s.sugestao ? notice(s.sugestao.tipo === "ok" ? "" : "warn", "chart",
        `<b>${esc(s.sugestao.titulo)}</b><p class="sub">${esc(s.sugestao.texto)}</p>`,
        `<div><button type="button" class="btn sm" data-act="sugestaook">Entendi</button></div>`) : ""}
      ${rotinaCard()}
      <div class="grid three">
        ${stat("Sequência", `${s.sequencia}`, s.sequencia === 1 ? "semana na meta" : "semanas na meta", "flame")}
        ${stat("Este mês", `${s.mes}`, s.mes === 1 ? "dia de treino" : "dias de treino")}
        ${stat("Este ano", `${s.ano}`, s.ano === 1 ? "dia de treino" : "dias de treino")}
      </div>
      <section class="card"><div class="card-head"><h3>Últimos 4 meses</h3><span class="sub muted">${s.heat.filter(Boolean).length} dias</span></div>${heatmap(s)}</section>
    </div>
    <div class="stack">
      <h2 class="sec">Histórico${s.sem_treinar != null && s.sem_treinar > 1 ? `<span class="muted">último há ${s.sem_treinar} dias</span>` : ""}</h2>
      ${s.treinos.length ? `<section class="card flush"><div class="list">${hist}</div></section>` : emptyBox("dumbbell", "Nenhum treino ainda. Toque em <b>Treinei hoje</b> depois da academia.")}
    </div>
  </div>`;
};
const FOCOS = ["Peito e tríceps", "Costas e bíceps", "Pernas", "Ombros", "Braços", "Full body", "Abdômen"];
const treinoFields = s => [
  { name: "tipo", label: "Tipo", type: "chips", options: s.tipos.slice(0, 8), required: true, full: true },
  { name: "foco", label: "O que treinou", placeholder: "Ex.: peito e tríceps", suggest: FOCOS, full: true },
  { name: "date", label: "Dia", type: "date" },
  { name: "minutos", label: "Duração (min)", type: "number", min: 1, max: 600 },
  { name: "foto", label: "Foto (opcional)", type: "file", accept: "image/*", keepLabel: "Foto anexada", full: true },
  { name: "nota", label: "Observação", type: "textarea", rows: 2, full: true },
];
function treinoSheet(x) {
  const s = S.m.saude;
  openSheet({ title: x ? "Treino" : "Registrar treino", fields: treinoFields(s), pairs: true, reload: reloadM,
    values: x ? { ...x, foto: x.arquivo_id } : { tipo: s.ultimo_tipo, date: S.m.hoje },
    onSave: async v => {
      const arquivo_id = await uploadPick(v.foto);
      await api("/api/treinos", { ...(x ? { id: x.id } : {}), date: v.date || S.m.hoje, tipo: v.tipo, foco: v.foco, minutos: v.minutos, nota: v.nota, arquivo_id });
      return x ? "Treino atualizado" : "Treino registrado";
    },
    onDelete: x ? () => api("/api/treinos", { id: x.id, delete: true }) : null });
}

// ---------- Saúde: corpo ----------
const CURTO = { biceps_d: "Bíceps dir.", biceps_e: "Bíceps esq.", coxa_d: "Coxa dir.", coxa_e: "Coxa esq.", gordura_pct: "Gordura",
  musculo_kg: "Massa muscular", massa_magra_kg: "Massa magra", agua_pct: "Água", visceral: "Gord. visceral", ossea_kg: "Massa óssea",
  tmb: "Metab. basal", idade_metabolica: "Idade metab." };
const MED_CAMPOS = ["peso", "altura", "biceps_d", "biceps_e", "peito", "cintura", "abdomen", "quadril", "coxa_d", "coxa_e", "panturrilha"];
const BIO_CAMPOS = ["peso", "gordura_pct", "musculo_kg", "massa_magra_kg", "agua_pct", "visceral", "ossea_kg", "tmb", "idade_metabolica"];
function deltaChip(c, k) {
  const u = c.ultimo[k]; if (!u || u.delta == null || u.delta === 0) return "";
  const better = c.campos[k].melhor, good = better === 0 ? null : (u.delta > 0) === (better > 0);
  return `<span class="chip ${good == null ? "" : good ? "pos" : "neg"} num">${u.delta > 0 ? "+" : "−"}${fmtN(Math.abs(u.delta))}</span>`;
}
function imcFaixa(v) { return v < 18.5 ? "abaixo do peso" : v < 25 ? "peso normal" : v < 30 ? "sobrepeso" : "obesidade"; }
function medidaRows(c, keys) {
  return keys.filter(k => c.ultimo[k]).map(k => `<button type="button" class="item" data-act="campo" data-k="${k}">
    <span class="body"><span class="t">${esc(c.campos[k].nome)}</span><span class="sub">${dmy(c.ultimo[k].date)}</span></span>
    ${deltaChip(c, k)}<span class="amt num">${withU(c.ultimo[k].valor, c.campos[k].unidade)}</span></button>`).join("");
}
VIEWS.corpo = () => {
  const c = S.m.saude.corpo, u = c.ultimo;
  const has = Object.keys(u).length;
  const stat = (k, v, sub, extra) => `<button type="button" class="card stat tap" ${extra || ""}><div class="k">${k}</div><div class="v num">${v}</div><div class="s srow">${sub}</div></button>`;
  const medKeys = MED_CAMPOS.filter(k => !["peso", "altura"].includes(k));
  const bioKeys = BIO_CAMPOS.filter(k => k !== "peso");
  const avs = c.avaliacoes.map(a => {
    const n = Object.keys(a.valores).length;
    return `<button type="button" class="item" data-act="avaliacao" data-id="${a.id}"><span class="ic ${a.tipo === "bio" ? "blue" : ""}">${icon(a.tipo === "bio" ? "file" : "ruler")}</span>
      <span class="body"><span class="t">${a.tipo === "bio" ? "Bioimpedância" : "Medidas"}</span><span class="sub">${dmy(a.date)} · ${n} ${n === 1 ? "valor" : "valores"}${a.arquivo_id ? ` · ${a.arquivo_mime === "application/pdf" ? "PDF" : "foto"}` : ""}</span></span>${icon("right")}</button>`;
  }).join("");
  return `<div class="stack"><div class="btn-row"><button type="button" class="btn" data-act="addmedida">${icon("ruler")}Nova medição</button><button type="button" class="btn secondary" data-act="addbio">${icon("file")}Bioimpedância</button></div>
  ${!has ? emptyBox("ruler", "Registre peso, altura e medidas para acompanhar a evolução. A bioimpedância pode ir com o laudo em foto ou PDF.") : `
  <div class="grid three">
    ${u.peso ? stat("Peso", `${fmtN(u.peso.valor)} kg`, `${deltaChip(c, "peso")}<span>${ddmm(u.peso.date)}</span>`, `data-act="campo" data-k="peso"`) : ""}
    ${c.imc ? stat("IMC", fmtN(c.imc), imcFaixa(c.imc)) : u.altura ? stat("Altura", `${fmtN(u.altura.valor)} cm`, dmy(u.altura.date), `data-act="campo" data-k="altura"`) : ""}
    ${u.gordura_pct ? stat("Gordura", `${fmtN(u.gordura_pct.valor)}%`, `${deltaChip(c, "gordura_pct")}<span>${ddmm(u.gordura_pct.date)}</span>`, `data-act="campo" data-k="gordura_pct"`)
      : u.altura && c.imc ? stat("Altura", `${fmtN(u.altura.valor)} cm`, dmy(u.altura.date), `data-act="campo" data-k="altura"`) : ""}
  </div>
  <div class="grid cols2">
    ${medKeys.some(k => u[k]) ? `<section class="card flush"><div class="card-head pad"><h3>Medidas</h3></div><div class="list">${medidaRows(c, medKeys)}</div></section>` : ""}
    ${bioKeys.some(k => u[k]) ? `<section class="card flush"><div class="card-head pad"><h3>Bioimpedância</h3></div><div class="list">${medidaRows(c, bioKeys)}</div></section>` : ""}
  </div>`}
  ${avs ? `<h2 class="sec">Histórico</h2><section class="card flush"><div class="list">${avs}</div></section>` : ""}</div>`;
};
function lineChart(pts, unidade) {
  if (pts.length < 2) return "";
  const W = 320, H = 140, P = 28;
  const xs = pts.map(p => parse(p[0]).getTime()), ys = pts.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sx = x => P + (x1 === x0 ? (W - 2 * P) / 2 : (x - x0) / (x1 - x0) * (W - 2 * P));
  const pad = (y1 - y0) * 0.15 || 1, lo = y0 - pad, hi = y1 + pad;
  const sy = y => H - 20 - (y - lo) / (hi - lo) * (H - 36);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(" ");
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Evolução">
    <line class="axis" x1="${P}" x2="${W - P}" y1="${H - 20}" y2="${H - 20}"/>
    <path class="acc" d="${path}"/>${pts.map((p, i) => `<circle class="accdot" cx="${sx(xs[i]).toFixed(1)}" cy="${sy(p[1]).toFixed(1)}" r="3.5"/>`).join("")}
    <text class="val" x="${sx(xs[0])}" y="${sy(ys[0]) - 8}" text-anchor="start">${fmtN(ys[0])}</text>
    <text class="val" x="${sx(xs[xs.length - 1])}" y="${sy(ys[ys.length - 1]) - 8}" text-anchor="end">${fmtN(ys[ys.length - 1])}</text>
    <text class="lbl" x="${P}" y="${H - 4}">${ddmm(pts[0][0])}</text><text class="lbl" x="${W - P}" y="${H - 4}" text-anchor="end">${ddmm(pts[pts.length - 1][0])}</text>
  </svg></div><div class="legend"><span>${esc(unidade)}</span></div>`;
}
function medidaSheet(tipo, a) {
  const c = S.m.saude.corpo, keys = tipo === "bio" ? BIO_CAMPOS : MED_CAMPOS;
  const fields = [{ name: "date", label: "Data", type: "date", full: true }];
  if (tipo === "bio") fields.push({ name: "laudo", label: "Laudo (foto ou PDF)", type: "file", keepLabel: "Laudo anexado", full: true, hint: "Opcional. Fica guardado junto da avaliação." });
  keys.forEach(k => fields.push({ name: k, label: `${CURTO[k] || c.campos[k].nome}${c.campos[k].unidade ? ` (${c.campos[k].unidade})` : " (nível)"}`, type: "decimal", placeholder: c.ultimo[k] ? fmtN(c.ultimo[k].valor) : "" }));
  fields.push({ name: "nota", label: "Observação", type: "textarea", rows: 2, full: true });
  const vals = a ? { date: a.date, laudo: a.arquivo_id, nota: a.nota, ...a.valores } : { date: S.m.hoje, ...(tipo === "medidas" && c.ultimo.altura ? { altura: c.ultimo.altura.valor } : {}) };
  openSheet({ title: tipo === "bio" ? "Bioimpedância" : "Medidas", desc: a ? "" : "Preencha só o que mediu. Os campos em branco ficam de fora.",
    fields, values: vals, pairs: true, reload: reloadM,
    onSave: async v => {
      const valores = {}; keys.forEach(k => { if (v[k] != null) valores[k] = v[k]; });
      const arquivo_id = tipo === "bio" ? await uploadPick(v.laudo) : (a ? a.arquivo_id : null);
      await api("/api/avaliacoes", { ...(a ? { id: a.id } : {}), tipo, date: v.date || S.m.hoje, valores, arquivo_id, nota: v.nota });
      return "Medição salva";
    },
    onDelete: a ? () => api("/api/avaliacoes", { id: a.id, delete: true }) : null });
}

// ---------- Saúde: hábitos ----------
VIEWS.habitos = () => {
  const s = S.m.saude, t = S.m.hoje, dia = S.habDia || t;
  const hs = s.habitos.filter(h => h.ativo), off = s.habitos.filter(h => !h.ativo);
  const n = daysBetween(dia, t);
  const mini = h => `<div class="hmini" aria-label="Últimos 7 dias">${h.semana.map((v, i) => `<i class="${v >= h.meta ? "ok" : v > 0 ? "part" : ""}" title="${v}"></i>`).join("")}</div>`;
  return `<div class="monthnav"><button type="button" class="iconbtn" data-act="habnav" data-d="-1" aria-label="Dia anterior" ${n >= 30 ? "disabled" : ""}>${icon("left")}</button>
    <div class="m-wrap"><div class="m">${n === 0 ? "Hoje" : n === 1 ? "Ontem" : esc(DSEM_LONG[parse(dia).getDay()])}</div><div class="m-sub">${dmy(dia)}</div></div>
    <button type="button" class="iconbtn" data-act="habnav" data-d="1" aria-label="Próximo dia" ${n <= 0 ? "disabled" : ""}>${icon("right")}</button></div>
  ${hs.length ? `<div class="grid cols2">${hs.map(h => `<section class="card habcard">
      ${habitRow(h, dia)}
      <div class="hc-foot">${mini(h)}<span class="sub muted">${h.semana_ok} de 7 dias na meta</span><button type="button" class="btn ghost sm" data-act="edithab" data-id="${h.id}">Editar</button></div>
    </section>`).join("")}</div>` : emptyBox("drop", "Nenhum hábito ativo.")}
  <div class="btn-row center mt"><button type="button" class="btn secondary" data-act="addhab">${icon("plus")}Novo hábito</button></div>
  ${off.length ? `<h2 class="sec">Pausados</h2><section class="card flush"><div class="list">${off.map(h => `<button type="button" class="item" data-act="edithab" data-id="${h.id}"><span class="ic">${icon("drop")}</span><span class="body"><span class="t">${esc(h.nome)}</span><span class="sub">pausado</span></span></button>`).join("")}</div></section>` : ""}`;
};
function habSheet(h) {
  openSheet({ title: h ? h.nome : "Novo hábito", desc: "Para um hábito de marcar (ex.: creatina, leitura), use meta 1 e passo 1.", pairs: true, reload: reloadM,
    fields: [
      { name: "nome", label: "Nome", required: true, full: true, placeholder: "Ex.: Água" },
      { name: "meta", label: "Meta por dia", type: "decimal", required: true },
      { name: "unidade", label: "Unidade", placeholder: "copos", suggest: ["copos", "h", "min", "páginas", "vezes"] },
      { name: "passo", label: "Cada toque no + soma", type: "decimal", required: true, full: true },
      ...(h ? [{ name: "ativo", label: "Ativo", type: "checkbox", hint: "Desligue para pausar sem perder o histórico", full: true }] : []),
    ],
    values: h ? { ...h, ativo: !!h.ativo } : { meta: 1, passo: 1 },
    onSave: v => api("/api/habitos", { ...(h ? { id: h.id, ativo: v.ativo } : {}), nome: v.nome, unidade: v.unidade, meta: v.meta, passo: v.passo }),
    onDelete: h ? () => api("/api/habitos", { id: h.id, delete: true }) : null });
}
async function habSet(id, date, body) {
  const h = S.m.saude.habitos.find(x => x.id === id);
  if (h) {  // mostra na hora; o servidor confirma em seguida
    const cur = Number((h.dias || {})[date] || 0);
    const nv = Math.max(0, "delta" in body ? cur + body.delta : body.valor);
    h.dias = { ...(h.dias || {}), [date]: nv }; if (date === S.m.hoje) h.hoje = nv;
    render();
  }
  buzz();
  try { await api("/api/habito_dia", { habito_id: id, date, ...body }); } catch (e) { toast(e.message); }
  reloadM();
}

// ---------- Agenda: próximos ----------
VIEWS.lembretes = d => {
  const t = S.m.hoje, items = agendaItems(d, 60);
  const grupos = [["Atrasado", i => i.dias < 0], ["Hoje", i => i.dias === 0], ["Amanhã", i => i.dias === 1],
    ["Próximos 7 dias", i => i.dias > 1 && i.dias <= 7], ["Mais para frente", i => i.dias > 7]];
  const body = grupos.map(([nome, f]) => { const l = items.filter(f); return l.length ? `<div class="group-label">${nome}</div>${l.map(i => proxRow(i, t)).join("")}` : ""; }).join("");
  return `<div class="btn-row quick"><button type="button" class="btn sm secondary" data-act="addnota">${icon("clock")}Lembrete</button><button type="button" class="btn sm secondary" data-act="addtarefa">${icon("list")}Tarefa</button><button type="button" class="btn sm secondary" data-act="addfacul">${icon("cap")}Prova</button></div>
  ${body ? `<section class="card flush"><div class="list">${body}</div></section>`
    : emptyBox("calendar", "Nada marcado. Crie um lembrete com data e o app avisa 1, 2 ou 3 dias antes.")}
  <p class="foot">Inclui contas a vencer, tarefas com prazo, provas e lembretes com data.</p>`;
};

// ---------- Agenda: notas ----------
function notaMatches(n) {
  const q = (S.notaQ || "").trim().toLowerCase();
  return (!S.notaPasta || n.pasta === S.notaPasta) && (!q || (n.titulo + " " + (n.texto || "")).toLowerCase().includes(q));
}
function notasList() {
  const t = S.m.hoje, ns = S.m.agenda.notas.filter(notaMatches);
  const abertas = ns.filter(n => !n.feito), feitas = ns.filter(n => n.feito);
  const card = n => `<button type="button" class="notecard ${n.feito ? "done" : ""}" data-act="editnota" data-id="${n.id}">
      <div class="nc-t">${n.fixada ? `<span class="pin">${icon("pin")}</span>` : ""}${esc(n.titulo)}</div>
      ${n.texto ? `<div class="nc-x">${esc(n.texto.slice(0, 220))}</div>` : ""}
      <div class="nc-f"><span class="chip">${icon("folder")}${esc(n.pasta)}</span>${n.quando ? `<span class="chip ${!n.feito && n.quando.slice(0, 10) >= t ? "blue" : ""}">${icon("clock")}${esc(quandoTxt(n.quando.slice(0, 10), n.quando.slice(11, 16) || null, t))}</span>` : ""}</div></button>`;
  if (!ns.length) return emptyBox("note", S.notaQ ? "Nada encontrado." : "Nenhuma nota nesta pasta.");
  return `<div class="notegrid">${abertas.map(card).join("")}</div>
    ${feitas.length ? `<details class="done-box"><summary>Concluídas (${feitas.length})</summary><div class="notegrid">${feitas.map(card).join("")}</div></details>` : ""}`;
}
VIEWS.notas = () => {
  const ag = S.m.agenda, cnt = p => ag.notas.filter(n => !n.feito && (!p || n.pasta === p)).length;
  const pastas = [...new Set(ag.pastas.concat(ag.notas.map(n => n.pasta)))];
  return `<div class="searchbar">${icon("search")}<input class="input" type="search" data-input="notaq" placeholder="Buscar nas notas" value="${esc(S.notaQ || "")}" aria-label="Buscar nas notas"></div>
    ${chipsFilter("notapasta", S.notaPasta || "", [["", `Todas · ${cnt("")}`]].concat(pastas.map(p => [p, `${p} · ${cnt(p)}`])))}
    <div id="notalist">${notasList()}</div>`;
};
function notaSheet(n) {
  const ag = S.m.agenda;
  const pastas = [...new Set(ag.pastas.concat(n ? [n.pasta] : []))];
  openSheet({ title: n ? "Nota" : "Nova nota", pairs: true, reload: reloadM,
    fields: [
      { name: "titulo", label: "Título", required: true, full: true, placeholder: "Ex.: Levar relatório da auditoria" },
      { name: "pasta", label: "Pasta", type: "select", options: pastas, full: true },
      { name: "texto", label: "Anotação", type: "textarea", rows: 5, full: true },
      { type: "section", label: "Lembrete" },
      { name: "data", label: "Lembrar em", type: "date" },
      { name: "hora", label: "Hora (opcional)", type: "time" },
      { name: "avisos", label: "Avisar antes", type: "multichips", options: AVISOS_OPC, full: true, hint: "No dia você sempre recebe o aviso. Com hora marcada, avisa também perto da hora." },
      { name: "fixada", label: "Fixar no topo", type: "checkbox", full: true },
      ...(n ? [{ name: "feito", label: "Concluída", type: "checkbox", hint: "Sai dos próximos e para de avisar", full: true }] : []),
    ],
    values: n ? { ...n, data: n.quando ? n.quando.slice(0, 10) : "", hora: n.quando && n.quando.length > 10 ? n.quando.slice(11, 16) : "", fixada: !!n.fixada, feito: !!n.feito }
      : { pasta: S.notaPasta || (S.view === "notas" ? pastas[0] : pastas.find(p => /pessoal/i.test(p)) || pastas[0]), avisos: ag.avisos },
    onSave: async v => {
      if (v.hora && !v.data) throw new Error("Escolha a data do lembrete");
      const quando = v.data ? (v.hora ? `${v.data}T${v.hora}` : v.data) : null;
      await api("/api/notas", { ...(n ? { id: n.id, feito: v.feito } : {}), titulo: v.titulo, pasta: v.pasta, texto: v.texto, quando, avisos: v.avisos, fixada: v.fixada });
      return quando ? `Lembrete para ${ddmm(quando.slice(0, 10))}` : "Nota salva";
    },
    onDelete: n ? () => api("/api/notas", { id: n.id, delete: true }) : null });
}

// ---------- Agenda: tarefas ----------
VIEWS.tarefas = () => {
  const ag = S.m.agenda, t = S.m.hoje, p = S.tarPasta || "";
  const l = ag.tarefas.filter(x => !p || x.pasta === p);
  const open = l.filter(x => !x.feito_em), done = l.filter(x => x.feito_em);
  const amanha = (() => { const d = parse(t); d.setDate(d.getDate() + 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; })();
  const grupos = [["Atrasadas", x => x.prazo && x.prazo < t], ["Hoje", x => x.prazo === t], ["Amanhã", x => x.prazo === amanha],
    ["Próximas", x => x.prazo && x.prazo > amanha], ["Sem prazo", x => !x.prazo]];
  const body = grupos.map(([nome, f]) => { const g = open.filter(f); return g.length ? `<div class="group-label row"><span>${nome}</span><span>${g.length}</span></div>${g.map(x => tarefaRow(x, t)).join("")}` : ""; }).join("");
  const pastas = [...new Set(ag.pastas.concat(ag.tarefas.map(x => x.pasta)))];
  return `${chipsFilter("tarpasta", p, [["", "Todas"]].concat(pastas.map(x => [x, x])))}
    <form class="quickadd card" data-form="tarefa" data-prazo="${esc(S.qaPrazo || "")}">
      <input class="input" name="titulo" maxlength="160" placeholder="Nova tarefa${p ? " em " + esc(p) : ""}…" aria-label="Nova tarefa" autocomplete="off">
      <div class="qa-row"><div class="seg">${[["", "Sem prazo"], ["hoje", "Hoje"], ["amanha", "Amanhã"]].map(([k, lb]) => `<button type="button" data-act="qaprazo" data-v="${k}" class="${(S.qaPrazo || "") === k ? "on" : ""}">${lb}</button>`).join("")}</div>
      <button class="btn sm">${icon("plus")}Adicionar</button></div></form>
    ${body ? `<section class="card flush"><div class="list">${body}</div></section>` : emptyBox("list", "Nenhuma tarefa aberta.")}
    ${done.length ? `<details class="done-box"><summary>Concluídas nos últimos 30 dias (${done.length})</summary><section class="card flush"><div class="list">${done.map(x => tarefaRow(x, t)).join("")}</div></section></details>` : ""}`;
};
function tarefaSheet(x) {
  const ag = S.m.agenda, pastas = [...new Set(ag.pastas.concat(x ? [x.pasta] : []))];
  openSheet({ title: x ? "Tarefa" : "Nova tarefa", reload: reloadM,
    fields: [
      { name: "titulo", label: "Tarefa", required: true, placeholder: "Ex.: Enviar programação de serviços" },
      { name: "pasta", label: "Pasta", type: "select", options: pastas },
      { name: "prazo", label: "Prazo (opcional)", type: "date" },
      { name: "importante", label: "Importante", type: "checkbox", hint: "Aparece no Hoje mesmo sem prazo" },
      { name: "nota", label: "Detalhes", type: "textarea", rows: 3 },
    ],
    values: x ? { ...x, importante: !!x.importante } : { pasta: S.tarPasta || pastas[0] },
    onSave: v => api("/api/tarefas", { ...(x ? { id: x.id } : {}), titulo: v.titulo, pasta: v.pasta, prazo: v.prazo || null, importante: v.importante, nota: v.nota }),
    onDelete: x ? () => api("/api/tarefas", { id: x.id, delete: true }) : null });
}

// ---------- Agenda: faculdade ----------
VIEWS.facul = () => {
  const ag = S.m.agenda, t = S.m.hoje, c = S.facCurso || "", TF = S.m.tipos_facul;
  const l = ag.facul.filter(f => !c || f.curso === c);
  const prox = l.filter(f => !f.feito).sort((a, b) => a.data.localeCompare(b.data));
  const feitas = l.filter(f => f.feito).sort((a, b) => b.data.localeCompare(a.data));
  const medias = {};
  feitas.filter(f => f.nota != null).forEach(f => { const k = f.disciplina; (medias[k] = medias[k] || []).push(f.nota / (f.nota_max || 10) * 10); });
  const row = f => { const n = daysBetween(t, f.data.slice(0, 10)); return `<button type="button" class="item" data-act="editfacul" data-id="${f.id}">
      <span class="ic ${f.feito ? "" : n <= 3 ? "warn" : "blue"}">${icon("cap")}</span>
      <span class="body"><span class="t">${esc(TF[f.tipo] || f.tipo)} · ${esc(f.disciplina)}${f.titulo ? " · " + esc(f.titulo) : ""}</span>
      <span class="sub">${esc(quandoTxt(f.data.slice(0, 10), f.data.slice(11, 16) || null, t))} · ${esc(curtoCurso(f.curso))}</span></span>
      ${f.feito ? (f.nota != null ? `<span class="amt num">${fmtN(f.nota)}${f.nota_max ? `<span class="muted">/${fmtN(f.nota_max)}</span>` : ""}</span>` : `<span class="chip pos">feito</span>`) : diasChip(n, "facul")}</button>`; };
  const cursos = [...new Set(ag.cursos.concat(ag.facul.map(f => f.curso)))];
  return `${cursos.length > 1 ? chipsFilter("faccurso", c, [["", "Todos"]].concat(cursos.map(x => [x, curtoCurso(x)]))) : ""}
    <div class="grid cols2">
      <div class="stack">
        <h2 class="sec">Próximas</h2>
        ${prox.length ? `<section class="card flush"><div class="list">${prox.map(row).join("")}</div></section>` : emptyBox("cap", "Nenhuma prova ou entrega marcada.", `<div class="btn-row center"><button type="button" class="btn sm" data-act="addfacul">${icon("plus")}Prova ou trabalho</button></div>`)}
      </div>
      <div class="stack">
        ${Object.keys(medias).length ? `<h2 class="sec">Médias</h2><section class="card">${Object.entries(medias).map(([k, v]) => { const m = v.reduce((a, b) => a + b, 0) / v.length; return `<div class="catrow wide"><span class="n">${esc(k)}</span><div class="bar ${m >= 6 ? "ok" : "warn"}"><i data-w="${m * 10}"></i></div><span class="num">${fmtN(Math.round(m * 10) / 10)}</span></div>`; }).join("")}<div class="sub muted">Na escala de 0 a 10, com as notas lançadas.</div></section>` : ""}
        ${feitas.length ? `<h2 class="sec">Feitas</h2><section class="card flush"><div class="list">${feitas.slice(0, 40).map(row).join("")}</div></section>` : ""}
      </div>
    </div>`;
};
function faculSheet(f) {
  const ag = S.m.agenda, TF = S.m.tipos_facul;
  const cursos = [...new Set(ag.cursos.concat(f ? [f.curso] : []))];
  const disc = [...new Set(ag.facul.map(x => x.disciplina))].slice(0, 12);
  if (!cursos.length) { toast("Cadastre seus cursos em Ajustes › Agenda"); return; }
  openSheet({ title: f ? `${TF[f.tipo]} · ${f.disciplina}` : "Prova ou trabalho", pairs: true, reload: reloadM,
    fields: [
      { name: "tipo", label: "Tipo", type: "chips", options: Object.values(TF), required: true, full: true },
      { name: "curso", label: "Curso", type: "select", options: cursos, full: true },
      { name: "disciplina", label: "Disciplina", required: true, full: true, suggest: disc, placeholder: "Ex.: Circuitos Elétricos II" },
      { name: "titulo", label: "Detalhe (opcional)", full: true, placeholder: "Ex.: P1 · capítulos 1 a 4" },
      { name: "data", label: "Data", type: "date", required: true },
      { name: "hora", label: "Hora (opcional)", type: "time" },
      { name: "feito", label: "Já fiz / entreguei", type: "checkbox", full: true },
      { name: "nota", label: "Nota", type: "decimal" },
      { name: "nota_max", label: "Vale", type: "decimal" },
      { name: "obs", label: "Observação", type: "textarea", rows: 2, full: true },
    ],
    values: f ? { ...f, tipo: TF[f.tipo], data: f.data.slice(0, 10), hora: f.data.length > 10 ? f.data.slice(11, 16) : "", feito: !!f.feito }
      : { curso: S.facCurso || cursos[0], nota_max: 10 },
    onSave: v => {
      if (!v.data) throw new Error("Informe a data");
      const tipo = Object.keys(TF).find(k => TF[k] === v.tipo) || "prova";
      return api("/api/facul", { ...(f ? { id: f.id } : {}), tipo, curso: v.curso, disciplina: v.disciplina, titulo: v.titulo,
        data: v.hora ? `${v.data}T${v.hora}` : v.data, feito: v.feito, nota: v.nota, nota_max: v.nota_max, obs: v.obs });
    },
    onDelete: f ? () => api("/api/facul", { id: f.id, delete: true }) : null });
}

// ---------- ações ----------
const findBy = (list, id) => list.find(x => String(x.id) === String(id));
Object.assign(A, {
  mod: el => {
    const m = MODS.find(x => x.id === el.dataset.m); if (!m) return;
    const dest = "#" + (S.tabs[m.id] || m.tabs[0][0]);
    if (location.hash === dest) { scrollTo(0, 0); render(); } else location.hash = dest;
  },
  checkin: () => {
    if (!S.m) return;
    const d = S.m.saude.proximo_dia;
    if (d && (S.m.saude.rotina[d] || []).length) { checkinSheet(d); return; }
    A._checkinRapido();
  },
  _checkinRapido: async () => {
    buzz();
    try {
      await api("/api/treinos", { tipo: S.m.saude.ultimo_tipo || "Musculação" });
      await reloadM();
      const s = S.m.saude, w = s.semana;
      toast(w.faltam ? `Check-in feito · ${w.feitos} de ${s.meta_semana} na semana` : "Check-in feito · meta da semana batida!");
    } catch (e) { toast(e.message); }
  },
  checkindia: el => checkinSheet(el.dataset.dia),
  sugestaook: async () => {
    try { await api("/api/sugestao", { ok: true }); await reloadM(); toast("Combinado"); } catch (e) { toast(e.message); }
  },
  addex: el => exercicioSheet(el.dataset.dia, null),
  exdet: async el => {
    const dia = DIAS_ROTINA.find(d => (S.m.saude.rotina[d] || []).some(x => String(x.id) === el.dataset.id));
    const ex = (S.m.saude.rotina[dia] || []).find(x => String(x.id) === el.dataset.id);
    if (!ex) return;
    let hist = [];
    try { hist = (await api("/api/progresso", { exercicio: ex.exercicio })).historico; } catch (e) { toast(e.message); }
    const pts = hist.filter(h => h.carga != null).map(h => [h.date, h.carga]);
    openDetail({ eyebrow: `Dia ${dia}`, title: ex.exercicio, sub: ex.series ? `${ex.series} séries x ${ex.reps || "?"}` : "",
      body: pts.length ? `${lineChart(pts, "kg")}<div class="card">${pts.slice().reverse().map(p => `<div class="kv"><span class="muted">${dmy(p[0])}</span><span class="num">${fmtN(p[1])} kg</span></div>`).join("")}</div>`
        : emptyBox("dumbbell", "Ainda sem carga registrada neste exercício."),
      actions: `<button type="button" class="btn secondary" data-act="exeditar" data-id="${ex.id}">Editar</button><button type="button" class="btn danger" data-act="exexcluir" data-id="${ex.id}">Excluir</button>` });
  },
  exeditar: el => {
    const dia = DIAS_ROTINA.find(d => (S.m.saude.rotina[d] || []).some(x => String(x.id) === el.dataset.id));
    const ex = (S.m.saude.rotina[dia] || []).find(x => String(x.id) === el.dataset.id);
    if (ex) exercicioSheet(dia, ex);
  },
  exexcluir: async el => {
    if (!(await confirmSheet("Excluir este exercício da rotina?", "Excluir"))) return;
    try { await api("/api/rotina", { id: el.dataset.id, delete: true }); await reloadM(); toast("Excluído"); } catch (e) { toast(e.message); }
  },
  treinodet: () => { const x = S.m.saude.treinos.find(t => t.date === S.m.hoje); if (x) treinoSheet(x); },
  addtreino: () => treinoSheet(null),
  edittreino: el => { const x = findBy(S.m.saude.treinos, el.dataset.id); if (x) treinoSheet(x); },
  addmedida: () => medidaSheet("medidas", null),
  addbio: () => medidaSheet("bio", null),
  avaliacao: el => {
    const c = S.m.saude.corpo, a = findBy(c.avaliacoes, el.dataset.id); if (!a) return;
    const rows = Object.entries(a.valores).map(([k, v]) => `<div class="kv"><span class="muted">${esc((c.campos[k] || {}).nome || k)}</span><span class="num">${withU(v, (c.campos[k] || {}).unidade)}</span></div>`).join("");
    openDetail({ eyebrow: dmy(a.date), title: a.tipo === "bio" ? "Bioimpedância" : "Medidas",
      body: `${a.arquivo_id ? `<div class="filebox">${fileLink(a.arquivo_id, a.arquivo_mime, a.arquivo_nome)}</div>` : ""}${rows ? `<div class="card">${rows}</div>` : ""}${a.nota ? `<p class="lead">${esc(a.nota)}</p>` : ""}`,
      actions: `<button type="button" class="btn" data-act="editaval" data-id="${a.id}">Editar</button>` });
  },
  editaval: el => { const a = findBy(S.m.saude.corpo.avaliacoes, el.dataset.id); if (a) medidaSheet(a.tipo, a); },
  campo: el => {
    const c = S.m.saude.corpo, k = el.dataset.k, f = c.campos[k], pts = c.historico[k] || [], u = c.ultimo[k];
    if (!u) return;
    openDetail({ eyebrow: f.nome, title: `${withU(u.valor, f.unidade)} ${deltaChip(c, k)}`, sub: `Última medição em ${dmy(u.date)}${u.anterior != null ? ` · antes ${withU(u.anterior, f.unidade)}` : ""}`,
      body: `${lineChart(pts, f.unidade || "nível")}<div class="card">${pts.slice().reverse().map(p => `<div class="kv"><span class="muted">${dmy(p[0])}</span><span class="num">${withU(p[1], f.unidade)}</span></div>`).join("")}</div>`,
      actions: `<button type="button" class="btn" data-act="${f.grupo === "bio" ? "addbio" : "addmedida"}">Nova medição</button>` });
  },
  habdelta: el => habSet(Number(el.dataset.id), el.dataset.date, { delta: Number(el.dataset.d) }),
  habset: el => {
    const h = findBy(S.m.saude.habitos, el.dataset.id); if (!h) return;
    const date = el.dataset.date;
    openSheet({ title: h.nome, desc: date === S.m.hoje ? "Quanto foi hoje?" : `Quanto foi em ${dmy(date)}?`, reload: reloadM,
      fields: [{ name: "valor", label: h.unidade ? `Valor (${h.unidade})` : "Valor", type: "decimal", required: true, big: true }],
      values: { valor: Number((h.dias || {})[date] || 0) || "" },
      onSave: v => { if (v.valor < 0) throw new Error("Informe um valor positivo"); return api("/api/habito_dia", { habito_id: h.id, date, valor: v.valor }); } });
  },
  habnav: el => {
    const t = S.m.hoje, cur = parse(S.habDia || t); cur.setDate(cur.getDate() + Number(el.dataset.d));
    const iso = `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, "0")}-${String(cur.getDate()).padStart(2, "0")}`;
    if (iso > t || daysBetween(iso, t) > 30) return;
    S.habDia = iso === t ? null : iso; render();
  },
  addhab: () => habSheet(null),
  edithab: el => { const h = findBy(S.m.saude.habitos, el.dataset.id); if (h) habSheet(h); },
  novo: () => openDetail({ eyebrow: "Agenda", title: "Criar", body: `<div class="choose">
      <button type="button" class="item" data-act="addnota"><span class="ic blue">${icon("clock")}</span><span class="body"><span class="t">Lembrete ou nota</span><span class="sub">Com data, avisa antes</span></span></button>
      <button type="button" class="item" data-act="addtarefa"><span class="ic blue">${icon("list")}</span><span class="body"><span class="t">Tarefa</span><span class="sub">Com ou sem prazo</span></span></button>
      <button type="button" class="item" data-act="addfacul"><span class="ic blue">${icon("cap")}</span><span class="body"><span class="t">Prova ou trabalho</span><span class="sub">Faculdade, com contagem regressiva</span></span></button></div>` }),
  addnota: () => notaSheet(null),
  editnota: el => { const n = findBy(S.m.agenda.notas, el.dataset.id); if (n) notaSheet(n); },
  notapasta: el => { S.notaPasta = el.dataset.v || null; render(); },
  addtarefa: () => tarefaSheet(null),
  edittarefa: el => { const x = findBy(S.m.agenda.tarefas, el.dataset.id); if (x) tarefaSheet(x); },
  tarpasta: el => { S.tarPasta = el.dataset.v || null; render(); },
  qaprazo: el => { S.qaPrazo = el.dataset.v || ""; const f = el.closest("form"); if (f) f.dataset.prazo = S.qaPrazo; el.parentElement.querySelectorAll("button").forEach(b => b.classList.toggle("on", b === el)); },
  addfacul: () => faculSheet(null),
  editfacul: el => { const f = findBy(S.m.agenda.facul, el.dataset.id); if (f) faculSheet(f); },
  faccurso: el => { S.facCurso = el.dataset.v || null; render(); },
  openitem: el => {
    const k = el.dataset.kind;
    if (k === "nota") A.editnota(el); else if (k === "facul") A.editfacul(el); else if (k === "tarefa") A.edittarefa(el);
    else if (k === "conta") location.hash = "#contas";
  },
});

// Entradas que não são cliques: marcar hábito/tarefa, busca nas notas, adicionar tarefa rápida.
document.addEventListener("change", async e => {
  const el = e.target;
  if (el.dataset.chg === "hab") { await habSet(Number(el.dataset.id), el.dataset.date, { valor: el.checked ? 1 : 0 }); }
  if (el.dataset.chg === "tarefa") {
    buzz();
    const x = S.m && findBy(S.m.agenda.tarefas, el.dataset.id);
    if (x) { x.feito_em = el.checked ? S.m.hoje : null; }
    try { await api("/api/tarefas", { id: Number(el.dataset.id), feito: el.checked }); if (el.checked) toast("Tarefa concluída"); } catch (err) { toast(err.message); }
    reloadM();
  }
});
document.addEventListener("input", e => {
  if (e.target.dataset.input === "notaq") { S.notaQ = e.target.value; const box = $("#notalist"); if (box) box.innerHTML = notasList(); }
});
document.addEventListener("submit", async e => {
  const f = e.target;
  if (f.dataset.form !== "tarefa") return;
  e.preventDefault();
  const inp = f.querySelector("input[name=titulo]"), titulo = inp.value.trim();
  if (!titulo) { inp.focus(); return; }
  const ag = S.m.agenda, t = S.m.hoje;
  const d = parse(t); d.setDate(d.getDate() + 1);
  const amanha = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const prazo = f.dataset.prazo === "hoje" ? t : f.dataset.prazo === "amanha" ? amanha : null;
  const pasta = S.view === "tarefas" && S.tarPasta ? S.tarPasta : ag.pastas.find(p => /pessoal/i.test(p)) || ag.pastas[0];
  inp.value = ""; buzz();
  try { await api("/api/tarefas", { titulo, pasta, prazo }); toast(`Tarefa adicionada${prazo ? (prazo === t ? " para hoje" : " para amanhã") : ""}`); await reloadM(); }
  catch (err) { inp.value = titulo; toast(err.message); }
  const again = document.querySelector(`form[data-form="tarefa"] input[name=titulo]`); if (again && matchMedia("(pointer:fine)").matches) again.focus();
});

// Atalhos por endereço: #treino/checkin (notificação e atalho do ícone), #notas/novo, #tarefas/novo, #facul/novo
function routeSub(view, sub) {
  if (!S.m) return false;
  const go = { "treino/checkin": () => A.checkin(), "notas/novo": () => A.addnota(), "tarefas/novo": () => A.addtarefa(), "facul/novo": () => A.addfacul(), "corpo/novo": () => A.addmedida() }[`${view}/${sub}`];
  if (!go) return false;
  setTimeout(go, 50);
  return true;
}
