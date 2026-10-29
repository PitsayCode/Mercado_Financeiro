/* Radar de Notícias – mostra as notícias e o efeito provável no dólar (WDO) e no índice (WIN).
   As notícias e a leitura de efeito vêm do servidor (app.py) em /api/noticias. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const salvar = (k, v) => { try { localStorage.setItem("radar_" + k, JSON.stringify(v)); } catch {} };
const ler = (k, padrao) => { try { const v = localStorage.getItem("radar_" + k); return v == null ? padrao : JSON.parse(v); } catch { return padrao; } };

let NOTICIAS = [];
const filtro = { imp: ler("filtro_imp", "medio"), ef: "todos", busca: "" };
const ORDEM_IMP = { alto: 3, medio: 2, baixo: 1 };
const NOME_IMP = { alto: "Alto impacto", medio: "Médio impacto", baixo: "Baixo impacto" };
const SETA = { sobe: "⬆", cai: "⬇", neutro: "↔" };

/* ───────── Tema e letra ───────── */
$("#btn-tema").addEventListener("click", () => {
  const novo = document.documentElement.dataset.tema === "papel" ? "grafite" : "papel";
  document.documentElement.dataset.tema = novo; salvar("tema", novo);
});
$("#btn-fonte").addEventListener("click", () => {
  const ordem = ["", "g", "gg"];
  const novo = ordem[(ordem.indexOf(document.documentElement.dataset.fonte || "") + 1) % ordem.length];
  if (novo) document.documentElement.dataset.fonte = novo; else delete document.documentElement.dataset.fonte;
  salvar("fonte", novo || null);
});

