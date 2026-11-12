// Governance dashboard: reads /api/governance and renders with textContent (never innerHTML).
const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

// Short Portuguese labels for the events table (visible UI text).
const CATEGORY_SHORT_LABELS = {
  OPERATION: "Operação", BALANCE_QUERY: "Consulta", OUT_OF_SCOPE_REFUSAL: "Recusa", CLARIFICATION: "Esclarecimento",
  VALIDATION_BLOCKED: "Validação bloqueou", INPUT_BLOCKED: "Entrada bloqueada",
  OUTPUT_GUARDRAIL: "Guardrail de saída", TECHNICAL_ERROR: "Erro técnico",
};

function barRow(label, value, max, className) {
  const row = el("li");
  const track = el("div", "track");
  const fill = el("div", `fill ${className || ""}`);
  fill.style.width = max ? `${(100 * value) / max}%` : "0%";
  track.appendChild(fill);
  row.append(el("span", "", label), track, el("span", "n", String(value)));
  return row;
}

function renderKpis(data) {
  const { security, operations, performance } = data;
  const refusals = data.by_category.find((c) => c.category === "OUT_OF_SCOPE_REFUSAL");
  $("k-total").textContent = data.total_messages;
  $("k-sessions").textContent = `${data.sessions} sessão(ões) · modelo ${data.model}`;
  $("k-ops").textContent = operations.deposits + operations.transfers;
  $("k-ops-s").textContent = `${operations.deposits} depósito(s) · ${operations.transfers} transferência(s)`;
  $("k-deposit-volume").textContent = operations.deposit_volume;
  $("k-transfer-volume").textContent = `depositados · ${operations.transfer_volume} transferidos`;
  $("k-refusals").textContent = security.out_of_scope_refusals;
  $("k-refusals-sub").textContent = `${refusals ? refusals.pct : 0}% das mensagens`;
  $("k-injections").textContent = security.injection_attempts;
  $("k-injections-sub").textContent = `${security.injections_that_changed_balance} alteraram o saldo indevidamente`;
  $("k-guardrail").textContent = security.output_guardrail_triggered;
  $("k-latency").textContent = `${performance.avg_latency_ms} ms`;
  $("k-latency-sub").textContent = `p95: ${performance.p95_latency_ms} ms`;
  $("k-errors").textContent = `${performance.technical_error_rate_pct}%`;
}

function renderBars(data) {
  const max = Math.max(1, ...data.by_category.map((c) => c.total));
  $("categories").replaceChildren(...data.by_category.map((c) => barRow(c.label, c.total, max, `c-${c.category}`)));

  const blocked = Object.entries(data.security.blocked_validations);
  const maxBlocked = Math.max(1, ...blocked.map(([, count]) => count));
  const rows = blocked.length
    ? blocked.map(([code, count]) => barRow(code, count, maxBlocked, "c-VALIDATION_BLOCKED"))
    : [el("li", "muted", "Nenhum bloqueio ainda.")];
  $("validations").replaceChildren(...rows);
}

function eventRow(event) {
  const row = el("tr");
  const category = el("td");
  category.appendChild(el("span", `pill ${event.category}`, CATEGORY_SHORT_LABELS[event.category] || event.category));
  if (event.injection_suspected) category.appendChild(el("span", "pill inj", "injeção"));
  const tools = event.tools.map((t) => `${t.name}${t.ok ? " ✓" : ` ✗ ${t.error || ""}`}`).join(", ") || "—";
  row.append(
    el("td", "mono", event.ts.replace("T", " ").slice(5)),
    el("td", "mono", event.session.slice(0, 8)),
    el("td", "msg", event.message),
    category,
    el("td", "mono", tools),
    el("td", "mono", event.balance_after),
    el("td", "mono", `${event.latency_ms} ms`),
  );
  return row;
}

function render(data) {
  renderKpis(data);
  renderBars(data);
  if (data.recent_events.length) $("events").replaceChildren(...data.recent_events.map(eventRow));
}

async function refresh() {
  try {
    const response = await fetch("/api/governance");
    if (!response.ok) return;
    render(await response.json());
    $("updated-at").textContent = `Atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
  } catch (error) {
    console.error(error);
  }
}

refresh();
setInterval(refresh, 5000);
