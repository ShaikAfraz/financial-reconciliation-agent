# Synthetic reconciliation sample

These CSVs are the clearly labeled sample run available from the Overview page's **Open sample reconciliation** action. The run is persisted for the signed-in user and is created at most once per user. The same files remain available to download for import practice.

- `bank_statement.csv`: 12 bank-side cash movements.
- `ledger_statement.csv`: 11 ledger-side cash movements.
- Currency: USD. Dates are ISO `YYYY-MM-DD`. Amounts use signed cash-account perspective (inflows positive, outflows negative).
- Bank net activity is `3350.37`; ledger net activity is `3352.87`.
- The files share 10 corresponding transactions, including a one-day date difference. Each has a side-specific adjustment, so the reconciliation produces matched and unmatched records.

The sample run uses the period `2026-10-01` through `2026-10-31`, maps `Date`, `Description`, and `Amount`, and reconciles both files without requiring opening or closing balances. The ledger sign convention is explicitly set to cash-account perspective for this synthetic sample.
