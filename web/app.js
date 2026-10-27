// ==========================================================================
// Veritas Treasury • Autonomous Two-Sided Financial Reconciliation Suite
// Emerald Edition Design System Controller & Human Auditor Workstation
// ==========================================================================

let currentFilter = "all";
let currentSearch = "";
let currentSplit = "dev";
let isSyncing = false;

document.addEventListener("DOMContentLoaded", () => {
  initLiveClock();
  initTabs();
  initToolbarControls();
  
  // Initial data load
  refreshAllData();
});

// --------------------------------------------------------------------------
// 1. Strict Security: Text Escaping (Prevents XSS Injection)
// --------------------------------------------------------------------------
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// --------------------------------------------------------------------------
// 2. Live System Clock
// --------------------------------------------------------------------------
function initLiveClock() {
  const clockEl = document.getElementById("live-clock");
  if (!clockEl) return;
  function update() {
    const now = new Date();
    const timeStr = now.toTimeString().split(" ")[0] + " UTC";
    clockEl.textContent = timeStr;
  }
  update();
  setInterval(update, 1000);
}

// --------------------------------------------------------------------------
// 3. Navigation Tabs
// --------------------------------------------------------------------------
function initTabs() {
  const tabBtns = document.querySelectorAll(".tab-button");
  const tabPanels = document.querySelectorAll(".tab-content");

  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.getAttribute("data-tab");
      tabBtns.forEach((b) => {
        b.classList.remove("active");
        b.setAttribute("aria-selected", "false");
      });
      tabPanels.forEach((p) => p.classList.remove("active"));

      btn.classList.add("active");
      btn.setAttribute("aria-selected", "true");

      const activePanel = document.getElementById(`tab-${target}`);
      if (activePanel) {
        activePanel.classList.add("active");
        // Refresh specific tab data on activation
        if (target === "reconcile") loadRecords();
        if (target === "queue") loadReviewQueue();
        if (target === "audit") loadAuditLogs();
        if (target === "tools") loadTools();
      }
    });
  });
}

// --------------------------------------------------------------------------
// 4. Toolbar & Filter Controls
// --------------------------------------------------------------------------
function initToolbarControls() {
  const splitSelect = document.getElementById("split-select");
  const btnReloadSplit = document.getElementById("btn-reload-split");
  const searchInput = document.getElementById("reconcile-search");
  const filterBtns = document.querySelectorAll(".filter-chip-btn");
  const btnRefreshAudit = document.getElementById("btn-refresh-audit");

  if (splitSelect) {
    splitSelect.addEventListener("change", (e) => {
      currentSplit = e.target.value;
    });
  }

  if (btnReloadSplit) {
    btnReloadSplit.addEventListener("click", async () => {
      if (isSyncing) return;
      btnReloadSplit.disabled = true;
      btnReloadSplit.innerHTML = `<span>Loading ${currentSplit}...</span>`;
      try {
        const res = await fetch("/api/reconciliation/load-split", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ split: currentSplit })
        });
        const data = await res.json();
        if (res.ok) {
          showNotification(`Successfully loaded ${currentSplit.toUpperCase()} split (${data.bank_records_count} bank, ${data.ledger_records_count} ledger records)`, "success");
          await refreshAllData();
        } else {
          showNotification(data.error || "Failed to load split", "error");
        }
      } catch (err) {
        showNotification("Network error loading split", "error");
      } finally {
        btnReloadSplit.disabled = false;
        btnReloadSplit.innerHTML = `<span>Reload Split</span>`;
      }
    });
  }

  if (searchInput) {
    let debounceTimer;
    searchInput.addEventListener("input", (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        currentSearch = e.target.value.trim();
        loadRecords();
      }, 250);
    });
  }

  filterBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      filterBtns.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentFilter = btn.getAttribute("data-filter");
      loadRecords();
    });
  });

  if (btnRefreshAudit) {
    btnRefreshAudit.addEventListener("click", loadAuditLogs);
  }
}

