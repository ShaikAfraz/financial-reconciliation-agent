# Veritas Treasury • Interactive Demonstration & Verification Checklist
### Autonomous Two-Sided Financial Reconciliation & Human-in-the-Loop Audit Suite

This document provides a step-by-step manual click-through checklist for demonstrating the Veritas Treasury Two-Sided Financial Reconciliation Suite (`web/index.html`, `web/app.js`, `server.py`).

---

## 1. Quick Start & Server Launch

From the project root:
```bash
# Launch the web server on port 8080
python3 cli.py serve --port 8080
```
Open your browser to:
**`http://localhost:8080`**

---

## 2. Click-Through Verification Checklist

### Phase 1: Institutional Balance Proof Panel
- [ ] **Observe Permanent Top Banner:** Confirm the top balance proof banner is prominently visible across all tabs.
- [ ] **Verify Variance Readout:** Check the right-hand variance pill:
  - Must read in bold emerald green: **`Difference: $0.00`** with the badge **`[✓ FULLY BALANCED]`**.
- [ ] **Verify Line Items:** Confirm integer-cents line items are displayed:
  - **Ending Bank Balance** (e.g., `$22,860.40`)
  - **Ending Book Balance** (e.g., `$22,860.40`)
  - **In-Transit Deposits** (Unmatched Ledger Records count & amount)
  - **Unposted Bank Items** (Unmatched Bank Records count & amount)
  - **Processing Fees** (Fee differential adjustments)
  - **Adjusted Net Parity** (Adjusted Bank vs Book balance equality)
- [ ] **Verify Zero Floats Rule:** All numbers are formatted to 2 decimal places with deterministic integer-cents code (`core/money.py`); no scientific notation or IEEE 754 precision drift.

---

### Phase 2: Two-Sided Side-by-Side Reconciliation View
- [ ] **Default Active Tab:** Confirm Tab 1 **`◈ Two-Sided Reconciliation`** is active on load.
- [ ] **Left Column (Bank Statement Activity):**
  - Displays bank transactions loaded from `bank_records`.
  - Columns: `Match Group`, `Record ID`, `Date`, `Amount`, `Statement Descriptor`, `Reference`.
- [ ] **Right Column (General Ledger Activity):**
  - Displays accounting ledger transactions loaded from `ledger_records`.
  - Columns: `Match Group`, `Record ID`, `Date`, `Amount`, `Ledger Description`, `Reference`.
- [ ] **Synchronized Match Group Highlighting:**
  - Locate a match group pill (e.g., `M-0001` or `M-0012`).
  - Hover your cursor over the pill on the Bank side.
  - **Verification:** The corresponding matching record(s) on the General Ledger side automatically highlight with an illuminated emerald background (`.highlight-group`).
- [ ] **Real-Time Search Filtering:**
  - Type a merchant name (e.g., `AMZN` or `GOOGLE`) into the search input.
  - **Verification:** Both tables filter down instantaneously to matching rows without page reload.
  - Clear the search box to restore the full view.
- [ ] **Status Filter Chips:**
  - Click **`Matched`**: Shows only paired transactions.
  - Click **`Unmatched`**: Shows only single-sided in-transit or unposted items.
  - Click **`Pending Review`**: Shows items staged for human auditor adjudication.
  - Click **`All`**: Restores the complete view.
- [ ] **Dataset Split Switcher:**
  - Switch the dropdown from **Dev Split** to **Test Split** and click **Reload Split**.
  - **Verification:** Success toast appears; tables reload with the 230 bank / 237 ledger test dataset; balance proof updates and verifies net parity. Switch back to Dev Split.

---

