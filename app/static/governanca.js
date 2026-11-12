// Painel de governança: lê /api/governance e renderiza com textContent (sem innerHTML).
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
const ROTULOS = {
  OPERACAO: "Operação", CONSULTA: "Consulta", RECUSA_ESCOPO: "Recusa", ESCLARECIMENTO: "Esclarecimento",
  VALIDACAO_BLOQUEADA: "Validação bloqueou", ENTRADA_BLOQUEADA: "Entrada bloqueada",
  GUARDRAIL_SAIDA: "Guardrail de saída", ERRO_TECNICO: "Erro técnico",
};

function barra(rotulo, valor, max, cls) {
  const li = el("li");
  const track = el("div", "track");
  const fill = el("div", `fill ${cls || ""}`);
  fill.style.width = max ? `${(100 * valor) / max}%` : "0%";
  track.appendChild(fill);
  li.append(el("span", "", rotulo), track, el("span", "n", String(valor)));
  return li;
}

function render(g) {
  const s = g.seguranca, o = g.operacoes, d = g.desempenho;
  $("k-total").textContent = g.total_mensagens;
  $("k-sessoes").textContent = `${g.sessoes} sessão(ões) · modelo ${g.modelo}`;
  $("k-ops").textContent = o.depositos + o.transferencias;
  $("k-ops-s").textContent = `${o.depositos} depósito(s) · ${o.transferencias} transferência(s)`;
  $("k-vol-dep").textContent = o.volume_depositos;
  $("k-vol-trf").textContent = `depositados · ${o.volume_transferencias} transferidos`;
  $("k-recusas").textContent = s.recusas_fora_escopo;
  const rec = g.por_categoria.find((c) => c.categoria === "RECUSA_ESCOPO");
  $("k-recusas-s").textContent = `${rec ? rec.pct : 0}% das mensagens`;
  $("k-inj").textContent = s.tentativas_injecao;
  $("k-inj-s").textContent = `${s.injecoes_que_alteraram_saldo} alteraram o saldo indevidamente`;
  $("k-guard").textContent = s.guardrail_saida_acionado;
  $("k-lat").textContent = `${d.latencia_media_ms} ms`;
  $("k-lat-s").textContent = `p95: ${d.latencia_p95_ms} ms`;
  $("k-err").textContent = `${d.taxa_erro_tecnico_pct}%`;

  const max = Math.max(1, ...g.por_categoria.map((c) => c.total));
  $("categorias").replaceChildren(...g.por_categoria.map((c) => barra(c.rotulo, c.total, max, `c-${c.categoria}`)));

  const val = Object.entries(s.validacoes_bloqueadas);
  const maxV = Math.max(1, ...val.map(([, n]) => n));
  $("validacoes").replaceChildren(
    ...(val.length ? val.map(([codigo, n]) => barra(codigo, n, maxV, "c-VALIDACAO_BLOQUEADA")) : [el("li", "muted", "Nenhum bloqueio ainda.")])
  );

  const tbody = $("eventos");
  if (!g.eventos_recentes.length) return;
  tbody.replaceChildren(...g.eventos_recentes.map((e) => {
    const tr = el("tr");
    const cat = el("td");
    cat.appendChild(el("span", `pill ${e.categoria}`, ROTULOS[e.categoria] || e.categoria));
    if (e.suspeita_injecao) cat.appendChild(el("span", "pill inj", "injeção"));
    const ferr = e.ferramentas.map((f) => `${f.nome}${f.ok ? " ✓" : ` ✗ ${f.erro || ""}`}`).join(", ") || "—";
    tr.append(
      el("td", "mono", e.ts.replace("T", " ").slice(5)),
      el("td", "mono", e.sessao.slice(0, 8)),
      el("td", "msg", e.mensagem),
      cat,
      el("td", "mono", ferr),
      el("td", "mono", e.saldo_apos),
      el("td", "mono", `${e.latencia_ms} ms`),
    );
    return tr;
  }));
}

async function atualizar() {
  try {
    const r = await fetch("/api/governance");
    if (r.ok) {
      render(await r.json());
      $("atualizado").textContent = `Atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
    }
  } catch (e) { console.error(e); }
}
atualizar();
setInterval(atualizar, 5000);
