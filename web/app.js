"use strict";

const state = {
  user: null,
  csrf: "",
  runs: [],
  currentRun: null,
  currentRunTab: "overview",
  importRunId: "",
  importSide: "bank",
  pendingBatchId: "",
  worklistPage: 1,
  worklistPageSize: 25,
  worklistTotal: 0,
  runResultsPage: 1,
  runResultsPageSize: 25,
  preRunAnalysis: {}
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
}

function formatCents(value, currency = null) {
  const cents = Number(value || 0);
  if (!currency) return `${(cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (currency === "INR") {
    return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(cents / 100);
  }
  const sign = cents < 0 ? "−" : "";
  const abs = Math.abs(cents);
  // This application stores all monetary values as integer cents, including JPY.
  const digits = 2;
  const whole = Math.floor(abs / 100);
  const fraction = String(abs % 100).padStart(2, "0");
  return `${sign}${currency} ${whole.toLocaleString("en-US")}${digits ? `.${fraction}` : ""}`;
}

function toast(message, type = "success") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  $("#toast-region").append(node);
  window.setTimeout(() => node.remove(), 4800);
}

async function request(path, { method = "GET", body, rawBody, contentType = "application/json" } = {}) {
  const headers = {};
  if (method !== "GET" && state.csrf) headers["X-CSRF-Token"] = state.csrf;
  if ((body !== undefined || rawBody !== undefined) && contentType) headers["Content-Type"] = contentType;
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers,
    body: rawBody !== undefined ? rawBody : body === undefined ? undefined : JSON.stringify(body)
  });
  if (response.status === 401) {
    state.user = null;
    state.csrf = "";
    showLogin();
  }
  const contentTypeHeader = response.headers.get("Content-Type") || "";
  const data = contentTypeHeader.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const reasons = data?.blocking_reasons?.length ? ` ${data.blocking_reasons.join(" ")}` : "";
    throw new Error(`${data?.error || data?.message || JSON.stringify(data)}${reasons}`);
  }
  return data;
}

function setBusy(button, busy, busyText = "Working…") {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = busyText;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
  }
}

function showLogin() {
  $("#application").hidden = true;
  $("#login-view").hidden = false;
  showAuthMode("login");
  $("#login-username").focus();
}

function showAuthMode(mode) {
  const signup = mode === "signup";
  $("#login-form").hidden = signup;
  $("#signup-form").hidden = !signup;
  $("#signup-invite").hidden = signup;
  $("#login-policy").hidden = signup;
  $("#login-switch").hidden = !signup;
  $("#login-title").textContent = signup ? "Create your account" : "Sign in to your workspace";
  $("#login-description").textContent = signup ? "Create an analyst account for your reconciliation workspace." : "Use your organization account to access reconciliations.";
  if (signup) $("#signup-username").focus();
}

function showApplication() {
  $("#login-view").hidden = true;
  $("#application").hidden = false;
  $("#account-name").textContent = state.user.username;
  $("#account-menu-name").textContent = state.user.username;
  $("#account-role").textContent = state.user.role;
  $("#account-initials").textContent = state.user.username.slice(0, 2).toUpperCase();
  $("#sample-run-button").hidden = state.user.role === "auditor";
}

async function restoreSession() {
  try {
    const data = await request("/api/auth/me");
    state.user = data.user;
    state.csrf = data.csrf_token;
    showApplication();
    await loadDashboard();
  } catch (_) {
    showLogin();
  }
}

function showPage(name) {
  const page = $(`[data-page-view="${name}"]`);
  if (!page) return;
  $$("[data-page-view]").forEach(section => {
    const active = section === page;
    section.classList.toggle("active", active);
    section.hidden = !active;
  });
  $$(".nav-link[data-page]").forEach(link => link.classList.toggle("active", link.dataset.page === name || (name === "run-detail" && link.dataset.page === "runs")));
  const labels = { dashboard: "Overview", runs: "Reconciliations", "new-run": "New reconciliation", "run-detail": "Run detail", import: "Import review", worklist: "Worklist", "match-detail": "Match detail", audit: "Audit trail", tools: "Tool registry" };
  $("#page-context").textContent = labels[name] || "Operations";
  if (name === "import") populateRunSelect("#import-run-select", state.importRunId);
  if (name === "worklist") populateRunSelect("#worklist-run-select", state.currentRun?.id || "");
}

function navTo(name) {
  showPage(name);
  if (name === "dashboard") loadDashboard();
  if (name === "runs") loadRunsPage();
  if (name === "audit") loadAudit();
  if (name === "tools") loadTools();
  if (name === "worklist") loadWorklist();
  if (name === "import") loadImportWorkspace();
}

async function startUploadFlow() {
  try {
    await loadRuns();
    if (state.runs.length) {
      state.importRunId = state.currentRun?.id || state.runs[0].id;
      navTo("import");
      return;
    }
    beginNewRunFlow(true);
  } catch (error) {
    toast(error.message, "error");
  }
}

function beginNewRunFlow(fromUpload = false) {
  $("#run-form").reset();
  showPage("new-run");
}

function emptyState(title, detail, actionLabel = "", action = "") {
  return `<div class="empty-state"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span>${actionLabel ? `<p><button class="button primary" data-action="${escapeHtml(action)}" type="button">${escapeHtml(actionLabel)}</button></p>` : ""}</div>`;
}

function statusBadge(status) {
  const normalized = String(status || "draft").toLowerCase();
  const label = normalized === "reconciled" ? "results ready" : normalized.replaceAll("_", " ");
  return `<span class="status ${escapeHtml(normalized)}">${escapeHtml(label)}</span>`;
}

function runsTable(runs, compact = false) {
  if (!runs.length) return emptyState("No reconciliations yet", "Create a run to import your first bank and ledger statements.", "New reconciliation", "new-run");
  return `<table><thead><tr><th>Run</th><th>Period</th><th>Bank import</th><th>Ledger import</th><th>Status</th><th>Created</th></tr></thead><tbody>${runs.map(run => {
    const bank = (run.batches || []).find(batch => batch.side === "bank");
    const ledger = (run.batches || []).find(batch => batch.side === "ledger");
    const batchLabel = batch => batch ? `${escapeHtml(batch.row_count ?? 0)} rows · ${statusBadge(batch.status)}` : "Not uploaded";
    return `<tr><td><button class="link-button" type="button" data-open-run="${escapeHtml(run.id)}">${escapeHtml(run.name)}</button><div class="muted mono">${escapeHtml(run.id)}</div></td><td class="nowrap">${escapeHtml(run.period_start || "—")} – ${escapeHtml(run.period_end || "—")}</td><td>${batchLabel(bank)}</td><td>${batchLabel(ledger)}</td><td>${statusBadge(run.status)}</td><td class="nowrap">${escapeHtml((run.created_at || "").slice(0, 16) || "—")}</td></tr>`;
  }).join("")}</tbody></table>`;
}

async function loadRuns() {
  const result = await request("/api/runs");
  state.runs = result.runs || [];
  return state.runs;
}

async function loadDashboard() {
  const metrics = $("#dashboard-metrics");
  metrics.innerHTML = '<div class="loading">Loading workspace data…</div>';
  try {
    const runs = await loadRuns();
    const statusCounts = runs.reduce((counts, run) => {
      counts[run.status] = (counts[run.status] || 0) + 1;
      return counts;
    }, {});
    const importedRows = runs.reduce((sum, run) => sum + (run.batches || []).reduce((batchSum, batch) => batchSum + (batch.status === "discarded" ? 0 : Number(batch.row_count || 0)), 0), 0);
    metrics.innerHTML = `<div class="metric"><span>Runs in workspace</span><strong>${runs.length}</strong></div><div class="metric"><span>Imported transaction rows</span><strong>${importedRows.toLocaleString()}</strong></div><div class="metric"><span>Reconciled</span><strong>${statusCounts.reconciled || 0}</strong></div>`;
    $("#dashboard-runs").innerHTML = runsTable(runs.slice(0, 8), true);
    await loadActivity("#dashboard-activity", 6);
    if (state.currentRun) $("#header-run-name").textContent = state.currentRun.name;
  } catch (error) {
    metrics.innerHTML = emptyState("Workspace data unavailable", error.message);
    $("#dashboard-runs").replaceChildren();
  }
}

async function loadRunsPage() {
  const root = $("#runs-table");
  root.innerHTML = '<div class="loading">Loading reconciliations…</div>';
  try { root.innerHTML = runsTable(await loadRuns()); }
  catch (error) { root.innerHTML = emptyState("Could not load reconciliations", error.message); }
}

function populateRunSelect(selector, selected = "") {
  const select = $(selector);
  if (!select) return;
  select.replaceChildren();
  const placeholder = new Option(state.runs.length ? "Choose a run" : "No runs available", "");
  select.append(placeholder);
  for (const run of state.runs) select.append(new Option(`${run.name} · ${run.status}`, run.id));
  select.value = selected;
}

async function openRun(runId) {
  try {
    const run = await request(`/api/runs/${encodeURIComponent(runId)}`);
    state.currentRun = run;
    state.runResultsPage = 1;
    state.importRunId = runId;
    state.currentRunTab = "overview";
    $("#run-title").textContent = run.name;
    $("#run-subtitle").textContent = `${run.currency || "Currency detected from import"} · ${run.period_start || "Dates detected from import"}${run.period_end ? ` – ${run.period_end}` : ""} · ${run.id}`;
    $("#header-run-name").textContent = run.name;
    $("#run-summary").innerHTML = `<span>Currency <strong>${escapeHtml(run.currency || "Detected from file when available")}</strong></span><span>Statement period <strong>${escapeHtml(run.period_start || "Detected from file when available")}${run.period_end ? ` – ${escapeHtml(run.period_end)}` : ""}</strong></span><span>${statusBadge(run.status)}</span>`;
    renderRunActions();
    showPage("run-detail");
    await renderRunTab("overview");
  } catch (error) { toast(error.message, "error"); }
}

function renderRunActions() {
  const actions = $("#run-actions");
  const readonly = state.user.role === "auditor";
  const batches = state.currentRun.batches || [];
  const ready = ["bank", "ledger"].every(side => batches.some(batch => batch.side === side && batch.status === "confirmed"));
  actions.innerHTML = `${!readonly && state.currentRun.status !== "reconciled" && ready ? '<button class="button primary" data-run-action="reconcile" type="button">Run Reconciliation</button>' : ""}${state.currentRun.status === "reconciled" ? '<button class="button primary" data-run-tab="worklist" type="button">View Results</button>' : ""}<a class="button subtle" href="/api/runs/${encodeURIComponent(state.currentRun.id)}/export/matches.csv">Export matches</a>`;
}

async function renderRunTab(tab) {
  state.currentRunTab = tab;
  $$("[data-run-tab]").forEach(button => button.classList.toggle("active", button.dataset.runTab === tab));
  $("#run-overview").hidden = tab !== "overview";
  $("#run-imports").hidden = tab !== "imports";
  $("#run-worklist").hidden = tab !== "worklist";
  if (tab === "overview") await renderRunOverview();
  if (tab === "imports") renderRunImports();
  if (tab === "worklist") await renderRunResults();
}

async function renderRunOverview() {
  const run = state.currentRun;
  const batches = run.batches || [];
  const checks = [];
  for (const side of ["bank", "ledger"]) {
    const batch = batches.find(item => item.side === side && ["staged", "confirmed"].includes(item.status));
    if (!batch) { checks.push(`<div class="check-item"><span>${side} statement</span><strong>Not uploaded</strong></div>`); continue; }
    try {
      const check = await request(`/api/runs/${encodeURIComponent(run.id)}/statement-checks?side=${side}`);
      checks.push(`<div class="check-item"><span>${side} · ${check.batch_status}</span><strong>${check.total_rows} rows</strong><span>${check.duplicate_count} duplicates · ${check.out_of_period_count} outside detected period</span></div>`);
    } catch (error) { checks.push(`<div class="check-item"><span>${side}</span><strong>${escapeHtml(error.message)}</strong></div>`); }
  }
  let resultsHtml = "";
  if (run.status === "reconciled") {
    try {
      const result = await request(`/api/runs/${encodeURIComponent(run.id)}/results`);
      const summary = result.summary;
      resultsHtml = `<section class="section-box"><h2>Results saved</h2><div class="check-grid"><div class="check-item"><span>Matched items</span><strong>${summary.matched_items}</strong></div><div class="check-item"><span>Needs review</span><strong>${summary.review_required_items}</strong></div><div class="check-item"><span>Unmatched bank</span><strong>${summary.unmatched_bank_records}</strong></div><div class="check-item"><span>Unmatched ledger</span><strong>${summary.unmatched_ledger_records}</strong></div></div><p class="muted">The engine finished processing. A zero net activity difference is not proof that the bank account is fully reconciled; opening and closing balances are not checked.</p></section>`;
    } catch (error) { resultsHtml = `<p class="form-error">${escapeHtml(error.message)}</p>`; }
  }
  $("#run-overview").innerHTML = `<section class="section-box"><h2>Source checks</h2><div class="check-grid">${checks.join("")}</div><div class="form-actions"><button class="button subtle" data-run-tab="imports" type="button">Manage imports</button>${run.status === "reconciled" ? '<button class="button subtle" data-run-tab="worklist" type="button">View results</button>' : ""}</div></section>${resultsHtml}`;
}

function renderRunImports() {
  const run = state.currentRun;
  const batches = run.batches || [];
  const rows = ["bank", "ledger"].map(side => {
    const batch = batches.find(item => item.side === side && ["staged", "confirmed"].includes(item.status));
    const label = side === "bank" ? "Bank statement" : "Internal ledger";
    return `<section class="section-box"><h2>${label}</h2>${batch ? `<p><strong>${escapeHtml(batch.filename)}</strong> · ${statusBadge(batch.status)} · ${batch.row_count || 0} rows</p><div class="table-actions"><button class="button subtle" data-review-batch="${escapeHtml(batch.id)}" type="button">Review ${side} import</button>${batch.status === "staged" ? `<button class="button danger" data-discard-batch="${escapeHtml(batch.id)}" type="button">Discard staged file</button>` : ""}</div>` : `<p class="muted">Choose one CSV file containing this source’s transactions.</p>`}${!batch && state.user.role !== "auditor" ? `<form class="upload-layout" data-upload-form="${side}"><label>${label} CSV<input type="file" name="file" accept=".csv,text/csv" required></label><label>Delimiter if ambiguous<select name="delimiter"><option value="">Detect automatically</option><option value=",">Comma</option><option value=";">Semicolon</option></select></label><button class="button primary" type="submit">Upload ${side === "bank" ? "Bank" : "Ledger"} CSV</button></form>` : ""}</section>`;
  }).join("");
  const bothStaged = ["bank", "ledger"].every(side => batches.some(batch => batch.side === side && batch.status === "staged"));
  const bothConfirmed = ["bank", "ledger"].every(side => batches.some(batch => batch.side === side && batch.status === "confirmed"));
  const footer = bothStaged && state.user.role !== "auditor"
    ? `<section class="section-box"><h2>Review and confirm</h2><p class="muted">Open each staged import to review its detected columns, validation status and compact row preview. Both imports must pass validation before confirmation.</p><button class="button primary" data-confirm-run-imports="${escapeHtml(run.id)}" type="button">Confirm Imports</button></section>`
    : bothConfirmed
      ? `<section class="section-box"><p>Both sources are confirmed and ready for matching.</p>${state.user.role !== "auditor" && run.status !== "reconciled" ? '<button class="button primary" data-run-action="reconcile" type="button">Run Reconciliation</button>' : ""}</section>`
      : "";
  $("#run-imports").innerHTML = `<p class="muted">Each upload is previewed and validated before it can be confirmed.</p><div class="source-upload-grid">${rows}</div>${footer}`;
}

async function createRun(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("button[type=submit]", form);
  const error = $("#run-form-error");
  let createdRun = null;
  error.hidden = true;
  setBusy(button, true, "Creating run…");
  try {
    const payload = { name: new FormData(form).get("name")?.toString().trim() || "" };
    const run = createdRun = await request("/api/runs", { method: "POST", body: payload });
    toast("Reconciliation run created. Upload the bank and ledger CSV files to continue.");
    await loadRuns();
    await openRun(run.id);
    await renderRunTab("imports");
  } catch (e) {
    if (createdRun) {
      toast(`Run created, but another step needs attention: ${e.message}`, "error");
      await loadRuns();
      await openRun(createdRun.id);
      await renderRunTab("imports");
    } else {
      error.textContent = e.message;
      error.hidden = false;
    }
  }
  finally { setBusy(button, false); }
}

async function openSampleReconciliation(button) {
  if (state.user.role === "auditor") {
    toast("Your account is read-only and cannot create a sample run.", "error");
    return;
  }
  setBusy(button, true, "Preparing sample…");
  try {
    const run = await request("/api/runs/sample", { method: "POST", body: {} });
    await loadRuns();
    await openRun(run.id);
    await renderRunTab("worklist");
    toast("Sample bank and ledger reconciliation is ready.");
  } catch (error) {
    toast(error.message, "error");
  } finally { setBusy(button, false); }
}

async function handleUpload(form) {
  const side = form.dataset.uploadForm;
  const file = $("input[type=file]", form).files[0];
  if (!state.currentRun || !file) return;
  const button = $("button[type=submit]", form);
  setBusy(button, true, "Staging…");
  try {
    const delimiter = $("select[name=delimiter]", form).value;
    const inspectQuery = new URLSearchParams({ filename: file.name, side });
    if (delimiter) inspectQuery.set("delimiter", delimiter);
    const suggestion = await request(`/api/imports/inspect?${inspectQuery}`, { method: "POST", rawBody: file, contentType: "text/csv" });
    const suffix = delimiter ? `&delimiter=${encodeURIComponent(delimiter)}` : "";
    const data = await request(`/api/runs/${encodeURIComponent(state.currentRun.id)}/imports/${side}?filename=${encodeURIComponent(file.name)}${suffix}`, { method: "PUT", rawBody: file, contentType: "text/csv" });
    const mapping = suggestion.mapping || {};
    let autoMapped = false;
    if (mapping.can_auto_map && mapping.date_format && !suggestion.date_ambiguous) {
      await request(`/api/imports/${encodeURIComponent(data.batch_id)}/map`, {
        method: "POST",
        body: { ...mapping, date_format: mapping.date_format, ledger_perspective: mapping.ledger_perspective || null }
      });
      autoMapped = true;
    }
    toast(`${data.total_data_rows} rows uploaded${autoMapped ? " and columns detected automatically" : ""}. Review the import before confirmation.`);
    await openRun(state.currentRun.id);
    await renderRunTab("imports");
    await openBatchReview(data.batch_id);
  } catch (e) { toast(e.message, "error"); }
  finally { setBusy(button, false); }
}

function addMappingSelect(container, field, label, headers, selected = "", optional = false) {
  const wrap = document.createElement("label");
  wrap.textContent = label;
  const select = document.createElement("select");
  select.name = field;
  select.required = !optional;
  const blank = new Option(optional ? "Not mapped" : "Choose column", "");
  select.append(blank);
  headers.forEach(header => select.append(new Option(header, header)));
  select.value = selected || "";
  wrap.append(select); container.append(wrap); return wrap;
}

function buildMappingForm(batch, headers, savedMapping = {}) {
  const mapArea = document.createElement("section");
  mapArea.className = "section-box";
  const title = document.createElement("h2");
  title.textContent = `Map ${batch.side} columns · ${batch.filename || "staged CSV"}`;
  mapArea.append(title);
  const form = document.createElement("form");
  form.dataset.mapBatch = batch.id;
  const grid = document.createElement("div"); grid.className = "map-grid";
  addMappingSelect(grid, "date_column", "Date column", headers, savedMapping.date_column);
  addMappingSelect(grid, "description_column", "Description column", headers, savedMapping.description_column);
  const modeWrap = document.createElement("label"); modeWrap.textContent = "Amount layout";
  const mode = document.createElement("select"); mode.name = "amount_mode";
  mode.append(new Option("One signed amount column", "signed"), new Option("Separate debit and credit columns", "debit_credit"));
  mode.value = savedMapping.amount_mode || "signed"; modeWrap.append(mode); grid.append(modeWrap);
  const signedField = addMappingSelect(grid, "amount_column", "Signed amount column", headers, savedMapping.amount_column, true);
  const debitField = addMappingSelect(grid, "debit_column", "Debit column", headers, savedMapping.debit_column, true);
  const creditField = addMappingSelect(grid, "credit_column", "Credit column", headers, savedMapping.credit_column, true);
  const extra = document.createElement("details"); extra.className = "mapping-advanced";
  const extraSummary = document.createElement("summary"); extraSummary.textContent = "Optional fields and parsing options"; extra.append(extraSummary);
  const advancedGrid = document.createElement("div"); advancedGrid.className = "map-grid";
  addMappingSelect(advancedGrid, "reference_column", "Reference column", headers, savedMapping.reference_column, true);
  addMappingSelect(advancedGrid, "currency_column", "Currency column", headers, savedMapping.currency_column, true);
  const decimalWrap = document.createElement("label"); decimalWrap.textContent = "Decimal separator";
  const decimal = document.createElement("select"); decimal.name = "decimal_separator";
  decimal.append(new Option("Dot (.)", "."), new Option("Comma (,)", ",")); decimal.value = savedMapping.decimal_separator || "."; decimalWrap.append(decimal); advancedGrid.append(decimalWrap);
  const dateWrap = document.createElement("label"); dateWrap.textContent = "Date format (choose when asked)";
  const dateSelect = document.createElement("select"); dateSelect.name = "date_format";
  [["", "Detect / unresolved"], ["YYYY-MM-DD", "YYYY-MM-DD"], ["DD/MM/YYYY", "DD/MM/YYYY"], ["MM/DD/YYYY", "MM/DD/YYYY"]].forEach(([v,t]) => dateSelect.append(new Option(t,v)));
  dateSelect.value = savedMapping.date_format || ""; dateWrap.append(dateSelect); advancedGrid.append(dateWrap);
  if (batch.side === "ledger") {
    const perspectiveWrap = document.createElement("label"); perspectiveWrap.textContent = "Ledger sign convention (required)";
    const perspective = document.createElement("select"); perspective.name = "ledger_perspective";
    perspective.append(new Option("Choose explicitly", ""), new Option("Cash account (debit increases cash)", "cash_account"), new Option("Bank view (credit increases cash)", "bank"));
    perspective.value = savedMapping.ledger_perspective || ""; perspectiveWrap.append(perspective); grid.append(perspectiveWrap);
  }
  const negatives = document.createElement("div"); negatives.className = "map-grid";
  negatives.append(checkControl("Allow parentheses as negative", "negative_parentheses", Boolean(savedMapping.negative_formats?.parentheses)));
  negatives.append(checkControl("Allow trailing minus", "negative_trailing_minus", Boolean(savedMapping.negative_formats?.trailing_minus)));
  advancedGrid.append(negatives); extra.append(advancedGrid);
  const updateAmountFields = () => {
    signedField.hidden = mode.value !== "signed";
    debitField.hidden = mode.value !== "debit_credit";
    creditField.hidden = mode.value !== "debit_credit";
  };
  mode.addEventListener("change", updateAmountFields); updateAmountFields();
  form.append(grid, extra);
  const message = document.createElement("p"); message.className = "form-error"; message.hidden = true;
  const submit = document.createElement("button"); submit.className = "button primary"; submit.type = "submit"; submit.textContent = "Map and validate";
  form.append(message, submit); mapArea.append(form);
  return mapArea;
}

function checkControl(labelText, name, checked) {
  const label = document.createElement("label"); label.className = "check-control";
  const input = document.createElement("input"); input.type = "checkbox"; input.name = name; input.checked = checked;
  label.append(input, document.createTextNode(labelText)); return label;
}

function mappingPayload(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  data.date_format ||= null;
  data.reference_column ||= null;
  data.currency_column ||= null;
  data.negative_formats = { leading_minus: true, parentheses: $("[name=negative_parentheses]", form)?.checked || false, trailing_minus: $("[name=negative_trailing_minus]", form)?.checked || false };
  delete data.negative_parentheses; delete data.negative_trailing_minus;
  return data;
}

async function openBatchReview(batchId) {
  try {
    let info = await request(`/api/imports/${encodeURIComponent(batchId)}`);
    // Existing staged batches can be mapped automatically when all required
    // columns and an unambiguous supported date format were detected.
    const candidate = info.suggested_mapping || {};
    if (!info.mapping && info.status === "staged" && candidate.can_auto_map && candidate.date_format && !info.date_ambiguous) {
      await request(`/api/imports/${encodeURIComponent(batchId)}/map`, {
        method: "POST", body: { ...candidate, date_format: candidate.date_format, ledger_perspective: candidate.ledger_perspective || null }
      });
      info = await request(`/api/imports/${encodeURIComponent(batchId)}`);
    }
    const review = await request(`/api/imports/${encodeURIComponent(batchId)}/rows?page=1&page_size=200`);
    const summary = await request(`/api/imports/${encodeURIComponent(batchId)}/summary`);
    const host = $("#import-workspace");
    host.replaceChildren();
    if (!info.mapping && info.status === "staged") host.append(buildMappingForm(info, info.detected_headers || [], info.suggested_mapping || {}));
    host.append(renderImportSummary(info, summary));
    if (info.mapping) host.append(renderRowsTable(info, review.rows || []));
    showPage("import");
  } catch (error) { toast(error.message, "error"); }
}

function renderImportSummary(batch, summary) {
  const section = document.createElement("section"); section.className = "section-box";
  const title = document.createElement("h2"); title.textContent = `${batch.filename || "Import"} · ${batch.side} · ${batch.status}`;
  const p = document.createElement("p"); p.className = "muted"; p.textContent = `${summary.counts.total} rows · ${summary.counts.valid} valid · ${summary.counts.invalid} invalid · ${summary.counts.ambiguous_date} ambiguous · ${summary.counts.duplicate_flagged} flagged duplicates`;
  const status = document.createElement("p"); status.textContent = "No opening or closing balance is requested. This review checks transaction rows, dates, amounts and duplicate flags.";
  section.append(title,p,status);
  if (batch.status === "staged" && state.user.role !== "auditor") {
    const note = document.createElement("p"); note.className = "muted"; note.textContent = "After reviewing both source files, return to the run’s Imports page and select Confirm Imports."; section.append(note);
    const back = document.createElement("button"); back.className = "button subtle"; back.type = "button"; back.dataset.returnRunImports = ""; back.textContent = "Return to run imports"; section.append(back);
  }
  return section;
}

function renderRowsTable(batch, rows) {
  const section = document.createElement("section"); section.className = "section-block";
  if (!rows.length) { section.innerHTML = `<div class="empty-state"><strong>No rows to review</strong><span>The CSV is staged but has not been mapped.</span></div>`; return section; }
  const table = values => `<div class="table-wrap"><table><thead><tr><th>Row</th><th>Date</th><th>Amount (cents)</th><th>Description</th><th>Reference</th><th>Status / issues</th><th>Actions</th></tr></thead><tbody>${values.map(row => `<tr data-row-id="${row.id}"><td>${row.row_number}</td><td><input class="row-edit" data-edit="date" aria-label="Date for row ${row.row_number}" value="${escapeHtml(row.date_iso || "")}" ${batch.status === "confirmed" ? "disabled" : ""}></td><td><input class="row-edit numeric" data-edit="amount" aria-label="Amount for row ${row.row_number}" value="${row.amount_cents == null ? "" : escapeHtml(formatDecimal(row.amount_cents))}" ${batch.status === "confirmed" ? "disabled" : ""}></td><td><input class="row-edit" data-edit="description" aria-label="Description for row ${row.row_number}" value="${escapeHtml(row.description || "")}" ${batch.status === "confirmed" ? "disabled" : ""}></td><td><input class="row-edit" data-edit="reference" aria-label="Reference for row ${row.row_number}" value="${escapeHtml(row.reference || "")}" ${batch.status === "confirmed" ? "disabled" : ""}></td><td>${statusBadge(row.status)}<div class="row-issues">${escapeHtml((row.issues || []).join("; "))}</div></td><td><div class="table-actions">${batch.status === "staged" && state.user.role !== "auditor" ? `<button class="button small" data-save-row="${row.id}" data-batch="${escapeHtml(batch.id)}" type="button">Save</button><button class="button small" data-row-action="${row.status === "rejected" ? "restore" : "reject"}" data-row="${row.id}" data-batch="${escapeHtml(batch.id)}" type="button">${row.status === "rejected" ? "Restore" : "Reject"}</button>${row.status === "duplicate_flagged" ? `<button class="button small" data-row-action="include-duplicate" data-row="${row.id}" data-batch="${escapeHtml(batch.id)}" type="button">Include</button>` : ""}` : ""}</div></td></tr>`).join("")}</tbody></table></div>`;
  section.innerHTML = `${table(rows.slice(0, 5))}${rows.length > 5 ? `<details class="compact-preview"><summary>Show remaining ${rows.length - 5} rows</summary>${table(rows.slice(5))}</details>` : ""}`;
  return section;
}

function formatDecimal(cents) {
  const n = Number(cents); const sign = n < 0 ? "-" : ""; const abs = Math.abs(n);
  return `${sign}${Math.floor(abs/100)}.${String(abs%100).padStart(2,"0")}`;
}

async function loadImportWorkspace() {
  const select = $("#import-run-select");
  await loadRuns().catch(() => {});
  populateRunSelect("#import-run-select", state.importRunId || state.currentRun?.id || "");
  if (!select.value) {
    $("#import-workspace").innerHTML = emptyState(
      "Create a reconciliation to import CSV files",
      "Uploads are attached to a run so the statements can be checked against its period and reconciled by transaction.",
      "Create reconciliation",
      "new-run"
    );
    return;
  }
  state.importRunId = select.value;
  await openRunForImports(select.value);
}

async function openRunForImports(runId) {
  try {
    state.importRunId = runId;
    await openRun(runId);
    await renderRunTab("imports");
    showPage("run-detail");
  } catch (error) { toast(error.message, "error"); }
}

function createUploadForm(side) {
  const form = document.createElement("form"); form.className = "upload-layout"; form.dataset.uploadForm = side;
  form.innerHTML = `<label>CSV file<input type="file" name="file" accept=".csv,text/csv" required></label><label>Delimiter if ambiguous<select name="delimiter"><option value="">Detect from header</option><option value=",">Comma</option><option value=";">Semicolon</option></select></label><button class="button primary" type="submit">Stage ${escapeHtml(side)} CSV</button>`;
  return form;
}

async function confirmBatch(batchId) {
  try {
    const result = await request(`/api/imports/${encodeURIComponent(batchId)}/confirm`, { method: "POST", body: {} });
    toast(`${result.confirmed_count} rows confirmed.`);
    await loadRuns();
    if (state.currentRun) await openRun(state.currentRun.id);
    await openBatchReview(batchId);
  } catch (error) { toast(error.message, "error"); }
}

async function confirmRunImports(runId) {
  const button = $(`[data-confirm-run-imports="${CSS.escape(runId)}"]`);
  if (!button) return;
  if (!window.confirm("Confirm both reviewed CSV imports? This makes their valid rows available to the reconciliation engine.")) return;
  setBusy(button, true, "Checking imports…");
  try {
    const run = await request(`/api/runs/${encodeURIComponent(runId)}`);
    const batches = ["bank", "ledger"].map(side => (run.batches || []).find(batch => batch.side === side && batch.status === "staged"));
    if (batches.some(batch => !batch)) throw new Error("Upload and map both the bank and ledger CSV files first.");
    for (const batch of batches) {
      const info = await request(`/api/imports/${encodeURIComponent(batch.id)}`);
      if (!info.mapping) throw new Error(`Review and map the ${batch.side} CSV before confirming imports.`);
      const summary = await request(`/api/imports/${encodeURIComponent(batch.id)}/summary`);
      const counts = summary.counts || {};
      if (counts.valid === 0 || counts.invalid || counts.ambiguous_date || counts.duplicate_flagged) {
        throw new Error(`${batch.side} import needs attention: ${counts.valid || 0} valid, ${counts.invalid || 0} invalid, ${counts.ambiguous_date || 0} ambiguous dates, ${counts.duplicate_flagged || 0} flagged duplicates.`);
      }
    }
    for (const batch of batches) await request(`/api/imports/${encodeURIComponent(batch.id)}/confirm`, { method: "POST", body: {} });
    toast("Bank and ledger imports confirmed. You can now run reconciliation.");
    await loadRuns();
    await openRun(runId);
    await renderRunTab("imports");
  } catch (error) {
    toast(error.message, "error");
    setBusy(button, false);
  }
}

async function mapBatch(form) {
  const button = $("button[type=submit]", form); setBusy(button, true, "Validating…");
  const message = $(".form-error", form); message.hidden = true;
  try {
    const result = await request(`/api/imports/${encodeURIComponent(form.dataset.mapBatch)}/map`, { method: "POST", body: mappingPayload(form) });
    if (result.date_format_confirmation_required) {
      const dateSelect = $("[name=date_format]", form); dateSelect.value = result.proposed_date_format;
      message.textContent = `Dates appear to use ${result.proposed_date_format}. Confirm the selection and submit again.`; message.hidden = false; return;
    }
    if (result.is_date_ambiguous) {
      message.textContent = "Dates are ambiguous. Choose DD/MM/YYYY or MM/DD/YYYY and submit again."; message.hidden = false; return;
    }
    toast(`${result.total_rows} rows mapped and checked.`);
    await loadRuns();
    await openBatchReview(form.dataset.mapBatch);
  } catch (error) { message.textContent = error.message; message.hidden = false; }
  finally { setBusy(button, false); }
}

async function saveRow(button) {
  const tr = button.closest("tr");
  const values = {};
  $$('[data-edit]', tr).forEach(input => values[input.dataset.edit] = input.value);
  setBusy(button, true, "Saving…");
  try { await request(`/api/imports/${encodeURIComponent(button.dataset.batch)}/rows/${button.dataset.saveRow}`, { method: "PATCH", body: values }); toast("Row updated."); await openBatchReview(button.dataset.batch); }
  catch (error) { toast(error.message, "error"); setBusy(button, false); }
}

async function updateRowStatus(button) {
  const action = button.dataset.rowAction;
  if (action === "reject" && !window.confirm("Reject this row? It will not be included when the batch is confirmed.")) return;
  try {
    await request(`/api/imports/${encodeURIComponent(button.dataset.batch)}/rows/${button.dataset.row}/${action}`, { method: "POST", body: {} });
    toast(action === "reject" ? "Row rejected." : action === "restore" ? "Row restored." : "Duplicate included.");
    await openBatchReview(button.dataset.batch);
  } catch (error) { toast(error.message, "error"); }
}

async function discardBatch(batchId) {
  if (!window.confirm("Discard this staged import? Its rows will be excluded from the run.")) return;
  try { await request(`/api/imports/${encodeURIComponent(batchId)}/discard`, { method: "POST", body: {} }); toast("Staged import discarded."); await loadRuns(); await openRun(state.currentRun.id); await renderRunTab("imports"); }
  catch (error) { toast(error.message, "error"); }
}

async function reconcileRun() {
  const run = state.currentRun;
  if (!run || !window.confirm("Run rules-only reconciliation on the confirmed bank and ledger records? This run cannot be reconciled a second time.")) return;
  const button = $("[data-run-action=reconcile]"); setBusy(button, true, "Reconciling…");
  try {
    await request(`/api/runs/${encodeURIComponent(run.id)}/reconcile`, { method: "POST", body: {} });
    toast("Reconciliation completed and results were saved."); await loadRuns(); await openRun(run.id); await renderRunTab("worklist");
  } catch (error) { toast(error.message, "error"); setBusy(button, false); }
}

async function renderRunResults() {
  const root = $("#run-worklist"); root.innerHTML = '<div class="loading">Loading persisted results…</div>';
  try {
    const result = await request(`/api/runs/${encodeURIComponent(state.currentRun.id)}/results`);
    const summary = result.summary;
    const metric = (label, value, target, hint) => `<button class="summary-metric" type="button" data-summary-target="${target}"><span>${label}</span><strong>${value}</strong><small>${hint}</small></button>`;
    const amountDiff = formatCents(summary.net_activity_difference_cents, result.currency);
    const matchedDiff = formatCents(summary.matched_amount_difference_cents, result.currency);
    root.innerHTML = `<section class="section-box"><h2>Run results</h2><p class="muted">Saved by the reconciliation engine. Select a count to inspect its records.</p><div class="summary-metrics">${metric("Total bank records", summary.bank_records, "records", "Imported bank rows")}${metric("Total ledger records", summary.ledger_records, "records", "Imported ledger rows")}${metric("Matched bank records", summary.matched_bank_records, "records", "Bank rows in stored pairs")}${metric("Matched ledger records", summary.matched_ledger_records, "records", "Ledger rows in stored pairs")}${metric("Match pairs", summary.matched_items, "matches", "Stored transaction pairs")}${metric("Unmatched bank records", summary.unmatched_bank_records, "exceptions", "Bank rows without an accepted pair")}${metric("Unmatched ledger records", summary.unmatched_ledger_records, "exceptions", "Ledger rows without an accepted pair")}${metric("Items requiring review", summary.review_required_items, "exceptions", "Open or review-required exceptions")}</div><div class="amount-summary"><span>Bank net activity <strong>${formatCents(summary.bank_net_activity_cents, result.currency)}</strong></span><span>Ledger net activity <strong>${formatCents(summary.ledger_net_activity_cents, result.currency)}</strong></span><span>Net activity difference <strong>${amountDiff}</strong></span><span>Matched amount differences (sum) <strong>${matchedDiff}</strong></span></div><p class="status-explanation"><strong>Status guide:</strong> Matched means the engine stored a transaction pair; unmatched means a row has no accepted pair; review required means an exception is still open for a person to inspect.</p><p class="callout">Processing is complete, but that does not mean the account is fully reconciled. The workflow checks transaction pairing and net activity only; it does not verify opening or closing account balances.</p><p class="muted">${result.limitations.map(escapeHtml).join(" ")}</p></section><section class="section-box ai-panel" aria-labelledby="ai-insights-title"><div class="ai-panel-heading"><div><h2 id="ai-insights-title">AI Reconciliation Insights</h2><p class="muted">Optional Gemini analysis of a bounded set of persisted exceptions and candidate records. Explanations are hypotheses and require human review.</p></div><button class="button primary" type="button" data-generate-ai>Generate AI insights</button></div><div id="ai-insights-content" aria-live="polite"><p class="muted">Checking for saved insights…</p></div><div class="ai-assistant"><h3>Ask about this run</h3><form data-ai-question><label for="ai-question-input">Question</label><div class="ai-question-row"><input id="ai-question-input" name="question" maxlength="500" placeholder="Which bank records remain unmatched?" required><button class="button subtle" type="submit">Ask</button></div></form><div id="ai-answer-content" aria-live="polite"></div></div><p><a class="button subtle" href="/api/runs/${encodeURIComponent(state.currentRun.id)}/ai-report">Download reconciliation report</a></p></section><div class="toolbar-line"><label>Result type<select id="run-results-type"><option value="matches">Matches</option><option value="exceptions">Exceptions</option><option value="records">Records</option></select></label><label>Status<select id="run-results-status"><option value="">All statuses</option><option value="matched">Matched</option><option value="open">Open</option><option value="resolved">Resolved</option></select></label><label>Search<input id="run-results-search" type="search" placeholder="Reference, description, ID"></label><button class="button subtle" data-run-results-search type="button">Apply</button></div><div id="run-results-table" class="table-wrap"></div><div class="pagination"><button class="button subtle" data-run-results-page="prev" type="button">Previous</button><span id="run-results-page-label">Page 1</span><button class="button subtle" data-run-results-page="next" type="button">Next</button></div>`;
    await loadAiInsights();
    await queryRunResults();
  } catch (error) { root.innerHTML = emptyState("Results unavailable", error.message); }
}

async function loadAiInsights() {
  const host = $("#ai-insights-content");
  if (!host || !state.currentRun) return;
  host.innerHTML = '<p class="loading">Loading saved insight state…</p>';
  try { renderAiInsights(await request(`/api/runs/${encodeURIComponent(state.currentRun.id)}/ai-insights`)); }
  catch (error) { host.innerHTML = `<p class="form-error">${escapeHtml(error.message)}</p>`; }
}

function renderAiInsights(data) {
  const host = $("#ai-insights-content"); if (!host) return;
  const label = escapeHtml(data.label || (data.status === "success" ? "Gemini-generated; validated" : "Rule-based fallback (not AI)"));
  const facts = data.facts || {};
  const statusText = data.error ? `<p class="form-error">${escapeHtml(data.error)}</p>` : "";
  const issues = (data.issues || []).map((issue, index) => `<article class="ai-issue"><h3>${index + 1}. ${escapeHtml(issue.issue_type)} <span class="status-label">${escapeHtml(issue.priority)}</span></h3><p><strong>Transaction IDs:</strong> ${issue.transaction_ids.map(escapeHtml).join(", ")}</p><p><strong>Verified evidence</strong></p><ul>${issue.observed_evidence.map(item => `<li><code>${escapeHtml(item.record_id)}</code> · ${escapeHtml(item.field)}: ${escapeHtml(typeof item.value === "object" ? JSON.stringify(item.value) : item.value)}</li>`).join("")}</ul><p><strong>Hypothesis:</strong> ${escapeHtml(issue.possible_explanation)}</p><p><strong>Suggested action:</strong> ${escapeHtml(issue.recommended_action)}</p><p><strong>Missing information:</strong> ${issue.missing_information.length ? issue.missing_information.map(escapeHtml).join("; ") : "Not specified"}</p><p class="muted">Human review required. This suggestion does not confirm or change a match.</p></article>`).join("");
  host.innerHTML = `<p class="ai-status"><strong>${label}</strong>${data.model ? ` · ${escapeHtml(data.model)}` : ""}${data.generated_at ? ` · ${escapeHtml(data.generated_at)}` : ""}</p><p>${escapeHtml(data.summary || "")}</p>${statusText}${data.status === "fallback" || data.status === "timeout" || data.status === "rate_limited" || data.status === "failed" || data.status === "invalid_response" ? `<p class="muted">Persisted totals: ${Number(facts.bank_records || 0)} bank rows · ${Number(facts.ledger_records || 0)} ledger rows · ${Number(facts.matched_items || 0)} match pairs · ${Number(facts.review_required_items || 0)} require review. Candidate issues considered: ${Number(data.fallback_issue_count || 0)}.</p>` : ""}${issues || `<p class="muted">${data.status === "success" ? "No issue suggestions were returned for the bounded evidence supplied." : "No model-generated explanations are available. Configure GEMINI_API_KEY on the server to enable Gemini."}</p>`}`;
}

async function generateAiInsights(button) {
  if (!state.currentRun) return;
  setBusy(button, true, "Contacting Gemini…");
  const host = $("#ai-insights-content"); host.innerHTML = '<p class="loading">Sending bounded run evidence to Gemini…</p>';
  try { renderAiInsights(await request(`/api/runs/${encodeURIComponent(state.currentRun.id)}/ai-insights`, { method: "POST", body: {} })); }
  catch (error) { host.innerHTML = `<p class="form-error">AI insights unavailable: ${escapeHtml(error.message)}. No AI result was saved.</p>`; }
  finally { setBusy(button, false); }
}

async function askRunQuestion(form) {
  const button = form.querySelector("button[type=submit]"), output = $("#ai-answer-content");
  setBusy(button, true, "Asking…"); output.innerHTML = '<p class="loading">Checking this run’s stored records…</p>';
  try {
    const data = await request(`/api/runs/${encodeURIComponent(state.currentRun.id)}/ai-question`, { method: "POST", body: { question: form.elements.question.value } });
    output.innerHTML = `<div class="ai-answer"><p class="ai-status"><strong>${escapeHtml(data.label)}</strong>${data.model ? ` · ${escapeHtml(data.model)}` : ""}</p><p>${escapeHtml(data.answer)}</p>${data.transaction_ids?.length ? `<p><strong>Cited records:</strong> ${data.transaction_ids.map(escapeHtml).join(", ")}</p>` : ""}${data.unknowns?.length ? `<p class="muted"><strong>Unresolved:</strong> ${data.unknowns.map(escapeHtml).join("; ")}</p>` : ""}${data.evidence?.length ? `<ul>${data.evidence.map(r => `<li><code>${escapeHtml(r.record_id)}</code> · ${escapeHtml(r.side)} · ${escapeHtml(r.date)} · ${formatCents(r.amount_cents, state.currentRun.currency)} · ${escapeHtml(r.description)}</li>`).join("")}</ul>` : ""}</div>`;
  } catch (error) { output.innerHTML = `<p class="form-error">Assistant unavailable: ${escapeHtml(error.message)}</p>`; }
  finally { setBusy(button, false); }
}

async function queryRunResults() {
  const root = $("#run-results-table");
  const type = $("#run-results-type").value;
  const query = new URLSearchParams({ page: String(state.runResultsPage), page_size: String(state.runResultsPageSize) });
  const search = $("#run-results-search").value.trim();
  const status = $("#run-results-status").value;
  if (search) query.set("search", search);
  if (status && type !== "records") query.set("status", status);
  root.innerHTML = '<div class="loading">Loading stored results…</div>';
  try {
    const data = await request(`/api/runs/${encodeURIComponent(state.currentRun.id)}/${type}?${query}`);
    const items = data[type] || [];
    root.innerHTML = !items.length ? emptyState("No results found", "Try a different status or search term.") : type === "matches" ? matchesTable(items, state.currentRun.id) : type === "exceptions" ? exceptionsTable(items) : recordsTable(items, state.currentRun.currency);
    $("#run-results-page-label").textContent = `Page ${data.page} · ${data.total_count} items`;
    $("[data-run-results-page=prev]").disabled = data.page <= 1;
    $("[data-run-results-page=next]").disabled = data.page * data.page_size >= data.total_count;
    $("#run-results-status").disabled = type === "records";
  } catch (error) { root.innerHTML = emptyState("Results unavailable", error.message); }
}

function matchesTable(matches, runId) {
  if (!matches.length) return emptyState("No matches stored", "Run reconciliation after both sources are confirmed.");
  return `<div class="table-wrap"><table><thead><tr><th>Match</th><th>Method</th><th>Rule score</th><th>Amount delta</th><th>Date delta</th><th>Reference</th></tr></thead><tbody>${matches.map(match => `<tr><td><button class="link-button" data-open-match="${escapeHtml(match.id)}" data-run="${escapeHtml(runId)}" type="button">${escapeHtml(match.id)}</button></td><td>${escapeHtml(match.match_type)}</td><td>${match.rule_score == null ? "—" : `${(Number(match.rule_score) * 100).toFixed(1)}%`} <span class="muted">(not a probability)</span></td><td class="numeric">${Number(match.features.amount_delta_cents).toLocaleString()}¢</td><td class="numeric">${Number(match.features.date_delta_days).toLocaleString()} days</td><td>${match.features.reference_equal ? "Equal" : "Different / missing"}</td></tr>`).join("")}</tbody></table></div>`;
}

function exceptionsTable(exceptions) {
  if (!exceptions.length) return emptyState("No exceptions", "The engine stored no unmatched records for this run.");
  return `<div class="table-wrap"><table><thead><tr><th>Record</th><th>Side</th><th>Reason</th><th>Status</th><th>Date</th><th>Description</th><th>Amount</th></tr></thead><tbody>${exceptions.map(item => `<tr><td class="mono">${escapeHtml(item.record_id)}</td><td>${escapeHtml(item.side)}</td><td>${escapeHtml(item.reason)}</td><td>${statusBadge(item.status)}</td><td>${escapeHtml(item.date)}</td><td>${escapeHtml(item.description)}</td><td class="numeric">${formatCents(item.amount_cents, state.currentRun.currency)}</td></tr>`).join("")}</tbody></table></div>`;
}

async function loadWorklist() {
  await loadRuns().catch(() => {});
  populateRunSelect("#worklist-run-select", state.currentRun?.id || state.runs[0]?.id || "");
  await queryWorklist();
}

async function queryWorklist() {
  const runId = $("#worklist-run-select").value;
  if (!runId) { $("#worklist-table").innerHTML = emptyState("No runs available", "Create a reconciliation to populate this worklist."); return; }
  const type = $("#worklist-type").value;
  const query = new URLSearchParams({ page: String(state.worklistPage), page_size: String(state.worklistPageSize) });
  const search = $("#worklist-search").value.trim(); const status = $("#worklist-status").value;
  if (search) query.set("search", search); if (status && type !== "records") query.set("status", status);
  const root = $("#worklist-table"); root.innerHTML = '<div class="loading">Loading stored items…</div>';
  try {
    const data = await request(`/api/runs/${encodeURIComponent(runId)}/${type}?${query}`);
    const key = type === "records" ? "records" : type;
    const items = data[key] || []; state.worklistTotal = data.total_count || 0;
    if (!items.length) root.innerHTML = emptyState("No items found", "Try another status or search term.");
    else if (type === "matches") root.innerHTML = matchesTable(items, runId);
    else if (type === "exceptions") root.innerHTML = exceptionsTable(items);
    else root.innerHTML = recordsTable(items, state.runs.find(run => run.id === runId)?.currency || null);
    $("#worklist-page-label").textContent = `Page ${state.worklistPage} · ${state.worklistTotal} items`;
    $("#worklist-prev").disabled = state.worklistPage <= 1;
    $("#worklist-next").disabled = state.worklistPage * state.worklistPageSize >= state.worklistTotal;
  } catch (error) { root.innerHTML = emptyState("Worklist unavailable", error.message); }
}

function recordsTable(records, currency) {
  return `<div class="table-wrap"><table><thead><tr><th>Record</th><th>Side</th><th>Date</th><th>Description</th><th>Reference</th><th class="numeric">Amount</th></tr></thead><tbody>${records.map(row => `<tr><td class="mono">${escapeHtml(row.id)}</td><td>${escapeHtml(row.side)}</td><td>${escapeHtml(row.date)}</td><td>${escapeHtml(row.description)}</td><td>${escapeHtml(row.reference)}</td><td class="numeric">${formatCents(row.amount_cents,currency)}</td></tr>`).join("")}</tbody></table></div>`;
}

async function openMatch(runId, matchId) {
  try {
    const match = await request(`/api/runs/${encodeURIComponent(runId)}/matches/${encodeURIComponent(matchId)}`);
    const run = state.runs.find(item => item.id === runId) || state.currentRun;
    const currency = run?.currency || null;
    $("#match-title").textContent = match.id;
    const records = side => (match[`${side}_records`] || []).map(record => `<article class="record-panel"><h2>${escapeHtml(side)} record ${escapeHtml(record.record_id)}</h2><dl><dt>Date</dt><dd>${escapeHtml(record.date)}</dd><dt>Amount</dt><dd>${formatCents(record.amount_cents, currency)}</dd><dt>Description</dt><dd>${escapeHtml(record.description)}</dd><dt>Reference</dt><dd>${escapeHtml(record.reference)}</dd></dl></article>`).join("") || `<div class="empty-state">No ${side} member records.</div>`;
    $("#match-detail-content").innerHTML = `<div class="callout">Stored method: ${escapeHtml(match.match_type)}. Stored rule score: ${match.rule_score == null ? "—" : (Number(match.rule_score)*100).toFixed(1)+"%"} (not a probability). No AI rationale is used for this result.</div><div class="check-grid"><div class="check-item"><span>Amount delta</span><strong>${Number(match.features.amount_delta_cents).toLocaleString()} cents</strong></div><div class="check-item"><span>Date difference</span><strong>${Number(match.features.date_delta_days)} days</strong></div><div class="check-item"><span>Descriptor similarity</span><strong>${Math.round(Number(match.features.descriptor_similarity || 0)*100)}%</strong></div><div class="check-item"><span>Reference</span><strong>${match.features.reference_equal ? "Equal" : "Different or missing"}</strong></div></div><div class="detail-grid">${records("bank")}${records("ledger")}</div><p class="muted">This application stores deterministic match results but does not currently support post-run approve/reject decisions.</p>`;
    showPage("match-detail");
  } catch (error) { toast(error.message, "error"); }
}

async function loadActivity(target, limit) {
  const root = $(target);
  try {
    const result = await request(`/api/reconciliation/audit-logs?limit=${limit}`);
    const logs = result.audit_logs || [];
    if (!logs.length) { root.innerHTML = emptyState("No recorded activity", "Actions will appear here after you create a run or work with an import."); return; }
    root.innerHTML = `<table><thead><tr><th>Time</th><th>Run</th><th>Action</th><th>Details</th></tr></thead><tbody>${logs.map(log => `<tr><td class="nowrap">${escapeHtml(log.timestamp)}</td><td class="mono">${escapeHtml(log.run_id)}</td><td>${escapeHtml(log.action || log.tool_name)}</td><td>${escapeHtml(log.details_json || log.output_json)}</td></tr>`).join("")}</tbody></table>`;
  } catch (error) { root.innerHTML = emptyState("Activity unavailable", error.message); }
}

async function loadAudit() { $("#audit-table").innerHTML = '<div class="loading">Loading audit trail…</div>'; await loadActivity("#audit-table", 100); }

async function loadTools() {
  const root = $("#tools-list"); root.innerHTML = '<div class="loading">Loading tool contracts…</div>';
  try {
    const data = await request("/api/reconciliation/tools");
    const tools = data.tools || [];
    root.innerHTML = tools.length ? tools.map(tool => `<article class="tool-row"><strong>${escapeHtml(tool.name)}</strong><p>${escapeHtml(tool.description)}</p><p class="muted">${tool.is_llm_callable ? "LLM inquiry scope" : "Deterministic application tool"}</p></article>`).join("") : emptyState("No tool metadata", "The API returned an empty tool registry.");
  } catch (error) { root.innerHTML = emptyState("Tool registry unavailable", error.message); }
}

function wireEvents() {
  $("#switch-to-signup").addEventListener("click", () => showAuthMode("signup"));
  $("#switch-to-login").addEventListener("click", () => showAuthMode("login"));

  $("#login-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget, button = $("#login-submit"), error = $("#login-error");
    error.hidden = true;
    const username = $("#login-username").value.trim(); const password = $("#login-password").value;
    if (!username || !password) { error.textContent = "Enter your username and password."; error.hidden = false; return; }
    setBusy(button, true, "Signing in…");
    try {
      const response = await fetch("/api/auth/login", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Unable to sign in.");
      state.user = data.user; state.csrf = data.csrf_token; form.reset(); showApplication(); await loadDashboard();
    } catch (e) { error.textContent = e.message; error.hidden = false; }
    finally { setBusy(button, false); }
  });

  $("#signup-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("#signup-submit");
    const error = $("#signup-error");
    error.hidden = true;
    if (!form.reportValidity()) return;
    const password = $("#signup-password").value;
    if (password !== $("#signup-confirm-password").value) {
      error.textContent = "The passwords do not match.";
      error.hidden = false;
      $("#signup-confirm-password").focus();
      return;
    }
    setBusy(button, true, "Creating account…");
    try {
      const data = await request("/api/auth/register", {
        method: "POST",
        body: {
          username: $("#signup-username").value.trim(),
          email: $("#signup-email").value.trim(),
          password
        }
      });
      state.user = data.user;
      state.csrf = data.csrf_token;
      form.reset();
      showApplication();
      await loadDashboard();
      toast("Your analyst account is ready.");
    } catch (e) {
      error.textContent = e.message;
      error.hidden = false;
    } finally {
      setBusy(button, false);
    }
  });

  $("#logout-button").addEventListener("click", async () => {
    try { await request("/api/auth/logout", { method: "POST", body: {} }); }
    catch (error) { toast(error.message, "error"); }
    state.user = null; state.csrf = ""; state.currentRun = null; showLogin();
  });
  $("#run-form").addEventListener("submit", createRun);
  $("#audit-refresh").addEventListener("click", loadAudit);
  $("#import-refresh").addEventListener("click", loadImportWorkspace);
  $("#import-run-select").addEventListener("change", event => { state.importRunId = event.target.value; openRunForImports(state.importRunId); });
  $("#import-side").addEventListener("change", () => openRunForImports(state.importRunId));
  $("#worklist-run-select").addEventListener("change", () => { state.worklistPage = 1; queryWorklist(); });
  $("#worklist-type").addEventListener("change", () => { state.worklistPage = 1; queryWorklist(); });
  $("#worklist-search-button").addEventListener("click", () => { state.worklistPage = 1; queryWorklist(); });
  $("#worklist-search").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); state.worklistPage = 1; queryWorklist(); } });
  $("#worklist-status").addEventListener("change", () => { state.worklistPage = 1; queryWorklist(); });
  $("#worklist-prev").addEventListener("click", () => { state.worklistPage = Math.max(1, state.worklistPage - 1); queryWorklist(); });
  $("#worklist-next").addEventListener("click", () => { state.worklistPage += 1; queryWorklist(); });
  $("#run-worklist").addEventListener("change", event => {
    if (["run-results-type", "run-results-status"].includes(event.target.id)) { state.runResultsPage = 1; queryRunResults(); }
  });
  $("#run-worklist").addEventListener("keydown", event => {
    if (event.target.id === "run-results-search" && event.key === "Enter") { event.preventDefault(); state.runResultsPage = 1; queryRunResults(); }
  });
  $("#run-worklist").addEventListener("click", event => {
    if (event.target.closest("[data-run-results-search]")) { state.runResultsPage = 1; queryRunResults(); }
    const pageButton = event.target.closest("[data-run-results-page]");
    if (pageButton) { state.runResultsPage = Math.max(1, state.runResultsPage + (pageButton.dataset.runResultsPage === "next" ? 1 : -1)); queryRunResults(); }
  });

  document.addEventListener("click", async event => {
    const generateAi = event.target.closest("[data-generate-ai]"); if (generateAi) { await generateAiInsights(generateAi); return; }
    const pageButton = event.target.closest("[data-page]");
    if (pageButton) { navTo(pageButton.dataset.page); return; }
    const action = event.target.closest("[data-action]");
    if (action?.dataset.action === "upload-documents") { await startUploadFlow(); return; }
    if (action?.dataset.action === "new-run") { beginNewRunFlow(false); return; }
    if (action?.dataset.action === "sample-reconciliation") { await openSampleReconciliation(action); return; }
    const open = event.target.closest("[data-open-run]"); if (open) { await openRun(open.dataset.openRun); return; }
    const tab = event.target.closest("[data-run-tab]"); if (tab) { await renderRunTab(tab.dataset.runTab); return; }
    const review = event.target.closest("[data-review-batch]"); if (review) { await openBatchReview(review.dataset.reviewBatch); return; }
    const confirm = event.target.closest("[data-confirm-batch]"); if (confirm) { await confirmBatch(confirm.dataset.confirmBatch); return; }
    const confirmRun = event.target.closest("[data-confirm-run-imports]"); if (confirmRun) { await confirmRunImports(confirmRun.dataset.confirmRunImports); return; }
    const backToRun = event.target.closest("[data-return-run-imports]"); if (backToRun && state.currentRun) { await openRun(state.currentRun.id); await renderRunTab("imports"); return; }
    const summaryTarget = event.target.closest("[data-summary-target]"); if (summaryTarget) {
      const selector = $("#run-results-type");
      if (selector) { selector.value = summaryTarget.dataset.summaryTarget; state.runResultsPage = 1; queryRunResults(); }
      return;
    }
    const discard = event.target.closest("[data-discard-batch]"); if (discard) { await discardBatch(discard.dataset.discardBatch); return; }
    const save = event.target.closest("[data-save-row]"); if (save) { await saveRow(save); return; }
    const rowAction = event.target.closest("[data-row-action]"); if (rowAction) { await updateRowStatus(rowAction); return; }
    const match = event.target.closest("[data-open-match]"); if (match) { await openMatch(match.dataset.run, match.dataset.openMatch); return; }
    const runAction = event.target.closest("[data-run-action=reconcile]"); if (runAction) { await reconcileRun(); return; }
  });

  document.addEventListener("submit", event => {
    const aiQuestion = event.target.closest("[data-ai-question]");
    if (aiQuestion) { event.preventDefault(); askRunQuestion(aiQuestion); return; }
    const upload = event.target.closest("[data-upload-form]");
    if (upload) { event.preventDefault(); handleUpload(upload); return; }
    const mapping = event.target.closest("[data-map-batch]");
    if (mapping) { event.preventDefault(); mapBatch(mapping); }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  wireEvents();
  restoreSession();
});