// --------------------------------------------------------------------------
// 5. Global Data Refresh
// --------------------------------------------------------------------------
async function refreshAllData() {
  await Promise.all([
    loadBalanceProof(),
    loadRecords(),
    loadReviewQueue(),
    loadAuditLogs(),
    loadTools()
  ]);
}

// --------------------------------------------------------------------------
// 6. Balance Proof Panel (Integer Cents Verification)
// --------------------------------------------------------------------------
async function loadBalanceProof() {
  try {
    const res = await fetch("/api/reconciliation/balance-proof");
    if (!res.ok) return;
    const proof = await res.json();

    const variancePill = document.getElementById("proof-variance-pill");
    const varianceLabel = document.getElementById("proof-variance-label");
    const statusTag = document.getElementById("proof-status-tag");

    const diffFormatted = proof.difference_formatted || "0.00";
    const isBalanced = proof.is_balanced === true;

    if (varianceLabel) {
      varianceLabel.textContent = `Difference: $${diffFormatted}`;
    }

    if (variancePill) {
      variancePill.className = `proof-variance-pill ${isBalanced ? "balanced" : "unbalanced"}`;
    }

    if (statusTag) {
      statusTag.textContent = isBalanced ? "Consistent" : "Inconsistent";
      statusTag.style.color = isBalanced ? "#34d399" : "#f43f5e";
    }

    // Metric line items
    const elEndingBank = document.getElementById("proof-ending-bank");
    const elEndingBook = document.getElementById("proof-ending-book");
    const elUnmatchedLedger = document.getElementById("proof-unmatched-ledger");
    const elUnmatchedBank = document.getElementById("proof-unmatched-bank");
    const elFees = document.getElementById("proof-fees");
    const elAdjParity = document.getElementById("proof-adjusted-parity");

    const elUnmatchedLedgerCount = document.getElementById("proof-unmatched-ledger-count");
    const elUnmatchedBankCount = document.getElementById("proof-unmatched-bank-count");

    if (elEndingBank) elEndingBank.textContent = `$${proof.ending_bank_balance_formatted || "0.00"}`;
    if (elEndingBook) elEndingBook.textContent = `$${proof.ending_book_balance_formatted || "0.00"}`;
    if (elUnmatchedLedger) elUnmatchedLedger.textContent = `$${proof.unmatched_ledger_formatted || "0.00"}`;
    if (elUnmatchedBank) elUnmatchedBank.textContent = `$${proof.unmatched_bank_formatted || "0.00"}`;
    if (elFees) elFees.textContent = `$${proof.fee_adjustments_formatted || "0.00"}`;
    if (elAdjParity) elAdjParity.textContent = `$${proof.adjusted_bank_balance_formatted || "0.00"}`;

    if (elUnmatchedLedgerCount) elUnmatchedLedgerCount.textContent = proof.unmatched_ledger_records_count || 0;
    if (elUnmatchedBankCount) elUnmatchedBankCount.textContent = proof.unmatched_bank_records_count || 0;

  } catch (err) {
    console.error("Failed to load balance proof:", err);
  }
}