### Phase 3: Review Queue & Unverified AI Rationale
- [ ] **Switch to Review Queue Tab:** Click Tab 2 **`⚡ Review Queue`**.
- [ ] **Badge Count:** Verify the navigation tab displays the pending item counter badge (e.g., `4`).
- [ ] **Inspect a Review Queue Card:**
  - **Card Header:** Displays target ID, proposed candidate ID, flag reason (e.g., `Low Confidence Fuzzy Candidate`), and confidence percentage.
  - **Comparison Grid:**
    - Left blue box: **Target Item** with ID, amount, date, descriptor, and reference.
    - Right green box: **Proposed Candidate** with ID, amount, date, description, and reference.
  - **Deterministic Kernel Metrics (Code-Computed Values):**
    - Amount Delta (e.g. `$0.00` or `+$6.07 fee`).
    - Date Drift (e.g. `2 days`).
    - Descriptor Similarity (e.g. `82.5%`).
    - Reference Match (e.g. `No Ref Match` or `Matched ✓`).
  - **Visually Distinct AI Rationale Box:**
    - Distinct amber/gold dashed border with subtle gradient glow.
    - Prominent label: **`AI rationale (unverified)`** with warning triangle icon.
    - Italicized LLM reasoning text explaining the candidate selection.
    - Bottom institutional disclaimer: *"Notice: The LLM rationale is strictly non-authoritative and has zero computational authority over financial balances. All authoritative values are computed by deterministic code using integer cents."*

---

### Phase 4: Confirm / Reject / Reassign Actions & Persistence
- [ ] **Test Confirm Action:**
  - In a review card, enter an auditor note in the text field: `"Verified vendor invoice and clearing timing."`
  - Click the green **`Confirm`** button.
  - **Verification:**
    - Success toast notification appears.
    - The item is removed from the pending queue.
    - Database verification: `matches.status` updates to `'approved'`, `reviews` records a `'confirm'` row, and `audit_logs` records a `'human_review_confirm'` entry.
    - The top balance proof remains at **`Difference: $0.00`**.
- [ ] **Test Reject Action (Item Returns to Queue):**
  - Locate another review item.
  - In the note input, type: `"Counterparty mismatch on closer inspection."`
  - Click the red **`Reject`** button.
  - **Verification:**
    - Info notification appears: *"Match rejected. Records returned to the review queue with status 'pending_review'."*
    - The tentative match is marked `'rejected'`.
    - The item's exception is updated/re-staged with `status = 'pending_review'`, returning it to the review queue as required by acceptance criteria.
    - Review row with decision `'reject'` and audit log entry `'human_review_reject'` are written to SQLite.
- [ ] **Test Reassign Action:**
  - In an active review card, use the candidate dropdown to select an alternative candidate record.
  - Click the blue **`Reassign`** button.
  - **Verification:**
    - Success notification appears.
    - Match member mapping updates to the newly selected candidate in SQLite.
    - Review row with decision `'reassign'` and audit log `'human_review_reassign'` are committed.

---

### Phase 5: Audit Trail & Tool Registry
- [ ] **Inspect Audit Trail Tab:**
  - Click Tab 3 **`📋 Audit Trail`**.
  - **Verification:** Table displays recent entries from SQLite `audit_logs`.
  - Locate the rows logged during your Confirm, Reject, and Reassign clicks with tool names `human_review_confirm`, `human_review_reject`, and `human_review_reassign`.
  - Confirm columns show exact timestamps, run IDs, duration in milliseconds, and JSON argument payloads.
- [ ] **Inspect Tool Registry Tab:**
  - Click Tab 4 **`⚙ Tool Registry`**.
  - **Verification:** Displays the 7 locked reconciliation tools:
    1. `ingest_records`
    2. `generate_candidates`
    3. `adjudicate_item`
    4. `validate_decision`
    5. `commit_match`
    6. `compute_balance_proof`
    7. `list_review_queue`
  - Confirm clear demarcation between deterministic kernel tools (LLM cannot call) and read-only inquiry tools.

---

### Phase 6: XSS Inertness Verification
- [ ] **Verify Description Escaping:**
  - All descriptors and text strings are escaped with `escapeHtml()` in `web/app.js`.
  - Any raw HTML or script payload (e.g., `<script>alert('XSS')</script>` or `<img src=x onerror=alert(1)>`) is strictly treated as inert character text and cannot execute in the browser DOM.
  - Validated automatically in `tests/test_web_api.py::test_xss_description_renders_inert`.

---

## 3. Automated Test Execution

To verify all programmatic guarantees:
```bash
# Run web API & state transition tests
python3 -m unittest tests/test_web_api.py

# Run complete 87-test suite
python3 -m unittest discover tests/

# Run legacy test suite
python3 -m unittest discover legacy/tests/
```
All 96 tests must pass cleanly with 0 failures and 0 errors.
