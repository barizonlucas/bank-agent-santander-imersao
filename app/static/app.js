// Frontend of the restricted banking agent (plain JS). Every piece of text goes in via
// textContent so HTML coming from the model or the user can never be injected.
const $ = (id) => document.getElementById(id);
const JSON_HEADERS = { "Content-Type": "application/json" };

// ---- session ---------------------------------------------------------------------
function loadSessionId() {
  let id = null;
  try { id = localStorage.getItem("bank_session_id"); } catch { /* storage blocked: fall through */ }
  if (id) return id;
  id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/[^A-Za-z0-9-]/g, "");
  try { localStorage.setItem("bank_session_id", id); } catch { /* storage blocked: session lives in memory */ }
  return id;
}
const sessionId = loadSessionId();

// ---- messages --------------------------------------------------------------------
// Minimal and SAFE markdown: only **bold**, built with text nodes (no innerHTML).
function renderText(container, text) {
  text.split(/(\*\*[^*]+\*\*)/g).forEach((piece) => {
    const isBold = piece.startsWith("**") && piece.endsWith("**") && piece.length > 4;
    if (!isBold) return container.appendChild(document.createTextNode(piece));
    const strong = document.createElement("strong");
    strong.textContent = piece.slice(2, -2);
    container.appendChild(strong);
  });
}

function toolTag(tool) {
  const tag = document.createElement("span");
  tag.className = `tool ${tool.result.ok ? "ok" : "err"}`;
  const args = Object.values(tool.args || {}).join(", ");
  tag.textContent = `${tool.name}(${args}) ${tool.result.ok ? "✓" : "✗ " + (tool.result.error || "")}`;
  return tag;
}

function addMessage(role, text, tools = []) {
  const message = document.createElement("div");
  message.className = `msg ${role}`;
  renderText(message, text);
  if (tools.length) {
    const box = document.createElement("div");
    box.className = "tools";
    box.append(...tools.map(toolTag));
    message.appendChild(box);
  }
  $("messages").appendChild(message);
  $("messages").scrollTop = $("messages").scrollHeight;
  return message;
}

function greet() {
  addMessage("bot", "Olá! Sou o assistente do Banco Imersão. Posso consultar seu saldo, fazer um depósito ou uma transferência. Como posso ajudar?");
}

// Restores the conversation saved by the backend (kept until "Reiniciar sessão").
function renderTranscript(entries) {
  $("messages").replaceChildren();
  greet();
  for (const entry of entries || []) {
    if (entry.role === "user") addMessage("user", entry.text);
    else addMessage("bot", entry.text, entry.tools || []);
  }
}

// ---- account state ---------------------------------------------------------------
function statementRow(transaction) {
  const isDeposit = transaction.type === "DEPOSIT";
  const row = document.createElement("li");
  const description = document.createElement("span");
  description.textContent = isDeposit ? "Depósito" : `Transferência → ${transaction.recipient}`;
  const amount = document.createElement("span");
  amount.className = isDeposit ? "in" : "out";
  amount.textContent = (isDeposit ? "+ " : "− ") + transaction.amount;
  row.title = `${transaction.timestamp} · saldo após: ${transaction.balance_after}`;
  row.append(description, amount);
  return row;
}

function renderStatement(statement) {
  const list = $("statement");
  list.replaceChildren();
  if (!statement.length) {
    const empty = document.createElement("li");
    empty.className = "muted";
    empty.textContent = "Nenhuma movimentação.";
    list.appendChild(empty);
  }
  list.append(...statement.map(statementRow));
}

function renderState(state) {
  $("balance").textContent = state.balance;
  $("account").textContent = state.account;
  renderStatement(state.statement);
  const pending = state.pending;
  $("pending").classList.toggle("hidden", !pending);
  $("pending-info").textContent = pending ? `${pending.amount} para ${pending.recipient} (${pending.operation_id})` : "";
}