// --------------------------------------------------------------------------
// 7. Side-by-Side Bank vs Ledger View with Matched Groups
// --------------------------------------------------------------------------
async function loadRecords() {
  const bankTbody = document.getElementById("bank-table-body");
  const ledgerTbody = document.getElementById("ledger-table-body");
  const countBankEl = document.getElementById("count-bank");
  const countLedgerEl = document.getElementById("count-ledger");
  const countMatchesEl = document.getElementById("count-matches");
  const bankLoadedCount = document.getElementById("bank-loaded-count");
  const ledgerLoadedCount = document.getElementById("ledger-loaded-count");

  try {
    const params = new URLSearchParams({
      status: currentFilter,
      search: currentSearch,
      limit: "500"
    });

    const res = await fetch(`/api/reconciliation/records?${params.toString()}`);
    if (!res.ok) return;
    const data = await res.json();

    const bankRecords = data.bank_records || [];
    const ledgerRecords = data.ledger_records || [];

    if (countBankEl) countBankEl.textContent = data.total_bank || bankRecords.length;
    if (countLedgerEl) countLedgerEl.textContent = data.total_ledger || ledgerRecords.length;
    if (countMatchesEl) countMatchesEl.textContent = data.matches_count || 0;
    if (bankLoadedCount) bankLoadedCount.textContent = `${bankRecords.length} records`;
    if (ledgerLoadedCount) ledgerLoadedCount.textContent = `${ledgerRecords.length} records`;

    // Render Bank Side
    if (bankRecords.length === 0) {
      bankTbody.innerHTML = `<tr><td colspan="6" style="text-align: center; padding: 32px; color: var(--text-muted);">No bank records match filter criteria.</td></tr>`;
    } else {
      let bHtml = "";
      for (const r of bankRecords) {
        const matchId = r.match_id;
        const matchStatus = r.match_status || "unmatched";
        let chipClass = "unmatched";
        let chipLabel = "Unmatched";

        if (matchId) {
          if (matchStatus === "approved" || matchStatus === "matched") {
            chipClass = "";
            chipLabel = matchId.replace("MATCH-B1-", "M-").replace("MATCH-", "M-");
          } else if (matchStatus === "pending_review") {
            chipClass = "pending";
            chipLabel = `${matchId.replace("MATCH-B1-", "M-").replace("MATCH-", "M-")} (Review)`;
          } else if (matchStatus === "rejected") {
            chipClass = "unmatched";
            chipLabel = "Rejected";
          }
        }

        bHtml += `
          <tr data-record-id="${escapeHtml(r.id)}" data-match-id="${escapeHtml(matchId || '')}">
            <td>
              <span class="match-group-chip ${chipClass}" data-match-id="${escapeHtml(matchId || '')}" title="${escapeHtml(matchId || 'Unmatched')}">
                ${escapeHtml(chipLabel)}
              </span>
            </td>
            <td><strong style="color: var(--text-primary); font-family: var(--font-mono);">${escapeHtml(r.id)}</strong></td>
            <td><span style="font-family: var(--font-mono); color: var(--text-muted);">${escapeHtml(r.date)}</span></td>
            <td><span style="font-family: var(--font-mono); font-weight: 700; color: #34d399;">$${escapeHtml(r.amount_formatted)}</span></td>
            <td><span class="descriptor-text" title="${escapeHtml(r.description)}">${escapeHtml(r.description)}</span></td>
            <td><span style="font-family: var(--font-mono); font-size: 0.72rem; color: var(--text-muted);">${escapeHtml(r.reference || '—')}</span></td>
          </tr>
        `;
      }
      bankTbody.innerHTML = bHtml;
    }

    // Render Ledger Side
    if (ledgerRecords.length === 0) {
      ledgerTbody.innerHTML = `<tr><td colspan="6" style="text-align: center; padding: 32px; color: var(--text-muted);">No ledger records match filter criteria.</td></tr>`;
    } else {
      let lHtml = "";
      for (const r of ledgerRecords) {
        const matchId = r.match_id;
        const matchStatus = r.match_status || "unmatched";
        let chipClass = "unmatched";
        let chipLabel = "Unmatched";

        if (matchId) {
          if (matchStatus === "approved" || matchStatus === "matched") {
            chipClass = "";
            chipLabel = matchId.replace("MATCH-B1-", "M-").replace("MATCH-", "M-");
          } else if (matchStatus === "pending_review") {
            chipClass = "pending";
            chipLabel = `${matchId.replace("MATCH-B1-", "M-").replace("MATCH-", "M-")} (Review)`;
          } else if (matchStatus === "rejected") {
            chipClass = "unmatched";
            chipLabel = "Rejected";
          }
        }

        lHtml += `
          <tr data-record-id="${escapeHtml(r.id)}" data-match-id="${escapeHtml(matchId || '')}">
            <td>
              <span class="match-group-chip ${chipClass}" data-match-id="${escapeHtml(matchId || '')}" title="${escapeHtml(matchId || 'Unmatched')}">
                ${escapeHtml(chipLabel)}
              </span>
            </td>
            <td><strong style="color: var(--text-primary); font-family: var(--font-mono);">${escapeHtml(r.id)}</strong></td>
            <td><span style="font-family: var(--font-mono); color: var(--text-muted);">${escapeHtml(r.date)}</span></td>
            <td><span style="font-family: var(--font-mono); font-weight: 700; color: #60a5fa;">$${escapeHtml(r.amount_formatted)}</span></td>
            <td><span class="descriptor-text" title="${escapeHtml(r.description)}">${escapeHtml(r.description)}</span></td>
            <td><span style="font-family: var(--font-mono); font-size: 0.72rem; color: var(--text-muted);">${escapeHtml(r.reference || '—')}</span></td>
          </tr>
        `;
      }
      ledgerTbody.innerHTML = lHtml;
    }

    // Attach synchronized match group hover listeners
    attachMatchGroupHoverListeners();

  } catch (err) {
    console.error("Failed to load records:", err);
  }
}

