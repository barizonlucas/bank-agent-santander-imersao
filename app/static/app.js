// Frontend do Agente Bancário Restrito (JS puro). Todo texto é inserido via
// textContent para evitar injeção de HTML vindo do modelo ou do usuário.
const $ = (id) => document.getElementById(id);

function getSessionId() {
  let id = null;
  try { id = localStorage.getItem("bank_session_id"); } catch {}
  if (!id) {
    id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/[^A-Za-z0-9-]/g, "");
    try { localStorage.setItem("bank_session_id", id); } catch {}
  }
  return id;
}
let sessionId = getSessionId();

function addMessage(role, text, tools = []) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  renderTexto(el, text);
  if (tools.length) {
    const box = document.createElement("div");
    box.className = "tools";
    for (const t of tools) {
      const tag = document.createElement("span");
      tag.className = `tool ${t.resultado.ok ? "ok" : "err"}`;
      const args = Object.values(t.args || {}).join(", ");
      tag.textContent = `${t.nome}(${args}) ${t.resultado.ok ? "✓" : "✗ " + (t.resultado.erro || "")}`;
      box.appendChild(tag);
    }
    el.appendChild(box);
  }
  $("messages").appendChild(el);
  $("messages").scrollTop = $("messages").scrollHeight;
  return el;
}

// Markdown mínimo e SEGURO: só **negrito**, montado com nós de texto (sem innerHTML).
function renderTexto(el, text) {
  text.split(/(\*\*[^*]+\*\*)/g).forEach((pedaco) => {
    if (pedaco.startsWith("**") && pedaco.endsWith("**") && pedaco.length > 4) {
      const b = document.createElement("strong");
      b.textContent = pedaco.slice(2, -2);
      el.appendChild(b);
    } else {
      el.appendChild(document.createTextNode(pedaco));
    }
  });
}

function renderState(s) {
  $("saldo").textContent = s.saldo;
  $("conta").textContent = s.conta;
  const ul = $("extrato");
  ul.replaceChildren();
  if (!s.extrato.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "Nenhuma movimentação.";
    ul.appendChild(li);
  }
  for (const t of s.extrato) {
    const li = document.createElement("li");
    const desc = document.createElement("span");
    desc.textContent = t.tipo === "DEPOSITO" ? "Depósito" : `Transferência → ${t.destinatario}`;
    const val = document.createElement("span");
    val.className = t.tipo === "DEPOSITO" ? "in" : "out";
    val.textContent = (t.tipo === "DEPOSITO" ? "+ " : "− ") + t.valor;
    li.title = `${t.data_hora} · saldo após: ${t.saldo_apos}`;
    li.append(desc, val);
    ul.appendChild(li);
  }
  const p = s.pendente;
  $("pending").classList.toggle("hidden", !p);
  $("pending-info").textContent = p ? `${p.valor} para ${p.destinatario} (${p.operacao_id})` : "";
}

let busy = false;
async function send(text) {
  text = text.trim();
  if (!text || busy) return;
  busy = true;
  $("send").disabled = true;
  addMessage("user", text);
  const typing = addMessage("bot typing", "Processando…");
  const t0 = Date.now();
  const relogio = setInterval(() => {
    const seg = Math.round((Date.now() - t0) / 1000);
    if (seg >= 2) typing.textContent = `Processando… ${seg} s`;
  }, 1000);
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    const data = await r.json();
    typing.remove();
    if (!r.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : r.statusText);
    const el = addMessage("bot", data.reply, data.tools || []);
    if (data.guardrail_saida && data.guardrail_saida.length) {
      const aviso = document.createElement("div");
      aviso.className = "guard-note";
      aviso.textContent = "🛡️ Resposta verificada e corrigida pelo guardrail de saída";
      el.appendChild(aviso);
    }
    renderState(data);
  } catch (e) {
    typing.remove();
    addMessage("bot", "Erro de comunicação com o servidor. Tente novamente.");
    console.error(e);
  } finally {
    clearInterval(relogio);
    busy = false;
    $("send").disabled = false;
    $("input").focus();
  }
}

$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("input").value;
  $("input").value = "";
  send(v);
});
document.querySelectorAll(".chips button").forEach((b) => b.addEventListener("click", () => send(b.textContent)));
$("confirm").addEventListener("click", () => send("Confirmo a transferência."));
$("cancel").addEventListener("click", () => send("Cancelar a transferência."));
$("reset").addEventListener("click", async () => {
  const r = await fetch("/api/reset", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  $("messages").replaceChildren();
  renderState(await r.json());
  greet();
});

// Restaura a conversa salva no backend (persistida até "Reiniciar sessão").
function renderTranscricao(mensagens) {
  $("messages").replaceChildren();
  greet();
  for (const m of mensagens || []) {
    if (m.papel === "user") addMessage("user", m.texto);
    else addMessage("bot", m.texto, m.ferramentas || []);
  }
}

// ---- Painel "Sobre o projeto" (dados vindos de /api/about) ----
async function carregarSobre() {
  try {
    const r = await fetch("/api/about");
    if (!r.ok) return;
    const a = await r.json();
    $("ab-nome").textContent = a.projeto.nome;
    $("ab-desc").textContent = a.projeto.descricao;
    const dl = $("ab-stack");
    dl.replaceChildren();
    for (const item of a.stack) {
      const dt = document.createElement("dt");
      dt.textContent = item.camada;
      const dd = document.createElement("dd");
      dd.textContent = item.tecnologia;
      dl.append(dt, dd);
    }
    $("ab-provedor").textContent = a.llm.provedor;
    $("ab-modelo").textContent = a.llm.modelo;
    $("ab-uso").textContent = a.llm.uso;
    $("ab-temp").textContent = `${a.llm.temperatura} (respostas determinísticas) · máx. ${a.llm.max_ferramentas_por_mensagem} ferramentas por mensagem`;
    $("ab-tools").textContent = a.llm.ferramentas.join(", ");
    for (const id of ["ab-autor", "foot-autor"]) { $(id).href = a.autor.linkedin; }
    $("ab-autor").textContent = `${a.autor.nome} — LinkedIn`;
    $("foot-autor").textContent = a.autor.nome;
    for (const id of ["ab-curso", "foot-curso"]) { $(id).href = a.curso.url; }
    $("ab-curso").textContent = a.curso.nome;
  } catch (e) { console.error(e); }
}
$("about-open").addEventListener("click", () => $("about").showModal());
$("about-close").addEventListener("click", () => $("about").close());
$("about").addEventListener("click", (e) => { if (e.target === $("about")) $("about").close(); });
carregarSobre();

function greet() {
  addMessage("bot", "Olá! Sou o assistente do Banco Imersão. Posso consultar seu saldo, fazer um depósito ou uma transferência. Como posso ajudar?");
}

(async () => {
  try {
    const r = await fetch(`/api/state?session_id=${encodeURIComponent(sessionId)}`);
    if (!r.ok) throw new Error(r.statusText);
    const estado = await r.json();
    renderTranscricao(estado.mensagens);
    renderState(estado);
  } catch (e) {
    greet();
    console.error(e);
  }
})();