/* ───────── Utilidades ───────── */
function tempoAtras(iso) {
  if (!iso) return "";
  const min = Math.round((Date.now() - new Date(iso)) / 60000);
  if (min < 1) return "agora";
  if (min < 60) return `há ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `há ${h} h`;
  return new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/* ───────── Efeito de cada notícia ───────── */
function chip(nome, dir, jaAconteceu) {
  const texto = dir === "neutro" ? "sem direção clara" : jaAconteceu ? (dir === "sobe" ? "subindo" : "caindo") : (dir === "sobe" ? "tende a subir" : "tende a cair");
  return `<span class="ef ${dir}">${nome} <b>${SETA[dir]} ${texto}</b></span>`;
}
function blocoEfeito(n) {
  const d = n.direcao;
  if (d.wdo === "neutro" && d.win === "neutro") {
    return `<div class="efeito neutro"><span class="ef-rotulo">Efeito no mercado</span>
      <span>↔ A manchete não indica direção clara. O efeito depende dos detalhes: veja se o número veio acima ou abaixo do esperado.</span></div>`;
  }
  return `<div class="efeito">
    <span class="ef-rotulo">${d.ja_aconteceu ? "📍 O mercado já reagiu" : "🔮 Reação mais comum"}</span>
    ${chip("💵 Dólar (WDO)", d.wdo, d.ja_aconteceu)}
    ${chip("📈 Índice (WIN)", d.win, d.ja_aconteceu)}
    ${d.motivos.length ? `<p class="ef-porque"><b>Por quê:</b> ${esc(d.motivos.join("; "))}.</p>` : ""}
  </div>`;
}
function cartao(n) {
  const novo = n.data && Date.now() - new Date(n.data) < 3600e3;
  return `
  <article class="noticia ${n.impacto}">
    <div class="linha1">
      <span class="selo ${n.impacto}">${NOME_IMP[n.impacto]}</span>
      ${novo ? `<span class="selo novo">novo</span>` : ""}
      <span>${esc(n.fonte)} · ${tempoAtras(n.data)}</span>
    </div>
    <h3><a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.titulo)}</a></h3>
    ${n.resumo ? `<p class="resumo">${esc(n.resumo.slice(0, 260))}${n.resumo.length > 260 ? "…" : ""}</p>` : ""}
    ${blocoEfeito(n)}
    <details class="porque">
      <summary>Como esse tipo de notícia afeta o mercado (${n.categorias.map((c) => esc(c.nome)).join(", ")})</summary>
      <ul>${n.categorias.map((c) => `<li><b>${esc(c.nome)}:</b> ${esc(c.efeito)}</li>`).join("")}</ul>
    </details>
  </article>`;
}

/* ───────── Resumo do momento ───────── */
function pressao() {
  const agora = Date.now(), total = { wdo: 0, win: 0 }, razoes = [];
  const recentes = NOTICIAS.filter((n) => n.impacto !== "baixo" && n.data && agora - new Date(n.data) < 18 * 3600e3);
  recentes.forEach((n) => {
    const peso = (n.impacto === "alto" ? 2 : 1) * Math.pow(0.5, (agora - new Date(n.data)) / (6 * 3600e3));
    const cw = n.direcao.forca_wdo * peso, ci = n.direcao.forca_win * peso;
    total.wdo += cw; total.win += ci;
    if (Math.abs(cw) + Math.abs(ci) >= 1) razoes.push({ n, c: Math.abs(cw) + Math.abs(ci) });
  });
  return { total, razoes: razoes.sort((a, b) => b.c - a.c).slice(0, 3), qtd: recentes.length };
}
function renderResumo() {
  const { total, razoes, qtd } = pressao();
  const dir = (x) => (x > 2 ? "sobe" : x < -2 ? "cai" : "neutro");
  const forca = (x) => (Math.abs(x) >= 10 ? "forte" : Math.abs(x) >= 5 ? "moderada" : "fraca");
  const linha = (k, nome) => {
    const d = dir(total[k]);
    const txt = d === "neutro" ? "sem direção clara" : `pressão de ${d === "sobe" ? "alta" : "baixa"} · ${forca(total[k])}`;
    return `<div class="pressao ${d}"><span>${nome}</span><b>${SETA[d]} ${txt}</b></div>`;
  };
  const mesmoLado = Math.sign(total.wdo) === Math.sign(total.win) && Math.abs(total.wdo) > 2 && Math.abs(total.win) > 2;
  $("#pressao").innerHTML = !qtd ? "<p class='status'>Ainda sem notícias recentes suficientes.</p>" :
    linha("wdo", "💵 Dólar (WDO)") + linha("win", "📈 Índice (WIN)") +
    (razoes.length ? `<p class="rotulo" style="margin-top:12px">O que mais pesou</p>` + razoes.map(({ n }) =>
      `<div class="mini"><a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.titulo)}</a>
        <small>${esc(n.direcao.motivos.join("; "))} · ${tempoAtras(n.data)}</small></div>`).join("") : "") +
    `<p class="status" style="margin:10px 0 0">${mesmoLado ? "⚠️ Dólar e índice apontam para o mesmo lado: sinal misto, cautela. " : ""}` +
    `Soma de ${qtd} notícias das últimas 18 h (as de alto impacto e as mais recentes pesam mais).</p>`;

  const temas = {};
  NOTICIAS.filter((n) => n.impacto !== "baixo" && n.data && Date.now() - new Date(n.data) < 24 * 3600e3)
    .forEach((n) => n.categorias.forEach((c) => { temas[c.nome] = (temas[c.nome] || 0) + ORDEM_IMP[n.impacto]; }));
  const top = Object.entries(temas).sort((a, b) => b[1] - a[1]).slice(0, 6);
  const max = top.length ? top[0][1] : 1;
  $("#temas").innerHTML = top.length ? top.map(([t, p]) =>
    `<button class="tema" data-assunto="${esc(t)}" title="Filtrar por este assunto"><span>${esc(t)}</span>
      <span class="barra"><span style="width:${Math.round(p / max * 100)}%"></span></span></button>`).join("") +
    `<p class="status" style="margin:8px 0 0">Últimas 24 h. Clique num assunto para filtrar.</p>`
    : "<p class='status'>Sem dados ainda.</p>";
  $$("[data-assunto]").forEach((b) => b.addEventListener("click", () => {
    filtro.busca = filtro.busca === "#" + b.dataset.assunto ? "" : "#" + b.dataset.assunto;
    $("#busca").value = filtro.busca ? b.dataset.assunto : "";
    renderLista();
  }));
}

/* ───────── Lista ───────── */
function renderLista() {
  const minimo = ORDEM_IMP[filtro.imp];
  const termo = filtro.busca.toLowerCase();
  const lista = NOTICIAS.filter((n) => {
    if (ORDEM_IMP[n.impacto] < minimo) return false;
    if (filtro.ef !== "todos") { const [a, d] = filtro.ef.split("-"); if (n.direcao[a] !== d) return false; }
    if (!termo) return true;
    if (termo.startsWith("#")) return n.categorias.some((c) => c.nome.toLowerCase() === termo.slice(1));
    return (n.titulo + " " + n.resumo).toLowerCase().includes(termo);
  });
  $("#lista").innerHTML = lista.length ? lista.slice(0, 150).map(cartao).join("")
    : `<div class="vazio">Nenhuma notícia com esses filtros.</div>`;
}

async function carregar(forcar = false) {
  $("#status").textContent = "Buscando notícias…";
  try {
    const d = await (await fetch("/api/noticias" + (forcar ? "?forcar=1" : ""))).json();
    NOTICIAS = d.noticias;
    const hora = new Date(d.atualizado_em).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    $("#status").textContent = `Atualizado às ${hora} · ${d.total} notícias relevantes nas últimas 48 h · ` +
      `${d.fontes_ok}/${d.fontes_total} fontes respondendo · atualiza sozinho a cada 5 min`;
    renderResumo(); renderLista();
  } catch {
    $("#status").textContent = "Não consegui buscar as notícias. A janela preta do app está aberta? Verifique a internet.";
  }
}

/* ───────── Filtros ───────── */
function marcar(attr, valor) { $$(`[${attr}]`).forEach((b) => b.classList.toggle("ativo", b.getAttribute(attr) === valor)); }
$$("[data-imp]").forEach((b) => b.addEventListener("click", () => {
  filtro.imp = b.dataset.imp; salvar("filtro_imp", filtro.imp); marcar("data-imp", filtro.imp); renderLista();
}));
$$("[data-ef]").forEach((b) => b.addEventListener("click", () => { filtro.ef = b.dataset.ef; marcar("data-ef", filtro.ef); renderLista(); }));
$("#busca").addEventListener("input", (e) => { filtro.busca = e.target.value; renderLista(); });
$("#atualizar").addEventListener("click", () => carregar(true));

marcar("data-imp", filtro.imp);
carregar();
setInterval(carregar, 5 * 60 * 1000);