// --------------------------------------------------------------------------
// 8. Match Group Synchronized Highlighting
// --------------------------------------------------------------------------
function attachMatchGroupHoverListeners() {
  const chips = document.querySelectorAll(".match-group-chip");
  chips.forEach((chip) => {
    const mid = chip.getAttribute("data-match-id");
    if (!mid) return;

    chip.addEventListener("mouseenter", () => {
      document.querySelectorAll(`tr[data-match-id="${mid}"]`).forEach((row) => {
        row.classList.add("highlight-group");
      });
    });

    chip.addEventListener("mouseleave", () => {
      document.querySelectorAll(`tr[data-match-id="${mid}"]`).forEach((row) => {
        row.classList.remove("highlight-group");
      });
    });
  });
}

// --------------------------------------------------------------------------
// 9. Review Queue (Candidates, Deterministic Features, Unverified AI Rationale)
// --------------------------------------------------------------------------
async function loadReviewQueue() {
  const container = document.getElementById("review-cards-list");
  const countBadge = document.getElementById("queue-count-badge");
  const kpiPending = document.getElementById("kpi-queue-pending");

  try {
    const res = await fetch("/api/reconciliation/review-queue");
    if (!res.ok) return;
    const data = await res.json();
    const items = data.items || [];

    if (countBadge) {
      countBadge.textContent = items.length;
      countBadge.style.display = items.length > 0 ? "inline-block" : "none";
    }

    if (kpiPending) {
      kpiPending.textContent = items.length;
    }

    if (items.length === 0) {
      container.innerHTML = `
        <div class="solid-card glass-card" style="text-align: center; padding: 48px 24px;">
          <div style="font-size: 2.2rem; margin-bottom: 12px; color: #34d399;">✓</div>
          <div style="font-family: var(--font-heading); font-size: 1.15rem; font-weight: 700; margin-bottom: 6px;">
            Review Queue Resolved
          </div>
          <div style="color: var(--text-secondary); max-width: 480px; margin: 0 auto; line-height: 1.6;">
            All escalated and low-confidence items have been adjudicated. Arithmetic consistency difference currently reads 
            <strong style="color: #34d399;">$0.00</strong>.
          </div>
        </div>
      `;
      return;
    }

    let cardsHtml = "";
    items.forEach((item, index) => {
      const target = item.target_item;
      const proposed = item.proposed_candidate;
      const candidates = item.candidates || [proposed];
      const features = item.deterministic_features || {};
      const matchId = item.match_id || "";
      const reviewId = item.review_id || `ITEM-${index}`;

      const deltaFormatted = features.amount_delta_formatted || "0.00";
      const dateDrift = features.date_diff_days !== undefined ? `${features.date_diff_days} days` : "0 days";
      const simRatio = features.similarity_pct !== undefined ? `${features.similarity_pct}%` : "0%";
      const refMatchStr = features.reference_match ? "Matched ✓" : "No Ref Match";

      // Build options for candidate reassign select
      let candOptionsHtml = "";
      candidates.forEach((c) => {
        const isSelected = c.id === proposed.id ? "selected" : "";
        candOptionsHtml += `<option value="${escapeHtml(c.id)}" ${isSelected}>${escapeHtml(c.id)} • $${escapeHtml(c.amount_formatted)} • ${escapeHtml(c.date)}</option>`;
      });

      cardsHtml += `
        <div class="review-card" id="card-${escapeHtml(reviewId)}" data-target-id="${escapeHtml(target.id)}" data-candidate-id="${escapeHtml(proposed.id)}" data-match-id="${escapeHtml(matchId)}">
          
          <!-- Card Header -->
          <div class="review-card-header">
            <div class="review-header-left">
              <span class="review-id-title">${escapeHtml(target.id)} ⇄ ${escapeHtml(proposed.id)}</span>
              <span class="review-flag-badge">${escapeHtml(item.flag_reason || "Adjudication Required")}</span>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
              <span style="font-size: 0.76rem; font-family: var(--font-mono); color: var(--text-muted);">CONFIDENCE:</span>
              <span style="font-family: var(--font-mono); font-weight: 700; color: #fbbf24;">${item.confidence !== null && item.confidence !== undefined ? `${(item.confidence * 100).toFixed(1)}%` : "n/a"}</span>
            </div>
          </div>

          <!-- Comparison Grid -->
          <div class="comparison-grid">
            
            <!-- Target Record -->
            <div class="comparison-box target-box">
              <div class="comparison-role-lbl" style="color: #60a5fa;">
                <span>TARGET ITEM (${escapeHtml(target.side.toUpperCase())})</span>
                <span style="font-family: var(--font-mono);">${escapeHtml(target.id)}</span>
              </div>
              <div class="comparison-amount">$${escapeHtml(target.amount_formatted)}</div>
              <div class="comparison-desc">${escapeHtml(target.description)}</div>
              <div class="comparison-meta">
                <span>Date: <strong>${escapeHtml(target.date)}</strong></span>
                <span>Ref: <strong>${escapeHtml(target.reference || 'None')}</strong></span>
              </div>
            </div>

            <!-- Proposed Candidate -->
            <div class="comparison-box candidate-box">
              <div class="comparison-role-lbl" style="color: #34d399;">
                <span>PROPOSED CANDIDATE (${escapeHtml(proposed.side.toUpperCase())})</span>
                <span style="font-family: var(--font-mono);">${escapeHtml(proposed.id)}</span>
              </div>
              <div class="comparison-amount">$${escapeHtml(proposed.amount_formatted)}</div>
              <div class="comparison-desc">${escapeHtml(proposed.description)}</div>
              <div class="comparison-meta">
                <span>Date: <strong>${escapeHtml(proposed.date)}</strong></span>
                <span>Ref: <strong>${escapeHtml(proposed.reference || 'None')}</strong></span>
              </div>
            </div>

          </div>

          <!-- Deterministic Features Box (Code-Computed) -->
          <div class="deterministic-features-box">
            <div class="features-box-header">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
              <span>Deterministic Kernel Metrics (Code-Computed)</span>
            </div>
            <div class="features-grid">
              <div class="feature-item">
                <span class="feature-lbl">Amount Delta</span>
                <span class="feature-val" style="color: ${features.is_exact_amount ? '#34d399' : '#fbbf24'};">$${escapeHtml(deltaFormatted)}</span>
              </div>
              <div class="feature-item">
                <span class="feature-lbl">Date Drift</span>
                <span class="feature-val">${escapeHtml(dateDrift)}</span>
              </div>
              <div class="feature-item">
                <span class="feature-lbl">Descriptor Similarity</span>
                <span class="feature-val">${escapeHtml(simRatio)}</span>
              </div>
              <div class="feature-item">
                <span class="feature-lbl">Reference Match</span>
                <span class="feature-val">${escapeHtml(refMatchStr)}</span>
              </div>
            </div>
          </div>

          <!-- VISUALLY DISTINCT: AI RATIONALE (UNVERIFIED) BLOCK -->
          <div class="unverified-rationale-box">
            <div class="unverified-header">
              <div class="unverified-title-group">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fbbf24" stroke-width="2.2"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                <span class="unverified-badge">AI rationale (unverified)</span>
              </div>
              <span style="font-size: 0.72rem; color: #fbbf24; font-weight: 600;">Advisory LLM Output • Unverified by Code</span>
            </div>
            <div class="unverified-text">
              "${escapeHtml(item.ai_rationale)}"
            </div>
            <div class="unverified-disclaimer">
              Notice: The LLM rationale is strictly non-authoritative and has zero computational authority over financial balances. All authoritative values are computed by deterministic code using integer cents.
            </div>
          </div>

          <!-- Actions & Persistence Bar -->
          <div class="review-actions-bar">
            <div class="review-inputs-group">
              <input type="text" class="reviewer-note-input" id="note-${escapeHtml(reviewId)}" placeholder="Optional auditor justification..." />
              <select class="reassign-select" id="select-cand-${escapeHtml(reviewId)}" title="Select alternative candidate for reassignment">
                ${candOptionsHtml}
              </select>
            </div>

            <div class="buttons-group">
              <button class="btn-confirm-match" onclick="handleConfirmMatch('${escapeHtml(target.id)}', '${escapeHtml(proposed.id)}', '${escapeHtml(matchId)}', '${escapeHtml(reviewId)}')">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                <span>Confirm</span>
              </button>

              <button class="btn-reject-match" onclick="handleRejectMatch('${escapeHtml(matchId)}', '${escapeHtml(target.id)}', '${escapeHtml(reviewId)}')">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                <span>Reject</span>
              </button>

              <button class="btn-reassign-match" onclick="handleReassignMatch('${escapeHtml(target.id)}', '${escapeHtml(matchId)}', '${escapeHtml(reviewId)}')">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M7 16V4m0 0L3 8m4-4 4 4m6 0v12m0 0 4-4m-4 4-4-4"/></svg>
                <span>Reassign</span>
              </button>
            </div>
          </div>

        </div>
      `;
    });

    container.innerHTML = cardsHtml;

  } catch (err) {
    console.error("Failed to load review queue:", err);
  }
}

