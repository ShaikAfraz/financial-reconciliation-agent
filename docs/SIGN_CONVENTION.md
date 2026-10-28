# Veritas Treasury • Accounting Sign Convention Specification

## 1. Core Principle: Cash Account Perspective
All monetary records in Veritas Treasury (`bank_records`, `ledger_records`, and `import_rows`) are normalized and stored strictly from the **Cash Account's Perspective**:

$$\text{Sign Rule: } \mathbf{Amount} > 0 \iff \text{Cash Inflow (Increase)}, \quad \mathbf{Amount} < 0 \iff \text{Cash Outflow (Decrease)}$$

Authoritative amounts are stored as **exact integer cents** (`amount_cents` `INTEGER`). Floating-point arithmetic is strictly prohibited across all data models and business logic.

---

## 2. Bank Statement Side Normalization

Bank statements reflect the bank's liability to the depositor:
- **Deposits / Credits**: Customer deposits increase cash $\to$ stored as **positive** ($+cents$).
- **Withdrawals / Debits / Checks / Fees**: Customer withdrawals decrease cash $\to$ stored as **negative** ($-cents$).

### Modes:
1. **Signed Mode (`signed`)**:
   - Values with explicit plus/positive are stored positive ($+cents$).
   - Values with leading minus, trailing minus (if enabled), or parentheses `(100.00)` (if enabled) are stored negative ($-cents$).
2. **Debit / Credit Columns (`debit_credit`)**:
   - **Credit Column**: Inflow $\to$ stored as positive ($+cents$).
   - **Debit Column**: Outflow $\to$ stored as negative ($-cents$).

---

## 3. General Ledger (Internal Book) Side Normalization

In double-entry corporate accounting, general ledger exports are prepared in one of two distinct conventions depending on the source accounting system:

1. **Cash Account Perspective (`cash_account`)** *(Standard GAAP/IFRS Asset Account)*:
   - Cash is an asset account.
   - **Debit**: Increases the asset (cash inflow) $\to$ stored as **positive** ($+cents$).
   - **Credit**: Decreases the asset (cash outflow) $\to$ stored as **negative** ($-cents$).
2. **Bank Perspective (`bank`)** *(Mirroring Bank Statement / Clearing Accounts)*:
   - Some treasury management and ERP systems export ledger clearing accounts mirrored to bank terminology.
   - **Credit**: Cash inflow $\to$ stored as **positive** ($+cents$).
   - **Debit**: Cash outflow $\to$ stored as **negative** ($-cents$).

> **Enforcement Rule**: A default perspective is **never assumed**. The user must explicitly specify `ledger_perspective: "cash_account"` or `"bank"` during the mapping phase for any ledger import batch. In `debit_credit` mode, this rule is applied deterministically.

---

## 4. Arithmetic Integrity & Balance Proof Formulas

For every side ($S \in \{\text{bank}, \text{ledger}\}$):

$$\text{Computed Closing Cents}_S = \text{Opening Cents}_S + \sum_{r \in \text{Confirmed Rows}_S} \text{Amount Cents}_r$$

$$\text{Integrity Difference Cents}_S = \text{Computed Closing Cents}_S - \text{User Entered Closing Cents}_S$$

A batch cannot be confirmed unless:
$$\text{Integrity Difference Cents}_S = 0$$
(except via audited administrator override).
