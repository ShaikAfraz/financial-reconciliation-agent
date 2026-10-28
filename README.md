# Financial Reconciliation Agent

A local financial operations web application for importing bank and ledger CSV files, validating their transactions, and comparing records with the existing deterministic reconciliation engine. The browser uses the authenticated server API and SQLite persistence; results are not supplied by hardcoded UI fixtures.

## Start the application

From the project root, run:

```sh
python3 server.py
```

Open [http://127.0.0.1:8080/](http://127.0.0.1:8080/). The server uses Python's standard library and initializes/upgrades the SQLite database on startup. To choose another port, set `PORT`, for example `PORT=8090 python3 server.py`.

## Account setup and login

1. Open the site and choose **Create an account** on the sign-in page.
2. Register an analyst account with a unique username, email address, and password of at least 12 characters.
3. New signups receive the analyst role. Administrator and auditor access is managed separately.
4. Sign in with the registered username and password. Sessions and authorization are handled by the server.

## Create a reconciliation and import files

1. From **Overview** or **Reconciliations**, choose **New reconciliation**.
2. Enter a short run name and select **Create run**. No opening or closing balances are requested.
3. On that run's **Imports** tab, select a bank statement CSV and an internal ledger CSV. The two upload areas are shown together.
4. The application detects familiar column names and supported date formats. Open each **Review bank/ledger import** to check the detected mapping, row counts, validation status, and first five parsed rows. Use the optional column mapping only if a required field or format cannot be detected safely.
5. Select **Confirm Imports** after both files are reviewed and validation blockers are resolved.
6. Select **Run Reconciliation**. Open the **Results** tab to inspect persisted matches, unmatched records, review items, and amount differences. Select a match ID to view the underlying bank and ledger transactions.

The sample CSV files can be downloaded from the new-run screen or loaded directly from:

- `web/sample_data/bank_statement.csv`
- `web/sample_data/ledger_statement.csv`

## Supported CSV columns and formats

- Files must be UTF-8 CSV, at most 2 MB, with no more than 20 columns and 3,000 data rows. Comma and semicolon delimiters are supported; the upload offers an explicit delimiter choice when needed.
- Required transaction fields are date, description, and amount. The application recognizes common headers such as `Date`/`Transaction Date`, `Description`/`Narration`/`Particulars`, and `Amount`/`Transaction Amount`.
- Amounts may use one signed amount column or separate `Debit` and `Credit` columns. Signed negative values represent an outflow from cash. The ledger sign convention is requested only for debit/credit columns where direction cannot be inferred from one signed amount.
- Optional reference columns include `Reference`, `Ref`, `Transaction ID`, or equivalent common names. An optional currency/code column is recognized. INR is supported, including values marked with `₹`.
- ISO dates (`YYYY-MM-DD`) are detected automatically. The mapping screen also supports `DD/MM/YYYY` and `MM/DD/YYYY`; ambiguous dates require an explicit choice.
- Numeric values are parsed into integer paise/cents to avoid floating-point arithmetic. The app currently stores two decimal places for all configured currencies, including JPY.

## Results and current limits

- Match types, matches, exceptions, and summary amounts are read from records persisted in SQLite by the reconciliation API.
- **Matched** means the engine stored a transaction pair. **Unmatched** means a source record has no accepted pair. **Review required** means an exception remains open for a person to inspect.
- Net activity difference is the bank transaction total minus the ledger transaction total. Matched amount differences are the sum of per-pair amount deltas. Neither is an account balance proof.
- This workflow does not collect or verify opening/closing statement balances. A zero net difference does not prove that all transactions are reconciled, and the matching engine may leave unmatched or review-required records.
- Matching is deterministic and heuristic. Rule scores are not probabilities. Generative AI is not used to compute the authoritative match or amount results. A configured adjudication integration may provide a separate non-authoritative suggestion for ambiguous cases.
- Large files and non-UTF-8 files are rejected. CSV import only; spreadsheet (`.xlsx`) and PDF statements are not supported.

## Optional Gemini investigation and run assistant

The deterministic reconciliation engine remains authoritative. On a completed run, the Results tab offers **Generate AI insights** and **Ask about this run**. These controls make explicit server-side requests to Google Gemini using the `google-genai` SDK; reconciliation itself does not make an AI call.

Configure credentials in the server process environment, then restart the server:

```sh
export GEMINI_API_KEY='your-Gemini-API-key'
export GEMINI_MODEL='gemini-2.5-flash'  # optional; defaults to the model pinned in reconcile/config.json
python3 server.py
```

The API key is read only by Python on the server and is never sent to browser JavaScript. AI requests are bounded and sent only when an authenticated user explicitly asks. The payload omits account identifiers and source references and contains run-scoped transaction IDs, dates, integer-cent amounts, short descriptions, and deterministic result evidence. Transaction descriptions are treated as untrusted data. The model cannot execute SQL or mutate reconciliation records. Its proposed matches and explanations are hypotheses that require human review; structured record citations are checked against the persisted run before they are saved. The downloadable Markdown report uses persisted reconciliation results and only validated AI explanations.

If `GEMINI_API_KEY` is absent or Gemini fails, the UI identifies a **Rule-based fallback (not AI)** and reports stored counts only. It does not claim a model call happened. Gemini request errors are not logged with prompts, responses, or credentials. `RUN_LIVE_GEMINI_TEST=1` enables the opt-in live integration test when a valid key is configured; ordinary tests use stubbed model responses.

Current limits: Gemini suggestions cannot approve matches or post entries; numeric arithmetic remains deterministic; the assistant answers from a bounded subset of the selected run and may report that the requested detail is unavailable. Real model availability depends on the configured Google account, model access, quota, network, and API key.

## Tests

Run the current reconciliation and API tests:

```sh
python3 -m unittest discover tests
python3 -m unittest discover legacy/tests
```