// --------------------------------------------------------------------------
// 10. Confirm / Reject / Reassign Action Handlers
// --------------------------------------------------------------------------
window.handleConfirmMatch = async function(targetId, candidateId, matchId, reviewId) {
  const noteInput = document.getElementById(`note-${reviewId}`);
  const reviewerNote = noteInput ? noteInput.value.trim() : "";

  try {
    const res = await fetch("/api/reconciliation/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_id: targetId,
        candidate_id: candidateId,
        match_id: matchId || undefined,
        reviewer_note: reviewerNote || undefined
      })
    });

    const data = await res.json();
    if (res.ok) {
      showNotification(`Match confirmed successfully! Persisted to SQLite matches & audit_logs.`, "success");
      await Promise.all([
        loadBalanceProof(),
        loadReviewQueue(),
        loadRecords(),
        loadAuditLogs()
      ]);
    } else {
      showNotification(data.error || "Confirmation failed", "error");
    }
  } catch (err) {
    showNotification("Network error executing confirm action", "error");
  }
};

window.handleRejectMatch = async function(matchId, targetId, reviewId) {
  const noteInput = document.getElementById(`note-${reviewId}`);
  const reviewerNote = noteInput ? noteInput.value.trim() : "";

  try {
    const res = await fetch("/api/reconciliation/reject", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        match_id: matchId || undefined,
        target_id: targetId || undefined,
        reviewer_note: reviewerNote || undefined
      })
    });

    const data = await res.json();
    if (res.ok) {
      showNotification(`Match rejected. Item returned to review queue with status 'pending_review'.`, "info");
      await Promise.all([
        loadBalanceProof(),
        loadReviewQueue(),
        loadRecords(),
        loadAuditLogs()
      ]);
    } else {
      showNotification(data.error || "Rejection failed", "error");
    }
  } catch (err) {
    showNotification("Network error executing reject action", "error");
  }
};