// ---- sending ---------------------------------------------------------------------
function startElapsedTimer(indicator) {
  const startedAt = Date.now();
  return setInterval(() => {
    const seconds = Math.round((Date.now() - startedAt) / 1000);
    if (seconds >= 2) indicator.textContent = `Processando… ${seconds} s`;
  }, 1000);
}

function showGuardrailNote(message) {
  const note = document.createElement("div");
  note.className = "guard-note";
  note.textContent = "🛡️ Resposta verificada e corrigida pelo guardrail de saída";
  message.appendChild(note);
}

async function postChat(text) {
  const response = await fetch("/api/chat", {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ session_id: sessionId, message: text }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : response.statusText);
  return data;
}

let busy = false;
async function send(rawText) {
  const text = rawText.trim();
  if (!text || busy) return;
  busy = true;
  $("send").disabled = true;
  addMessage("user", text);
  const typing = addMessage("bot typing", "Processando…");
  const timer = startElapsedTimer(typing);
  try {
    const data = await postChat(text);
    typing.remove();
    const message = addMessage("bot", data.reply, data.tools || []);
    if (data.output_guardrail && data.output_guardrail.length) showGuardrailNote(message);
    renderState(data);
  } catch (error) {
    typing.remove();
    addMessage("bot", "Erro de comunicação com o servidor. Tente novamente.");
    console.error(error);
  } finally {
    clearInterval(timer);
    busy = false;
    $("send").disabled = false;
    $("input").focus();
  }
}

async function resetSession() {
  const response = await fetch("/api/reset", {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ session_id: sessionId }),
  });
  $("messages").replaceChildren();
  renderState(await response.json());
  greet();
}

// ---- "About the project" dialog (data from /api/about) ---------------------------
function renderStack(stack) {
  const list = $("about-stack");
  list.replaceChildren();
  for (const item of stack) {
    const term = document.createElement("dt");
    term.textContent = item.layer;
    const detail = document.createElement("dd");
    detail.textContent = item.technology;
    list.append(term, detail);
  }
}

function renderAbout(about) {
  $("about-name").textContent = about.project.name;
  $("about-description").textContent = about.project.description;
  renderStack(about.stack);
  $("about-provider").textContent = about.llm.provider;
  $("about-model").textContent = about.llm.model;
  $("about-usage").textContent = about.llm.usage;
  $("about-temperature").textContent =
    `${about.llm.temperature} (respostas determinísticas) · máx. ${about.llm.max_tools_per_message} ferramentas por mensagem`;
  $("about-tools").textContent = about.llm.tools.join(", ");
  for (const id of ["about-author", "footer-author"]) $(id).href = about.author.linkedin;
  $("about-author").textContent = `${about.author.name} — LinkedIn`;
  $("footer-author").textContent = about.author.name;
  for (const id of ["about-course", "footer-course"]) $(id).href = about.course.url;
  $("about-course").textContent = about.course.name;
}

async function loadAbout() {
  try {
    const response = await fetch("/api/about");
    if (response.ok) renderAbout(await response.json());
  } catch (error) {
    console.error(error);
  }
}

// ---- wiring ----------------------------------------------------------------------
$("form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("input").value;
  $("input").value = "";
  send(text);
});
document.querySelectorAll(".chips button").forEach((chip) => chip.addEventListener("click", () => send(chip.textContent)));
$("confirm").addEventListener("click", () => send("Confirmo a transferência."));
$("cancel").addEventListener("click", () => send("Cancelar a transferência."));
$("reset").addEventListener("click", resetSession);
$("about-open").addEventListener("click", () => $("about").showModal());
$("about-close").addEventListener("click", () => $("about").close());
$("about").addEventListener("click", (event) => { if (event.target === $("about")) $("about").close(); });

async function restoreSavedState() {
  try {
    const response = await fetch(`/api/state?session_id=${encodeURIComponent(sessionId)}`);
    if (!response.ok) throw new Error(response.statusText);
    const state = await response.json();
    renderTranscript(state.messages);
    renderState(state);
  } catch (error) {
    greet();
    console.error(error);
  }
}

loadAbout();
restoreSavedState();