window.handleReassignMatch = async function(targetId, matchId, reviewId) {
  const selectEl = document.getElementById(`select-cand-${reviewId}`);
  const noteInput = document.getElementById(`note-${reviewId}`);
  const newCandidateId = selectEl ? selectEl.value : "";
  const reviewerNote = noteInput ? noteInput.value.trim() : "";

  if (!newCandidateId) {
    showNotification("Please select an alternative candidate to reassign", "error");
    return;
  }

  try {
    const res = await fetch("/api/reconciliation/reassign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_id: targetId,
        new_candidate_id: newCandidateId,
        match_id: matchId || undefined,
        reviewer_note: reviewerNote || undefined
      })
    });

    const data = await res.json();
    if (res.ok) {
      showNotification(`Reassigned target ${targetId} to candidate ${newCandidateId}!`, "success");
      await Promise.all([
        loadBalanceProof(),
        loadReviewQueue(),
        loadRecords(),
        loadAuditLogs()
      ]);
    } else {
      showNotification(data.error || "Reassignment failed", "error");
    }
  } catch (err) {
    showNotification("Network error executing reassign action", "error");
  }
};

// --------------------------------------------------------------------------
// 11. Audit Trail Feed (SQLite audit_logs)
// --------------------------------------------------------------------------
async function loadAuditLogs() {
  const tbody = document.getElementById("audit-table-body");
  if (!tbody) return;

  try {
    const res = await fetch("/api/reconciliation/audit-logs?limit=50");
    if (!res.ok) return;
    const data = await res.json();
    const logs = data.audit_logs || [];

    if (logs.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; padding: 24px; color: var(--text-muted);">No audit logs recorded yet.</td></tr>`;
      return;
    }

    let html = "";
    logs.forEach((log) => {
      let argsPreview = log.arguments_json || "";
      if (argsPreview.length > 60) argsPreview = argsPreview.substring(0, 60) + "...";

      html += `
        <tr>
          <td><span style="font-family: var(--font-mono); color: var(--text-muted);">#${log.id}</span></td>
          <td><span style="font-family: var(--font-mono); font-size: 0.76rem;">${escapeHtml(log.timestamp)}</span></td>
          <td><strong style="font-family: var(--font-mono); color: var(--accent-primary-light);">${escapeHtml(log.run_id)}</strong></td>
          <td><span class="tag-badge ${log.tool_name.includes('confirm') ? 'success' : (log.tool_name.includes('reject') ? 'rose' : '')}">${escapeHtml(log.tool_name)}</span></td>
          <td><span style="font-size: 0.8rem; color: var(--text-secondary);">${escapeHtml(log.provider || 'deterministic')}</span></td>
          <td><span style="font-family: var(--font-mono); font-size: 0.74rem;">${escapeHtml(log.model || 'none')}</span></td>
          <td><span style="font-family: var(--font-mono);">${log.duration_ms !== undefined ? log.duration_ms.toFixed(1) : '0.0'}ms</span></td>
          <td><span style="font-family: var(--font-mono); font-size: 0.72rem; color: var(--text-muted); cursor: pointer;" title="${escapeHtml(log.arguments_json)}">${escapeHtml(argsPreview)}</span></td>
        </tr>
      `;
    });

    tbody.innerHTML = html;

  } catch (err) {
    console.error("Failed to load audit logs:", err);
  }
}

// --------------------------------------------------------------------------
// 12. 7-Tool Registry
// --------------------------------------------------------------------------
async function loadTools() {
  const grid = document.getElementById("tools-grid");
  if (!grid) return;

  try {
    const res = await fetch("/api/reconciliation/tools");
    if (!res.ok) return;
    const data = await res.json();
    const tools = data.tools || [];

    let html = "";
    tools.forEach((t) => {
      const isLLM = t.is_llm_callable;
      let paramsList = "";
      (t.parameters || []).forEach((p) => {
        paramsList += `
          <div style="font-size: 0.74rem; font-family: var(--font-mono); margin-bottom: 4px;">
            <strong style="color: var(--accent-primary-light);">${escapeHtml(p.name)}</strong>: 
            <span style="color: var(--text-muted);">${escapeHtml(p.type)}</span> 
            ${p.required ? '<span style="color: var(--accent-warning); font-size: 0.68rem;">[req]</span>' : ''}
          </div>
        `;
      });

      html += `
        <div class="tool-card glass-card">
          <div class="tool-card-header">
            <span class="tool-name">${escapeHtml(t.name)}</span>
            <span class="tag-badge ${isLLM ? 'warning' : 'success'}">${escapeHtml(t.category)}</span>
          </div>
          <p class="tool-desc">${escapeHtml(t.description)}</p>
          <div style="margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--border-glass);">
            <div style="font-size: 0.7rem; font-weight: 700; text-transform: uppercase; color: var(--text-muted); margin-bottom: 6px;">Parameters:</div>
            ${paramsList}
          </div>
          <div style="margin-top: 10px; font-size: 0.7rem; color: ${isLLM ? '#fbbf24' : '#34d399'};">
            ${isLLM ? '• Exposed to LLM (isolated inquiry scope)' : '• Deterministic Kernel only (LLM cannot call)'}
          </div>
        </div>
      `;
    });

    grid.innerHTML = html;

  } catch (err) {
    console.error("Failed to load tools:", err);
  }
}

// --------------------------------------------------------------------------
// 13. Visual Notifications / Toast
// --------------------------------------------------------------------------
function showNotification(msg, type = "info") {
  const existing = document.getElementById("app-notification-toast");
  if (existing) existing.remove();

  const toast = document.createElement("div");
  toast.id = "app-notification-toast";
  toast.style.position = "fixed";
  toast.style.bottom = "24px";
  toast.style.right = "24px";
  toast.style.zIndex = "9999";
  toast.style.padding = "12px 20px";
  toast.style.borderRadius = "8px";
  toast.style.fontFamily = "var(--font-sans)";
  toast.style.fontSize = "0.85rem";
  toast.style.fontWeight = "600";
  toast.style.boxShadow = "0 10px 30px rgba(0,0,0,0.5)";
  toast.style.display = "flex";
  toast.style.alignItems = "center";
  toast.style.gap = "10px";
  toast.style.transition = "all 0.3s ease";

  if (type === "success") {
    toast.style.background = "#064e3b";
    toast.style.border = "1px solid #10b981";
    toast.style.color = "#a7f3d0";
  } else if (type === "error") {
    toast.style.background = "#881337";
    toast.style.border = "1px solid #f43f5e";
    toast.style.color = "#fecdd3";
  } else {
    toast.style.background = "#1e293b";
    toast.style.border = "1px solid #64748b";
    toast.style.color = "#cbd5e1";
  }

  toast.textContent = msg;
  document.body.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(10px)";
    setTimeout(() => toast.remove(), 350);
  }, 4000);
}
