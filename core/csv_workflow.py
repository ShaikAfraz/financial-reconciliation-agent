"""
Veritas Treasury • CSV Transaction Workflow Engine
Handles raw CSV statement uploads, column mapping, accounting sign conventions,
strict numeric/date validation, duplicate flagging, review/editing,
atomic confirmation into authoritative records, statement checks,
deterministic run-scoped reconciliation, and CSV export.

Authoritative amounts are stored in integer cents.
No floats, no LLM calls, zero synthetic production data seeding.
"""

import os
import re
import io
import csv
import json
import time
import uuid
import hashlib
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set

from core.money import parse_to_cents, format_cents
from data.db import get_connection

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STAGING_DIR = Path(os.environ.get("CSV_STAGING_DIR", str(DATA_DIR / "staging")))
STAGING_DIR.mkdir(parents=True, exist_ok=True)

# Whitelist of allowed ISO 4217 currency codes
ALLOWED_CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "NZD", "SGD", "HKD", "INR"]

# Upload constraints
MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_DATA_ROWS = 3000
MAX_COLUMNS = 20
MAX_FIELD_LENGTH = 500

# Zero-width and control characters regex (excluding standard whitespace \t, \n, \r)
CONTROL_AND_ZERO_WIDTH_REGEX = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\ufeff]"
)

# Whitelisted columns for sorting/filtering rows
WHITELISTED_ROW_COLUMNS = {
    "row_number", "date_iso", "amount_cents", "description",
    "reference", "status", "edited", "currency"
}


# ----------------------------------------------------------------------
# 1. Audit Logging Helper
# ----------------------------------------------------------------------

def write_audit_log(
    conn,
    user_id: Optional[str],
    run_id: Optional[str],
    action: str,
    details: Dict[str, Any],
    duration_ms: Optional[int] = None
) -> None:
    """Appends an immutable audit log entry within the caller's transaction."""
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO audit_logs
        (user_id, run_id, step_index, tool_name, arguments_json, output_json,
         duration_ms, tokens, action, details_json)
    VALUES (?, ?, 0, ?, '{}', ?, ?, 0, ?, ?)
    """, (
        user_id, run_id, action, json.dumps(details, separators=(",", ":")),
        duration_ms, action, json.dumps(details, separators=(",", ":"))
    ))


# ----------------------------------------------------------------------
# 2. Text Normalization & Sanitization
# ----------------------------------------------------------------------

def sanitize_text(text: Optional[str], max_length: int) -> str:
    """
    Strips control and zero-width characters, collapses whitespace,
    and caps length at max_length. Does NOT modify formula injection prefixes.
    """
    if not text:
        return ""
    # Strip zero-width and control characters
    cleaned = CONTROL_AND_ZERO_WIDTH_REGEX.sub("", str(text))
    # Collapse whitespace
    cleaned = " ".join(cleaned.split())
    return cleaned[:max_length]


def normalize_description_for_dedup(text: Optional[str]) -> str:
    """Normalizes description for duplicate detection (lowercase, alphanumeric)."""
    if not text:
        return ""
    cleaned = sanitize_text(text, 200).lower()
    return re.sub(r"[^a-z0-9]", "", cleaned)


def escape_csv_cell(val: Any) -> str:
    """
    Formula injection prevention:
    Prefixes cells starting with =, +, -, @, \\t, \\r with a single quote.
    """
    s = str(val if val is not None else "")
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{s}"
    return s


# ----------------------------------------------------------------------
# 3. CSV Ingestion & Delimiter Detection
# ----------------------------------------------------------------------

def detect_delimiter(header_line: str) -> str:
    """
    Detects whether the CSV uses comma or semicolon based on the header line.
    Returns ',' or ';'. Raises ValueError on ambiguity.
    """
    candidates = []
    for delimiter in (",", ";"):
        try:
            parsed = next(csv.reader(io.StringIO(header_line, newline=""), delimiter=delimiter), [])
            if len(parsed) > 1:
                candidates.append(delimiter)
        except csv.Error:
            continue
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ValueError("Ambiguous CSV delimiter in header. Please choose comma or semicolon explicitly.")
    raise ValueError("Unable to detect CSV delimiter (header row has no delimiter).")


def inspect_csv_document(raw_bytes: bytes, filename: str, side: str, delimiter_choice: Optional[str] = None) -> Dict[str, Any]:
    """Inspect an uploaded CSV in memory and return conservative setup/mapping suggestions."""
    if side not in ("bank", "ledger"):
        raise ValueError("Statement side must be bank or ledger.")
    if not filename.lower().endswith(".csv"):
        raise ValueError("Please choose a CSV file.")
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("CSV exceeds the 2 MB upload limit.")
    if not raw_bytes or b"\x00" in raw_bytes:
        raise ValueError("CSV is empty or contains binary data.")
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must use UTF-8 encoding.") from exc

    if delimiter_choice not in (None, ",", ";"):
        raise ValueError("Delimiter must be comma or semicolon.")
    delimiter = delimiter_choice or detect_delimiter(text)
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("CSV has no header row.")
    headers = [str(header or "").strip() for header in reader.fieldnames]
    if not any(headers) or len(headers) > MAX_COLUMNS:
        raise ValueError("CSV must have a header row and no more than 20 columns.")
    reader.fieldnames = headers
    rows: List[Dict[str, str]] = []
    for row_number, row in enumerate(reader, start=1):
        if row_number > MAX_DATA_ROWS:
            raise ValueError("CSV exceeds the 3,000 row limit.")
        if None in row or any(len(value or "") > MAX_FIELD_LENGTH for value in row.values()):
            raise ValueError(f"CSV row {row_number} has too many columns or a field over 500 characters.")
        rows.append({key: str(value or "").strip() for key, value in row.items()})
    if not rows:
        raise ValueError("CSV contains a header but no transaction rows.")

    def key(header: str) -> str:
        return re.sub(r"[^a-z0-9]", "", header.casefold())

    normalized = {header: key(header) for header in headers}

    def find_exact(names: Set[str]) -> Optional[str]:
        return next((header for header in headers if normalized[header] in names), None)

    def find_contains(names: Tuple[str, ...]) -> Optional[str]:
        return next((header for header in headers if any(token in normalized[header] for token in names)), None)

    date_column = find_exact({"date", "transactiondate", "postingdate", "valuedate", "txndate", "settlementdate"})
    description_column = find_exact({"description", "narration", "particulars", "transactiondetails", "details", "memo", "merchant"}) or find_contains(("description", "narration", "particular"))
    reference_column = find_exact({"reference", "ref", "referencenumber", "transactionid", "txnid", "chequenumber", "checknumber"})
    currency_column = find_exact({"currency", "currencycode", "curr"})
    amount_column = find_exact({"amount", "transactionamount", "txnamount", "signedamount", "netamount", "value"})
    debit_column = find_exact({"debit", "debitamount", "withdrawal", "withdrawals", "withdrawalamount", "dr"}) or find_contains(("debit", "withdrawal"))
    credit_column = find_exact({"credit", "creditamount", "deposit", "deposits", "depositamount", "cr"}) or find_contains(("credit", "deposit"))

    amount_mode = "signed" if amount_column else "debit_credit" if debit_column and credit_column else "signed"
    decimal_separator = "."
    number_samples = [row.get(amount_column or debit_column or credit_column or "", "") for row in rows[:30]]
    for sample in number_samples:
        cleaned = re.sub(r"[^0-9,.]", "", sample)
        if "." in cleaned and "," in cleaned:
            decimal_separator = "." if cleaned.rfind(".") > cleaned.rfind(",") else ","
            break
        if "," in cleaned:
            decimal_separator = "," if len(cleaned.rsplit(",", 1)[-1]) == 2 else "."
            break

    negative_formats = {
        "leading_minus": True,
        "parentheses": any(value.startswith("(") and value.endswith(")") for value in number_samples),
        "trailing_minus": any(value.endswith("-") for value in number_samples),
    }
    mapping: Dict[str, Any] = {
        "date_column": date_column,
        "description_column": description_column,
        "amount_mode": amount_mode,
        "amount_column": amount_column,
        "debit_column": debit_column,
        "credit_column": credit_column,
        "reference_column": reference_column,
        "currency_column": currency_column,
        "decimal_separator": decimal_separator,
        "negative_formats": negative_formats,
    }

    date_strings = [row.get(date_column, "") for row in rows if date_column and row.get(date_column)]
    date_format, date_ambiguous = infer_or_validate_batch_date_format(date_strings) if date_strings else (None, False)
    if date_format:
        mapping["date_format"] = date_format
    parsed_dates = []
    if date_format and not date_ambiguous:
        for raw_date in date_strings:
            normalized_date, error = parse_date_to_iso(raw_date, date_format)
            if normalized_date and not error:
                parsed_dates.append(normalized_date)

    currency = None
    if currency_column:
        found_currencies = {row[currency_column].upper() for row in rows if row.get(currency_column)}
        if len(found_currencies) == 1 and next(iter(found_currencies)) in ALLOWED_CURRENCIES:
            currency = next(iter(found_currencies))
    if not currency:
        monetary_values = [value for row in rows[:100] for value in (row.get(amount_column or "", ""), row.get(debit_column or "", ""), row.get(credit_column or "", ""))]
        if any("₹" in value for value in monetary_values):
            currency = "INR"
        elif any("€" in value for value in monetary_values):
            currency = "EUR"
        elif any("£" in value for value in monetary_values):
            currency = "GBP"
        elif any("¥" in value for value in monetary_values):
            currency = "JPY"

    mapping["can_auto_map"] = bool(date_column and description_column and ((amount_mode == "signed" and amount_column) or (amount_mode == "debit_credit" and debit_column and credit_column)))
    if side == "ledger":
        # A signed amount already expresses the cash direction. Only separate
        # debit/credit columns need the user to declare their ledger perspective.
        mapping["ledger_perspective"] = "cash_account" if amount_mode == "signed" else None
        mapping["can_auto_map"] = bool(
            mapping["can_auto_map"] and amount_mode == "signed"
        )

    safe_name = re.sub(r"[_-]+", " ", Path(filename).stem).strip().title()[:120]
    detected = [label for label, column in (("date", date_column), ("description", description_column), ("amount", amount_column), ("debit", debit_column), ("credit", credit_column), ("reference", reference_column), ("currency", currency_column)) if column]
    return {
        "filename": filename,
        "side": side,
        "delimiter": delimiter,
        "headers": headers,
        "row_count": len(rows),
        "preview_rows": [{header: row.get(header, "") for header in headers} for row in rows[:5]],
        "mapping": mapping,
        "detected_fields": detected,
        "period_start": min(parsed_dates) if parsed_dates else None,
        "period_end": max(parsed_dates) if parsed_dates else None,
        "date_format": date_format,
        "date_ambiguous": date_ambiguous,
        "currency": currency,
        "run_name_suggestion": safe_name or f"{side.title()} reconciliation",
    }


def ingest_raw_csv_upload(
    run_id: str,
    side: str,
    filename: str,
    raw_bytes: bytes,
    user_id: str,
    delimiter_choice: Optional[str] = None
) -> Dict[str, Any]:
    """
    Validates and stages raw CSV upload without parsing into authoritative tables.
    Enforces strict size, UTF-8, row count, and column limits.
    """
    start_time = time.perf_counter()

    if side not in ("bank", "ledger"):
        raise ValueError(f"Invalid side '{side}'. Must be 'bank' or 'ledger'.")

    # Filename check
    if not filename.lower().endswith(".csv"):
        raise ValueError(f"Filename '{filename}' must end with .csv")

    # Byte length check
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Upload exceeds maximum allowable size of 2 MB ({len(raw_bytes)} bytes).")

    # Reject NUL bytes
    if b"\x00" in raw_bytes:
        raise ValueError("Binary or NUL-byte characters are strictly forbidden in statement uploads.")

    # Validate UTF-8 (handling BOM)
    try:
        if raw_bytes.startswith(b"\xef\xbb\xbf"):
            text = raw_bytes.decode("utf-8-sig")
        else:
            text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"CSV content is not valid UTF-8: {e}")

    # Check for empty text
    lines = text.splitlines()
    if not lines or not any(l.strip() for l in lines):
        raise ValueError("Uploaded CSV file is completely empty.")

    # Detect delimiter from first non-empty line
    first_line = ""
    for l in lines:
        if l.strip():
            first_line = l
            break
    if delimiter_choice is not None:
        if delimiter_choice not in (",", ";"):
            raise ValueError("delimiter must be ',' or ';'.")
        delimiter = delimiter_choice
    else:
        delimiter = detect_delimiter(text)

    # Parse with csv.reader to check constraints and support multiline quoted fields
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header_row = next(reader, None)
    except Exception as e:
        raise ValueError(f"Malformed CSV content: {e}")

    if not header_row or not any(h.strip() for h in header_row):
        raise ValueError("CSV contains no valid header columns.")

    if len(header_row) > MAX_COLUMNS:
        raise ValueError(f"CSV exceeds maximum column limit ({len(header_row)} > {MAX_COLUMNS}).")

    headers = [h.strip() for h in header_row]
    if any(len(h) > MAX_FIELD_LENGTH for h in headers):
        raise ValueError(f"CSV header field exceeds {MAX_FIELD_LENGTH} characters.")

    sample_rows = []
    total_data_rows = 0
    for idx, row in enumerate(reader, start=1):
        if idx > MAX_DATA_ROWS:
            raise ValueError(f"CSV exceeds maximum allowable limit of {MAX_DATA_ROWS} data rows.")
        if len(row) > MAX_COLUMNS:
            raise ValueError(f"Row {idx} exceeds maximum column limit ({len(row)} > {MAX_COLUMNS}).")
        if len(row) != len(header_row):
            raise ValueError(f"Row {idx} has {len(row)} fields; expected {len(header_row)} (ragged CSV rows are not accepted).")
        for fld in row:
            if len(fld) > MAX_FIELD_LENGTH:
                raise ValueError(f"Row {idx} field exceeds {MAX_FIELD_LENGTH} characters.")
        if len(sample_rows) < 20:
            sample_rows.append(row)
        total_data_rows += 1

    if total_data_rows == 0:
        raise ValueError("CSV contains a header row but 0 data rows.")

    # Use the same conservative header/date inspection used by the review UI to
    # populate optional run metadata. A run remains valid if these are unknown.
    detected_metadata = inspect_csv_document(raw_bytes, filename, side, delimiter)

    # Calculate SHA-256
    file_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Check run existence and ownership
        cursor.execute("SELECT id, owner_user_id, status FROM runs WHERE id = ?", (run_id,))
        run_row = cursor.fetchone()
        if not run_row:
            raise KeyError(f"Run '{run_id}' not found.")

        cursor.execute("""
        UPDATE runs
        SET currency = COALESCE(currency, ?),
            period_start = CASE WHEN ? IS NULL THEN period_start WHEN period_start IS NULL OR ? < period_start THEN ? ELSE period_start END,
            period_end = CASE WHEN ? IS NULL THEN period_end WHEN period_end IS NULL OR ? > period_end THEN ? ELSE period_end END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (
            detected_metadata.get("currency"),
            detected_metadata.get("period_start"),
            detected_metadata.get("period_start"),
            detected_metadata.get("period_start"),
            detected_metadata.get("period_end"),
            detected_metadata.get("period_end"),
            detected_metadata.get("period_end"),
            run_id,
        ))

        # Check existing batches for this run and side
        cursor.execute("SELECT id FROM import_batches WHERE run_id = ? AND side = ? AND sha256 = ?", (run_id, side, file_sha256))
        if cursor.fetchone():
            raise ValueError(
                f"A statement file with identical SHA-256 hash ({file_sha256[:12]}...) "
                f"has already been uploaded for run {run_id} ({side} side)."
            )
        cursor.execute("""
        SELECT id, sha256, status FROM import_batches
        WHERE run_id = ? AND side = ? AND status IN ('staged', 'confirmed')
        """, (run_id, side))
        existing_batch = cursor.fetchone()
        if existing_batch:
            raise ValueError(
                f"An active {side} batch already exists for this run (status: {existing_batch['status']}). "
                f"You must discard batch '{existing_batch['id']}' before uploading a new statement."
            )

        batch_id = f"batch_{uuid.uuid4().hex[:12]}"

        # Save raw staged file
        staging_path = STAGING_DIR / f"{batch_id}.csv"
        with open(staging_path, "wb") as f:
            f.write(raw_bytes)

        # Insert staged batch record
        cursor.execute("""
        INSERT INTO import_batches (
            id, run_id, owner_user_id, user_id, side, record_type,
            filename, sha256, size_bytes, status, row_count, total_records, delimiter
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged', ?, ?, ?)
        """, (
            batch_id, run_id, user_id, user_id, side, side,
            filename, file_sha256, len(raw_bytes), total_data_rows, total_data_rows, delimiter
        ))

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        write_audit_log(
            conn, user_id, run_id, "upload_statement",
            {
                "batch_id": batch_id,
                "side": side,
                "filename": filename,
                "sha256": file_sha256,
                "size_bytes": len(raw_bytes),
                "total_rows": total_data_rows,
                "detected_delimiter": delimiter
            },
            duration_ms=duration_ms
        )

        conn.commit()

        return {
            "batch_id": batch_id,
            "run_id": run_id,
            "side": side,
            "filename": filename,
            "sha256": file_sha256,
            "size_bytes": len(raw_bytes),
            "detected_delimiter": delimiter,
            "detected_headers": headers,
            "sample_rows": sample_rows,
            "total_data_rows": total_data_rows,
            "status": "staged"
        }
    finally:
        conn.close()


def discard_import_batch(batch_id: str, user_id: str) -> Dict[str, Any]:
    """Marks an active staged or confirmed batch as discarded."""
    start_time = time.perf_counter()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT b.id, b.run_id, b.owner_user_id, b.side, b.status
        FROM import_batches b WHERE b.id = ?
        """, (batch_id,))
        batch = cursor.fetchone()
        if not batch:
            raise KeyError(f"Batch '{batch_id}' not found.")

        if batch["status"] == "confirmed":
            raise ValueError("Confirmed batches are read-only and cannot be discarded.")

        cursor.execute("UPDATE import_batches SET status = 'discarded' WHERE id = ?", (batch_id,))
        # Also clean up unconfirmed staged rows
        cursor.execute("UPDATE import_rows SET status = 'rejected' WHERE batch_id = ? AND status != 'confirmed'", (batch_id,))

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        write_audit_log(
            conn, user_id, batch["run_id"], "discard_batch",
            {"batch_id": batch_id, "side": batch["side"], "previous_status": batch["status"]},
            duration_ms=duration_ms
        )
        conn.commit()
        return {"status": "success", "batch_id": batch_id, "new_status": "discarded"}
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 4. Strict Amount & Date Parsing Algorithms
# ----------------------------------------------------------------------

def parse_csv_amount(
    raw_val: str,
    decimal_sep: str,
    negative_formats: Dict[str, bool],
    currency: str = "USD"
) -> Tuple[Optional[int], Optional[str]]:
    """
    Parses a monetary string into integer cents using strictly specified rules.
    Returns (cents, issue_message). If parsing fails, cents is None.
    """
    if raw_val is None:
        return None, "Amount is empty."

    s = raw_val.strip()
    if not s or s == "--" or s == "-":
        return None, "Amount is blank or empty ('--')."

    # Reject scientific notation
    if "e" in s.lower():
        return None, f"Scientific notation is not allowed: '{s}'"

    # Handle negative modes
    is_negative = False

    # Parentheses mode e.g. (123.45)
    if s.startswith("(") and s.endswith(")"):
        if not negative_formats.get("parentheses", False):
            return None, f"Parentheses negative format is not enabled for: '{s}'"
        is_negative = True
        s = s[1:-1].strip()

    # Trailing minus e.g. 123.45-
    elif s.endswith("-"):
        if not negative_formats.get("trailing_minus", False):
            return None, f"Trailing minus negative format is not enabled for: '{s}'"
        is_negative = True
        s = s[:-1].strip()

    # Leading minus e.g. -123.45
    elif s.startswith("-"):
        if not negative_formats.get("leading_minus", True):
            return None, f"Leading minus negative format is not enabled for: '{s}'"
        is_negative = True
        s = s[1:].strip()

    elif s.startswith("+"):
        s = s[1:].strip()

    # Permit only a symbol consistent with the run currency.
    symbols = {"$": {"USD", "CAD", "AUD", "SGD", "HKD"}, "€": {"EUR"}, "£": {"GBP"}, "¥": {"JPY"}, "₹": {"INR"}, "CHF": {"CHF"}}
    symbol_match = re.match(r"^(\$|€|£|¥|₹|CHF)\s*", s, re.IGNORECASE)
    if symbol_match:
        symbol = symbol_match.group(1).upper()
        allowed = symbols.get(symbol, symbols.get(symbol_match.group(1), set()))
        if currency not in allowed:
            return None, f"Currency symbol '{symbol_match.group(1)}' conflicts with run currency {currency}."
        s = s[symbol_match.end():].strip()

    if not s:
        return None, "Empty numeric value."

    # Validate thousands separators and decimals
    thousands_sep = "," if decimal_sep == "." else "."

    # Check for invalid characters
    allowed_chars = set("0123456789" + decimal_sep + thousands_sep)
    if any(c not in allowed_chars for c in s):
        return None, f"Contains invalid characters for decimal '{decimal_sep}': '{raw_val}'"

    # Check decimal separator count
    if s.count(decimal_sep) > 1:
        return None, f"Multiple decimal separators '{decimal_sep}' found: '{raw_val}'"

    parts = s.split(decimal_sep)
    int_part = parts[0]
    frac_part = parts[1] if len(parts) > 1 else ""

    # Reject more than 2 decimal places
    if len(frac_part) > 2:
        return None, f"Amount exceeds 2 decimal places: '{raw_val}'"

    # Validate thousands grouping if present
    if thousands_sep in int_part:
        groups = int_part.split(thousands_sep)
        western_grouping = 1 <= len(groups[0]) <= 3 and all(len(g) == 3 and g.isdigit() for g in groups[1:])
        indian_grouping = (
            len(groups) >= 3
            and 1 <= len(groups[0]) <= 2
            and all(len(group) == 2 and group.isdigit() for group in groups[1:-1])
            and len(groups[-1]) == 3
            and groups[-1].isdigit()
        )
        if not (western_grouping or indian_grouping):
            return None, f"Inconsistent thousands separator '{thousands_sep}' in integer part: '{raw_val}'"
        int_clean = "".join(groups)
    else:
        int_clean = int_part

    if not int_clean.isdigit() and int_clean != "":
        return None, f"Invalid digits in integer part: '{raw_val}'"

    dollars = int(int_clean) if int_clean else 0
    cents = int((frac_part + "00")[:2]) if frac_part else 0
    total_cents = dollars * 100 + cents

    if is_negative:
        total_cents = -total_cents

    return total_cents, None


def infer_or_validate_batch_date_format(
    date_strings: List[str],
    user_format: Optional[str] = None
) -> Tuple[Optional[str], bool]:
    """
    Examines all non-empty date strings to determine if format is unambiguous or inferable.
    Returns (resolved_format, is_ambiguous).
    """
    if user_format:
        return user_format, False

    has_iso = False
    has_first_gt_12 = False   # e.g. 25/01/2026 -> DD/MM/YYYY
    has_second_gt_12 = False  # e.g. 01/25/2026 -> MM/DD/YYYY

    for raw in date_strings:
        d = raw.strip()
        if not d:
            continue
        # Check ISO YYYY-MM-DD
        if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$", d):
            has_iso = True
            continue

        # Check delimiter / or - or .
        m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$", d)
        if m:
            p1 = int(m.group(1))
            p2 = int(m.group(2))
            if p1 > 12:
                has_first_gt_12 = True
            if p2 > 12:
                has_second_gt_12 = True

    if has_iso and not (has_first_gt_12 or has_second_gt_12):
        return "YYYY-MM-DD", False

    if has_first_gt_12 and not has_second_gt_12:
        return "DD/MM/YYYY", False

    if has_second_gt_12 and not has_first_gt_12:
        return "MM/DD/YYYY", False

    # If all components are <= 12 and not ISO, it is ambiguous
    return None, True


def parse_date_to_iso(date_str: str, resolved_format: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Parses date string into standard ISO 'YYYY-MM-DD'.
    Returns (iso_str, error_message).
    """
    s = date_str.strip()
    if not s:
        return None, "Date string is empty."

    # Direct ISO check
    m_iso = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", s)
    if m_iso:
        y, m, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        try:
            dt = datetime(y, m, d)
            return dt.strftime("%Y-%m-%d"), None
        except ValueError:
            return None, f"Invalid calendar date: '{s}'"

    if not resolved_format:
        return None, "Date format is ambiguous."

    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$", s)
    if not m:
        return None, f"Unrecognized date structure: '{s}'"

    p1, p2, p3 = int(m.group(1)), int(m.group(2)), int(m.group(3))
    # Normalize 2-digit year
    if p3 < 100:
        p3 += 2000

    if resolved_format == "DD/MM/YYYY":
        day, month, year = p1, p2, p3
    elif resolved_format == "MM/DD/YYYY":
        month, day, year = p1, p2, p3
    else:
        return None, f"Unsupported date format: '{resolved_format}'"

    try:
        dt = datetime(year, month, day)
        return dt.strftime("%Y-%m-%d"), None
    except ValueError:
        return None, f"Invalid calendar date components ({year}-{month}-{day}) from: '{s}'"


# ----------------------------------------------------------------------
# 5. Mapping Submission & Row Processing
# ----------------------------------------------------------------------

def map_and_process_import_batch(
    batch_id: str,
    mapping: Dict[str, Any],
    user_id: str
) -> Dict[str, Any]:
    """
    Applies user column mapping, parses all rows, validates amounts/dates/currencies,
    flags duplicates and ambiguities, and stores parsed rows in import_rows.
    """
    start_time = time.perf_counter()

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
        SELECT b.id, b.run_id, b.side, b.filename, b.status, b.delimiter,
               r.currency AS run_currency, r.period_start, r.period_end
        FROM import_batches b
        JOIN runs r ON b.run_id = r.id
        WHERE b.id = ?
        """, (batch_id,))
        batch = cursor.fetchone()
        if not batch:
            raise KeyError(f"Batch '{batch_id}' not found.")

        if batch["status"] == "confirmed":
            raise ValueError("Confirmed batches are read-only and cannot be re-mapped.")

        staging_path = STAGING_DIR / f"{batch_id}.csv"
        if not staging_path.exists():
            raise FileNotFoundError(f"Staged file for batch {batch_id} not found.")

        with open(staging_path, "rb") as f:
            raw_bytes = f.read()

        # Decode UTF-8
        if raw_bytes.startswith(b"\xef\xbb\xbf"):
            text = raw_bytes.decode("utf-8-sig")
        else:
            text = raw_bytes.decode("utf-8")

        delimiter = batch["delimiter"] or detect_delimiter(text)

        reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("CSV contains no valid headers.")

        # Validate mapping parameters
        date_col = mapping.get("date_column")
        amount_mode = mapping.get("amount_mode", "signed")
        decimal_sep = mapping.get("decimal_separator", ".")
        desc_col = mapping.get("description_column")
        ref_col = mapping.get("reference_column")
        curr_col = mapping.get("currency_column")
        user_date_fmt = mapping.get("date_format")
        neg_formats = mapping.get("negative_formats", {"leading_minus": True})

        if not date_col or date_col not in reader.fieldnames:
            raise ValueError(f"Date column '{date_col}' not found in CSV headers.")
        if not desc_col or desc_col not in reader.fieldnames:
            raise ValueError(f"Description column '{desc_col}' not found in CSV headers.")

        if amount_mode == "signed":
            amt_col = mapping.get("amount_column")
            if not amt_col or amt_col not in reader.fieldnames:
                raise ValueError(f"Amount column '{amt_col}' not found in CSV headers.")
        elif amount_mode == "debit_credit":
            debit_col = mapping.get("debit_column")
            credit_col = mapping.get("credit_column")
            if not debit_col or debit_col not in reader.fieldnames:
                raise ValueError(f"Debit column '{debit_col}' not found in CSV headers.")
            if not credit_col or credit_col not in reader.fieldnames:
                raise ValueError(f"Credit column '{credit_col}' not found in CSV headers.")
        else:
            raise ValueError(f"Invalid amount_mode '{amount_mode}'. Must be 'signed' or 'debit_credit'.")

        side = batch["side"]
        ledger_perspective = mapping.get("ledger_perspective")
        if side == "ledger":
            if not ledger_perspective or ledger_perspective not in ("cash_account", "bank"):
                raise ValueError(
                    "For ledger imports, 'ledger_perspective' must be explicitly specified "
                    "as either 'cash_account' (debit=increase) or 'bank' (credit=increase)."
                )

        # First pass: collect date strings to infer date format
        raw_rows_data = []
        source_lines = text.splitlines(keepends=True)
        prior_line_end = reader.reader.line_num
        date_strings = []
        for row in reader:
            line_end = reader.reader.line_num
            row["__exact_raw_line"] = "".join(source_lines[prior_line_end:line_end])
            prior_line_end = line_end
            raw_rows_data.append(row)
            d_val = row.get(date_col, "")
            if d_val:
                date_strings.append(d_val)

        resolved_date_format, is_date_ambiguous = infer_or_validate_batch_date_format(
            date_strings, user_format=user_date_fmt
        )
        if resolved_date_format and resolved_date_format != "YYYY-MM-DD" and not user_date_fmt:
            return {
                "batch_id": batch_id,
                "status": "staged",
                "date_format_confirmation_required": True,
                "proposed_date_format": resolved_date_format,
                "message": "Confirm the inferred date format by submitting it as date_format before parsing rows."
            }

        # Clear any prior rows for this batch (if re-mapping)
        cursor.execute("DELETE FROM import_rows WHERE batch_id = ?", (batch_id,))

        seen_signatures: Dict[Tuple, int] = {}  # signature -> row_number
        parsed_rows = []

        valid_count = 0
        invalid_count = 0
        ambiguous_date_count = 0
        duplicate_count = 0

        for row_idx, row in enumerate(raw_rows_data, start=1):
            raw_line_text = row.get("__exact_raw_line", "")
            issues = []
            row_status = "valid"

            # 1. Date processing
            raw_date = row.get(date_col, "").strip()
            date_iso, date_err = parse_date_to_iso(raw_date, resolved_date_format)
            if date_err:
                if "ambiguous" in date_err.lower():
                    row_status = "ambiguous_date"
                    issues.append("Date is ambiguous between DD/MM and MM/DD; specify date_format.")
                    ambiguous_date_count += 1
                else:
                    row_status = "invalid"
                    issues.append(f"Invalid date: {date_err}")
                    invalid_count += 1
            else:
                # Check period boundary
                if batch["period_start"] and date_iso < batch["period_start"]:
                    issues.append(f"Date {date_iso} precedes run start {batch['period_start']}.")
                if batch["period_end"] and date_iso > batch["period_end"]:
                    issues.append(f"Date {date_iso} exceeds run end {batch['period_end']}.")

            # 2. Description & Reference
            raw_desc = row.get(desc_col, "")
            clean_desc = sanitize_text(raw_desc, 200)
            if not clean_desc:
                issues.append("Description is empty.")
                if row_status == "valid":
                    row_status = "invalid"
                    invalid_count += 1

            raw_ref = row.get(ref_col, "") if ref_col else None
            clean_ref = sanitize_text(raw_ref, 64) if raw_ref else None

            # 3. Currency
            row_currency = batch["run_currency"]
            if curr_col and row.get(curr_col):
                file_curr = row.get(curr_col, "").strip().upper()
                if file_curr != batch["run_currency"]:
                    issues.append(f"Currency mismatch: file has '{file_curr}', run expects '{batch['run_currency']}'.")
                    if row_status == "valid":
                        row_status = "invalid"
                        invalid_count += 1
                else:
                    row_currency = file_curr

            # 4. Amount parsing per sign convention
            amount_cents = None
            if amount_mode == "signed":
                raw_amt = row.get(amt_col, "")
                parsed_c, amt_err = parse_csv_amount(raw_amt, decimal_sep, neg_formats, batch["run_currency"])
                if amt_err:
                    issues.append(f"Amount error: {amt_err}")
                    if row_status == "valid":
                        row_status = "invalid"
                        invalid_count += 1
                else:
                    # In signed mode:
                    # Bank: positive = cash increase, negative = cash decrease
                    # Ledger: positive = cash increase, negative = cash decrease
                    amount_cents = parsed_c

            elif amount_mode == "debit_credit":
                raw_deb = row.get(debit_col, "").strip()
                raw_cred = row.get(credit_col, "").strip()

                deb_c = 0
                cred_c = 0
                deb_has_val = bool(raw_deb and raw_deb != "--" and raw_deb != "-")
                cred_has_val = bool(raw_cred and raw_cred != "--" and raw_cred != "-")

                if not deb_has_val and not cred_has_val:
                    issues.append("Both debit and credit columns are blank.")
                    if row_status == "valid":
                        row_status = "invalid"
                        invalid_count += 1
                else:
                    amt_err_occurred = False
                    if deb_has_val:
                        deb_parsed, deb_err = parse_csv_amount(raw_deb, decimal_sep, neg_formats, batch["run_currency"])
                        if deb_err:
                            issues.append(f"Debit column error: {deb_err}")
                            amt_err_occurred = True
                        else:
                            deb_c = abs(deb_parsed)
                    if cred_has_val:
                        cred_parsed, cred_err = parse_csv_amount(raw_cred, decimal_sep, neg_formats, batch["run_currency"])
                        if cred_err:
                            issues.append(f"Credit column error: {cred_err}")
                            amt_err_occurred = True
                        else:
                            cred_c = abs(cred_parsed)

                    if amt_err_occurred:
                        if row_status == "valid":
                            row_status = "invalid"
                            invalid_count += 1
                    else:
                        # Apply sign convention:
                        if side == "bank":
                            # Bank: Credit = Inflow (+), Debit = Outflow (-)
                            amount_cents = cred_c - deb_c
                        elif side == "ledger":
                            if ledger_perspective == "cash_account":
                                # Debit = Cash Inflow (+), Credit = Cash Outflow (-)
                                amount_cents = deb_c - cred_c
                            else:
                                # Bank perspective: Credit = Inflow (+), Debit = Outflow (-)
                                amount_cents = cred_c - deb_c

            # 5. Duplicate Detection (date_iso, amount_cents, normalized_desc, clean_ref)
            duplicate_of = None
            if row_status == "valid" and amount_cents is not None and date_iso:
                sig = (date_iso, amount_cents, normalize_description_for_dedup(clean_desc), clean_ref or "")
                if sig in seen_signatures:
                    duplicate_of = seen_signatures[sig]
                    row_status = "duplicate_flagged"
                    issues.append(f"Duplicate of row #{duplicate_of} with identical date, amount, description and ref.")
                    duplicate_count += 1
                else:
                    seen_signatures[sig] = row_idx
                    valid_count += 1

            cursor.execute("""
            INSERT INTO import_rows (
                batch_id, row_index, row_number, raw_data_json, raw_line, date_iso, description,
                raw_description, reference, amount_cents, currency,
                status, issues_json, edited, duplicate_of_row
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """, (
                batch_id, row_idx, row_idx,
                json.dumps({k: v for k, v in row.items() if k != "__exact_raw_line"}, ensure_ascii=False),
                raw_line_text, date_iso, clean_desc,
                raw_desc, clean_ref, amount_cents, row_currency,
                row_status, json.dumps(issues), duplicate_of
            ))

        # Update batch with mapping and date_format
        cursor.execute("""
        UPDATE import_batches
        SET mapping_json = ?, date_format = ?, row_count = ?, total_records = ?
        WHERE id = ?
        """, (
            json.dumps(mapping),
            resolved_date_format,
            len(raw_rows_data),
            len(raw_rows_data),
            batch_id
        ))

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        write_audit_log(
            conn, user_id, batch["run_id"], "map_import_batch",
            {
                "batch_id": batch_id,
                "total_rows": len(raw_rows_data),
                "valid": valid_count,
                "invalid": invalid_count,
                "ambiguous_date": ambiguous_date_count,
                "duplicate_flagged": duplicate_count,
                "resolved_date_format": resolved_date_format
            },
            duration_ms=duration_ms
        )

        conn.commit()

        return {
            "batch_id": batch_id,
            "status": "staged",
            "total_rows": len(raw_rows_data),
            "valid": valid_count,
            "invalid": invalid_count,
            "ambiguous_date": ambiguous_date_count,
            "duplicate_flagged": duplicate_count,
            "resolved_date_format": resolved_date_format,
            "is_date_ambiguous": is_date_ambiguous
        }
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 6. Staging Review, Row Edits, Reject/Restore, Summary
# ----------------------------------------------------------------------

def get_import_batch_rows(
    batch_id: str,
    page: int = 1,
    page_size: int = 50,
    status_filter: Optional[str] = None,
    search_query: Optional[str] = None,
    sort_by: str = "row_number",
    sort_dir: str = "asc"
) -> Dict[str, Any]:
    """Retrieves paginated, filtered import rows with whitelisted parameters."""
    page_size = max(1, min(page_size, 200))
    page = max(1, page)
    offset = (page - 1) * page_size

    if sort_by not in WHITELISTED_ROW_COLUMNS:
        sort_by = "row_number"
    direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

    conn = get_connection()
    cursor = conn.cursor()

    try:
        where_clauses = ["batch_id = ?"]
        params = [batch_id]

        if status_filter and status_filter.lower() != "all":
            where_clauses.append("status = ?")
            params.append(status_filter.lower())

        if search_query:
            where_clauses.append("(description LIKE ? OR reference LIKE ? OR raw_line LIKE ?)")
            q = f"%{search_query.strip()}%"
            params.extend([q, q, q])

        where_sql = " AND ".join(where_clauses)

        cursor.execute(f"SELECT COUNT(*) FROM import_rows WHERE {where_sql}", params)
        total_count = cursor.fetchone()[0]

        cursor.execute(f"""
        SELECT id, batch_id, row_number, raw_line, date_iso, description,
               raw_description, reference, amount_cents, currency, status,
               issues_json, edited, duplicate_of_row
        FROM import_rows
        WHERE {where_sql}
        ORDER BY {sort_by} {direction}
        LIMIT ? OFFSET ?
        """, (*params, page_size, offset))

        rows = []
        for r in cursor.fetchall():
            row_dict = dict(r)
            row_dict["issues"] = json.loads(row_dict["issues_json"] or "[]")
            rows.append(row_dict)

        return {
            "rows": rows,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": (total_count + page_size - 1) // page_size if total_count > 0 else 1
        }
    finally:
        conn.close()


def edit_import_row(
    batch_id: str,
    row_id: int,
    updates: Dict[str, Any],
    user_id: str
) -> Dict[str, Any]:
    """
    Patches date, amount, description, or reference on a staged row.
    Performs re-validation, logs changes in import_row_edits, preserves raw_line.
    """
    start_time = time.perf_counter()
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
        SELECT r.*, b.status AS batch_status, b.run_id, b.date_format,
               rn.currency AS run_currency, rn.period_start, rn.period_end
        FROM import_rows r
        JOIN import_batches b ON r.batch_id = b.id
        JOIN runs rn ON b.run_id = rn.id
        WHERE r.id = ? AND r.batch_id = ?
        """, (row_id, batch_id))
        row = cursor.fetchone()
        if not row:
            raise KeyError(f"Row {row_id} not found in batch {batch_id}.")

        if row["batch_status"] == "confirmed":
            raise ValueError("Cannot edit rows in a confirmed batch.")

        field_edits = []
        new_date = row["date_iso"]
        new_amount_cents = row["amount_cents"]
        new_desc = row["description"]
        new_ref = row["reference"]

        # Validate edited fields
        if "date" in updates and updates["date"] is not None:
            raw_d = str(updates["date"]).strip()
            date_iso, date_err = parse_date_to_iso(raw_d, row["date_format"] or "YYYY-MM-DD")
            if date_err:
                raise ValueError(f"Invalid date format: {date_err}")
            field_edits.append(("date_iso", row["date_iso"], date_iso))
            new_date = date_iso

        if "amount" in updates and updates["amount"] is not None:
            raw_a = str(updates["amount"]).strip()
            # User edits are decimal strings; parse via parse_to_cents
            parsed_c = parse_to_cents(raw_a)
            field_edits.append(("amount_cents", str(row["amount_cents"]), str(parsed_c)))
            new_amount_cents = parsed_c

        if "description" in updates and updates["description"] is not None:
            clean_d = sanitize_text(str(updates["description"]), 200)
            if not clean_d:
                raise ValueError("Description cannot be empty.")
            field_edits.append(("description", row["description"], clean_d))
            new_desc = clean_d

        if "reference" in updates and updates["reference"] is not None:
            clean_r = sanitize_text(str(updates["reference"]), 64)
            field_edits.append(("reference", row["reference"], clean_r))
            new_ref = clean_r

        if not field_edits:
            return {"status": "no_changes", "row_id": row_id}

        # Check issues on the re-validated row
        issues = []
        new_status = "valid"
        if row["period_start"] and new_date < row["period_start"]:
            issues.append(f"Date {new_date} precedes run start {row['period_start']}.")
        if row["period_end"] and new_date > row["period_end"]:
            issues.append(f"Date {new_date} exceeds run end {row['period_end']}.")

        for fld, old_val, new_val in field_edits:
            cursor.execute("""
            INSERT INTO import_row_edits (row_id, field, old_value, new_value, user_id, ts)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (row_id, fld, str(old_val), str(new_val), user_id))

        cursor.execute("""
        UPDATE import_rows
        SET date_iso = ?, amount_cents = ?, description = ?, reference = ?,
            status = ?, issues_json = ?, edited = 1
        WHERE id = ?
        """, (new_date, new_amount_cents, new_desc, new_ref, new_status, json.dumps(issues), row_id))

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        write_audit_log(
            conn, user_id, row["run_id"], "edit_import_row",
            {"row_id": row_id, "batch_id": batch_id, "edits": [f[0] for f in field_edits]},
            duration_ms=duration_ms
        )

        conn.commit()
        return {"status": "success", "row_id": row_id, "new_status": new_status}
    finally:
        conn.close()


def set_import_row_status(
    batch_id: str,
    row_id: int,
    action: str,
    user_id: str
) -> Dict[str, Any]:
    """Handles reject, restore, or include-duplicate on a staged row."""
    start_time = time.perf_counter()
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
        SELECT r.*, b.status AS batch_status, b.run_id
        FROM import_rows r
        JOIN import_batches b ON r.batch_id = b.id
        WHERE r.id = ? AND r.batch_id = ?
        """, (row_id, batch_id))
        row = cursor.fetchone()
        if not row:
            raise KeyError(f"Row {row_id} not found in batch {batch_id}.")

        if row["batch_status"] == "confirmed":
            raise ValueError("Cannot modify row status in a confirmed batch.")

        if action == "reject":
            new_status = "rejected"
        elif action == "restore":
            issues = json.loads(row["issues_json"] or "[]")
            if any("ambiguous" in i.lower() for i in issues):
                new_status = "ambiguous_date"
            elif row["duplicate_of_row"]:
                new_status = "duplicate_flagged"
            elif any("error" in i.lower() or "invalid" in i.lower() for i in issues):
                new_status = "invalid"
            else:
                new_status = "valid"
        elif action == "include-duplicate":
            new_status = "valid"
        else:
            raise ValueError(f"Unknown row status action: '{action}'")

        cursor.execute("UPDATE import_rows SET status = ? WHERE id = ?", (new_status, row_id))

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        write_audit_log(
            conn, user_id, row["run_id"], f"{action}_import_row",
            {"row_id": row_id, "batch_id": batch_id, "old_status": row["status"], "new_status": new_status},
            duration_ms=duration_ms
        )

        conn.commit()
        return {"status": "success", "row_id": row_id, "new_status": new_status}
    finally:
        conn.close()


def get_import_batch_summary(batch_id: str) -> Dict[str, Any]:
    """
    Computes status counts and authoritative integrity balance check:
    Opening + sum(amount_cents of rows that would be confirmed) vs Closing.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
        SELECT b.id, b.run_id, b.side, b.filename, b.status, b.date_format,
               r.bank_opening_cents, r.bank_closing_cents,
               r.ledger_opening_cents, r.ledger_closing_cents, r.currency
        FROM import_batches b
        JOIN runs r ON b.run_id = r.id
        WHERE b.id = ?
        """, (batch_id,))
        batch = cursor.fetchone()
        if not batch:
            raise KeyError(f"Batch '{batch_id}' not found.")

        # Status counts
        cursor.execute("""
        SELECT status, COUNT(*) AS cnt
        FROM import_rows
        WHERE batch_id = ?
        GROUP BY status
        """, (batch_id,))
        counts_by_status = {r["status"]: r["cnt"] for r in cursor.fetchall()}

        valid_count = counts_by_status.get("valid", 0)
        invalid_count = counts_by_status.get("invalid", 0)
        ambiguous_date_count = counts_by_status.get("ambiguous_date", 0)
        duplicate_flagged_count = counts_by_status.get("duplicate_flagged", 0)
        rejected_count = counts_by_status.get("rejected", 0)
        confirmed_count = counts_by_status.get("confirmed", 0)
        total_rows = sum(counts_by_status.values())

        # Sum of candidate rows (valid or already confirmed)
        cursor.execute("""
        SELECT COALESCE(SUM(amount_cents), 0)
        FROM import_rows
        WHERE batch_id = ? AND status IN ('valid', 'confirmed')
        """, (batch_id,))
        candidate_cents = cursor.fetchone()[0]

        side = batch["side"]
        if side == "bank":
            opening_cents = batch["bank_opening_cents"]
            closing_cents = batch["bank_closing_cents"]
        else:
            opening_cents = batch["ledger_opening_cents"]
            closing_cents = batch["ledger_closing_cents"]

        balance_available = opening_cents is not None and closing_cents is not None
        computed_closing_cents = opening_cents + candidate_cents if balance_available else None
        integrity_diff_cents = computed_closing_cents - closing_cents if balance_available else None

        return {
            "batch_id": batch_id,
            "run_id": batch["run_id"],
            "side": side,
            "filename": batch["filename"],
            "status": batch["status"],
            "currency": batch["currency"],
            "counts": {
                "total": total_rows,
                "valid": valid_count,
                "invalid": invalid_count,
                "ambiguous_date": ambiguous_date_count,
                "duplicate_flagged": duplicate_flagged_count,
                "rejected": rejected_count,
                "confirmed": confirmed_count
            },
            "integrity_check": {
                "side": side,
                "applicable": balance_available,
                "opening_cents": opening_cents,
                "opening_formatted": format_cents(opening_cents) if opening_cents is not None else None,
                "closing_cents": closing_cents,
                "closing_formatted": format_cents(closing_cents) if closing_cents is not None else None,
                "candidate_net_cents": candidate_cents,
                "candidate_net_formatted": format_cents(candidate_cents),
                "computed_closing_cents": computed_closing_cents,
                "computed_closing_formatted": format_cents(computed_closing_cents) if computed_closing_cents is not None else None,
                "difference_cents": integrity_diff_cents,
                "difference_formatted": format_cents(integrity_diff_cents) if integrity_diff_cents is not None else None,
                "balanced": integrity_diff_cents == 0 if integrity_diff_cents is not None else None
            }
        }
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 7. Batch Confirmation (Staging Gate)
# ----------------------------------------------------------------------

def confirm_import_batch(
    batch_id: str,
    user_id: str,
    user_role: str,
    override_reason: Optional[str] = None
) -> Dict[str, Any]:
    """
    Confirms valid rows from import_rows into authoritative bank_records or ledger_records.
    Blocks if any non-rejected row is invalid, ambiguous_date, duplicate_flagged,
    or if integrity difference != 0 (unless admin override with min 10 char reason is provided).
    Entire copy executes inside ONE atomic SQLite transaction. Idempotent.
    """
    start_time = time.perf_counter()
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
        SELECT b.id, b.run_id, b.side, b.status, b.owner_user_id,
               r.bank_opening_cents, r.bank_closing_cents,
               r.ledger_opening_cents, r.ledger_closing_cents
        FROM import_batches b
        JOIN runs r ON b.run_id = r.id
        WHERE b.id = ?
        """, (batch_id,))
        batch = cursor.fetchone()
        if not batch:
            raise KeyError(f"Batch '{batch_id}' not found.")

        # Idempotent return if already confirmed
        if batch["status"] == "confirmed":
            cursor.execute("SELECT COUNT(*) FROM import_rows WHERE batch_id = ? AND status = 'confirmed'", (batch_id,))
            confirmed_cnt = cursor.fetchone()[0]
            return {
                "status": "already_confirmed",
                "batch_id": batch_id,
                "side": batch["side"],
                "confirmed_records": confirmed_cnt,
                "message": "Batch was already confirmed. No new records inserted."
            }

        # Check blocking conditions
        cursor.execute("""
        SELECT status, COUNT(*) AS cnt
        FROM import_rows
        WHERE batch_id = ?
        GROUP BY status
        """, (batch_id,))
        counts = {r["status"]: r["cnt"] for r in cursor.fetchall()}

        blocking_reasons = []
        if counts.get("invalid", 0) > 0:
            blocking_reasons.append(f"{counts['invalid']} invalid row(s) must be edited or rejected.")
        if counts.get("ambiguous_date", 0) > 0:
            blocking_reasons.append(f"{counts['ambiguous_date']} row(s) have ambiguous dates; resolve date_format.")
        if counts.get("duplicate_flagged", 0) > 0:
            blocking_reasons.append(f"{counts['duplicate_flagged']} flagged duplicate(s) must be included or rejected.")

        # Integrity difference check
        cursor.execute("""
        SELECT COALESCE(SUM(amount_cents), 0)
        FROM import_rows
        WHERE batch_id = ? AND status = 'valid'
        """, (batch_id,))
        candidate_cents = cursor.fetchone()[0]

        side = batch["side"]
        opening = batch["bank_opening_cents"] if side == "bank" else batch["ledger_opening_cents"]
        closing = batch["bank_closing_cents"] if side == "bank" else batch["ledger_closing_cents"]
        balance_available = opening is not None and closing is not None
        computed_closing = opening + candidate_cents if balance_available else None
        diff_cents = computed_closing - closing if balance_available else None

        if diff_cents is not None and diff_cents != 0:
            blocking_reasons.append(
                f"Integrity check failed: opening ({format_cents(opening)}) + net activity ({format_cents(candidate_cents)}) "
                f"= {format_cents(computed_closing)} does not equal closing ({format_cents(closing)}). "
                f"Difference is {format_cents(diff_cents)}."
            )

        is_override = False
        if blocking_reasons:
            # Check for valid admin override
            if user_role == "admin" and override_reason and len(override_reason.strip()) >= 10:
                is_override = True
            else:
                conn.close()
                return {
                    "error": "Batch confirmation blocked",
                    "blocking_reasons": blocking_reasons,
                    "difference_cents": diff_cents,
                    "can_override": user_role == "admin"
                }

        # ATOMIC CONFIRMATION TRANSACTION
        run_id = batch["run_id"]
        table_name = "bank_records" if side == "bank" else "ledger_records"
        prefix = "BANK" if side == "bank" else "LEDGER"

        # Fetch only valid rows
        cursor.execute("""
        SELECT id, row_number, date_iso, description, reference, amount_cents
        FROM import_rows
        WHERE batch_id = ? AND status = 'valid'
        ORDER BY row_number ASC
        """, (batch_id,))
        valid_rows = cursor.fetchall()

        inserted_count = 0
        for r in valid_rows:
            # Generate deterministic, unique record ID
            rec_id = f"{prefix}-{run_id[:8]}-{r['row_number']:04d}"
            cursor.execute(f"""
            INSERT INTO {table_name} (
                id, date, amount_cents, description, reference,
                user_id, batch_id, run_id, source_row_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rec_id,
                r["date_iso"],
                r["amount_cents"],
                r["description"],
                r["reference"],
                batch["owner_user_id"],
                batch_id,
                run_id,
                r["id"]
            ))
            inserted_count += 1

        # Mark confirmed rows
        cursor.execute("UPDATE import_rows SET status = 'confirmed' WHERE batch_id = ? AND status = 'valid'", (batch_id,))

        # Update batch status
        cursor.execute("""
        UPDATE import_batches
        SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP, confirmed_by = ?
        WHERE id = ?
        """, (user_id, batch_id))

        # Check if both sides are confirmed for this run
        cursor.execute("""
        SELECT COUNT(DISTINCT side) FROM import_batches
        WHERE run_id = ? AND status = 'confirmed'
        """, (run_id,))
        sides_confirmed = cursor.fetchone()[0]
        if sides_confirmed >= 2:
            cursor.execute("UPDATE runs SET status = 'imported' WHERE id = ?", (run_id,))

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        write_audit_log(
            conn, user_id, run_id, "confirm_import_batch",
            {
                "batch_id": batch_id,
                "side": side,
                "confirmed_count": inserted_count,
                "is_override": is_override,
                "override_reason": override_reason if is_override else None,
                "difference_cents": diff_cents
            },
            duration_ms=duration_ms
        )

        conn.commit()

        return {
            "status": "confirmed",
            "batch_id": batch_id,
            "side": side,
            "confirmed_count": inserted_count,
            "is_override": is_override
        }
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 8. Statement Checks & Reconciliation Engine Entry Point
# ----------------------------------------------------------------------

def get_run_statement_checks(run_id: str, side: str) -> Dict[str, Any]:
    """
    Pre-reconciliation statement checks for a run's uploaded batch:
    row count, duplicate count, out-of-period count, share of rows without reference, integrity difference.
    """
    if side not in ("bank", "ledger"):
        raise ValueError(f"Invalid side '{side}'")

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
        SELECT b.id, b.status, r.currency, r.period_start, r.period_end,
               r.bank_opening_cents, r.bank_closing_cents,
               r.ledger_opening_cents, r.ledger_closing_cents
        FROM import_batches b
        JOIN runs r ON b.run_id = r.id
        WHERE b.run_id = ? AND b.side = ? AND b.status IN ('staged', 'confirmed')
        """, (run_id, side))
        batch = cursor.fetchone()
        if not batch:
            return {"error": f"No active statement batch for side '{side}'"}

        batch_id = batch["id"]
        cursor.execute("SELECT COUNT(*) FROM import_rows WHERE batch_id = ?", (batch_id,))
        total_rows = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM import_rows WHERE batch_id = ? AND duplicate_of_row IS NOT NULL", (batch_id,))
        duplicate_rows = cursor.fetchone()[0]

        cursor.execute("""
        SELECT COUNT(*) FROM import_rows
        WHERE batch_id = ? AND (reference IS NULL OR trim(reference) = '')
        """, (batch_id,))
        no_ref_rows = cursor.fetchone()[0]

        out_of_period = 0
        if batch["period_start"] and batch["period_end"]:
            cursor.execute("""
            SELECT COUNT(*) FROM import_rows
            WHERE batch_id = ? AND (date_iso < ? OR date_iso > ?)
            """, (batch_id, batch["period_start"], batch["period_end"]))
            out_of_period = cursor.fetchone()[0]

        summary = get_import_batch_summary(batch_id)

        return {
            "side": side,
            "batch_id": batch_id,
            "batch_status": batch["status"],
            "total_rows": total_rows,
            "duplicate_count": duplicate_rows,
            "out_of_period_count": out_of_period,
            "missing_reference_share_percent": round((no_ref_rows / total_rows * 100), 2) if total_rows > 0 else 0.0,
            "integrity_difference_cents": summary["integrity_check"]["difference_cents"],
            "integrity_difference_formatted": summary["integrity_check"]["difference_formatted"],
            "integrity_check_applicable": summary["integrity_check"]["applicable"]
        }
    finally:
        conn.close()


def run_reconciliation_for_run(run_id: str, user_id: str) -> Dict[str, Any]:
    """
    Executes rules-only B1 reconciliation on a run's confirmed records.
    Entry point: reconcile_b1(bank_records, ledger_records) in reconcile/baselines.py:158.
    Ensures both sides confirmed, runs only once per run, persists matches/exceptions with run_id.
    """
    start_time = time.perf_counter()
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id, owner_user_id, status FROM runs WHERE id = ?", (run_id,))
        run_row = cursor.fetchone()
        if not run_row:
            raise KeyError(f"Run '{run_id}' not found.")

        if run_row["status"] == "reconciled":
            raise ValueError(f"Run '{run_id}' has already been reconciled. Rerun is not permitted.")

        # Check both sides confirmed
        cursor.execute("SELECT side FROM import_batches WHERE run_id = ? AND status = 'confirmed'", (run_id,))
        confirmed_sides = {r["side"] for r in cursor.fetchall()}

        if "bank" not in confirmed_sides:
            return {"error": "Missing confirmed bank statement", "missing": "bank"}
        if "ledger" not in confirmed_sides:
            return {"error": "Missing confirmed ledger statement", "missing": "ledger"}

        # Fetch confirmed bank records strictly for this run
        cursor.execute("""
        SELECT id, date, amount_cents, description, reference
        FROM bank_records
        WHERE run_id = ?
        ORDER BY date ASC, id ASC
        """, (run_id,))
        bank_records = [dict(r) for r in cursor.fetchall()]

        # Fetch confirmed ledger records strictly for this run
        cursor.execute("""
        SELECT id, date, amount_cents, description, reference
        FROM ledger_records
        WHERE run_id = ?
        ORDER BY date ASC, id ASC
        """, (run_id,))
        ledger_records = [dict(r) for r in cursor.fetchall()]

        # Call existing engine entry point in reconcile/baselines.py:158
        from reconcile.baselines import reconcile_b1
        recon_result = reconcile_b1(bank_records, ledger_records)

        matches = recon_result.get("matches", [])
        exceptions = recon_result.get("exceptions", [])

        # PERSIST MATCHES & EXCEPTIONS INSIDE ONE TRANSACTION
        # Clear any prior records for this run_id
        cursor.execute("DELETE FROM matches WHERE run_id = ?", (run_id,))
        cursor.execute("DELETE FROM exceptions WHERE run_id = ?", (run_id,))

        for m in matches:
            engine_match_id = m.get("match_id") or m.get("id") or f"M-{uuid.uuid4().hex[:8]}"
            # Engine IDs are run-local; persist globally unique IDs because match_id is a DB primary key.
            match_id = f"{run_id}-{engine_match_id}"
            match_type = m.get("method", m.get("match_type", "B1"))
            rule_score = m.get("confidence", 1.0)

            cursor.execute("""
            INSERT INTO matches (match_id, match_type, rule_score, method, confidence, status, user_id, run_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (match_id, match_type, rule_score, match_type, rule_score, m.get("status", "matched"), user_id, run_id))

            for bid in m.get("bank_record_ids", []):
                cursor.execute("""
                INSERT INTO match_members (match_id, record_id, side, user_id)
                VALUES (?, ?, 'bank', ?)
                """, (match_id, bid, user_id))

            for lid in m.get("ledger_record_ids", []):
                cursor.execute("""
                INSERT INTO match_members (match_id, record_id, side, user_id)
                VALUES (?, ?, 'ledger', ?)
                """, (match_id, lid, user_id))

        for exc in exceptions:
            rec_id = exc.get("record_id")
            side = exc.get("side", "bank")
            reason = exc.get("reason", exc.get("type", "unmatched"))

            cursor.execute("""
            INSERT INTO exceptions (record_id, side, type, status, user_id, run_id)
            VALUES (?, ?, ?, 'open', ?, ?)
            """, (rec_id, side, reason, user_id, run_id))

        cursor.execute("UPDATE runs SET status = 'reconciled' WHERE id = ?", (run_id,))

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        write_audit_log(
            conn, user_id, run_id, "reconcile_run",
            {
                "run_id": run_id,
                "engine": "reconcile/baselines.py:reconcile_b1",
                "bank_records_count": len(bank_records),
                "ledger_records_count": len(ledger_records),
                "matches_count": len(matches),
                "exceptions_count": len(exceptions)
            },
            duration_ms=duration_ms
        )

        conn.commit()

        return {
            "status": "reconciled",
            "run_id": run_id,
            "engine": "reconcile/baselines.py:reconcile_b1",
            "matches_count": len(matches),
            "exceptions_count": len(exceptions),
            "duration_ms": duration_ms
        }
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 9. Results & Output Normalization
# ----------------------------------------------------------------------

def get_run_results(run_id: str) -> Dict[str, Any]:
    """
    Retrieves stored reconciliation results for a run:
    deterministic features, rule_score (not a probability), arithmetic consistency check,
    omits ai_rationale, and lists known engine limitations plainly.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        run = cursor.fetchone()
        if not run:
            raise KeyError(f"Run '{run_id}' not found.")

        # Matches
        cursor.execute("""
        SELECT m.match_id AS id, m.match_type, m.rule_score, m.status, m.created_at
        FROM matches m
        WHERE m.run_id = ?
        ORDER BY m.created_at ASC, m.match_id ASC
        """, (run_id,))
        match_rows = cursor.fetchall()

        matches = []
        for mr in match_rows:
            mid = mr["id"]
            # Fetch members
            cursor.execute("""
            SELECT mm.side, mm.record_id,
                   CASE WHEN mm.side = 'bank' THEN b.date ELSE l.date END AS date,
                   CASE WHEN mm.side = 'bank' THEN b.amount_cents ELSE l.amount_cents END AS amount_cents,
                   CASE WHEN mm.side = 'bank' THEN b.description ELSE l.description END AS description,
                   CASE WHEN mm.side = 'bank' THEN b.reference ELSE l.reference END AS reference
            FROM match_members mm
            LEFT JOIN bank_records b ON mm.side = 'bank' AND mm.record_id = b.id
            LEFT JOIN ledger_records l ON mm.side = 'ledger' AND mm.record_id = l.id
            WHERE mm.match_id = ?
            """, (mid,))
            members = cursor.fetchall()

            bank_m = [m for m in members if m["side"] == "bank"]
            ledger_m = [m for m in members if m["side"] == "ledger"]

            bank_sum = sum(m["amount_cents"] or 0 for m in bank_m)
            ledger_sum = sum(m["amount_cents"] or 0 for m in ledger_m)
            amount_delta = abs(bank_sum - ledger_sum)

            date_delta_days = 0
            if bank_m and ledger_m and bank_m[0]["date"] and ledger_m[0]["date"]:
                try:
                    d1 = datetime.strptime(bank_m[0]["date"][:10], "%Y-%m-%d")
                    d2 = datetime.strptime(ledger_m[0]["date"][:10], "%Y-%m-%d")
                    date_delta_days = abs((d1 - d2).days)
                except ValueError:
                    date_delta_days = 0

            ref_equal = False
            if bank_m and ledger_m:
                b_ref = (bank_m[0]["reference"] or "").strip().lower()
                l_ref = (ledger_m[0]["reference"] or "").strip().lower()
                if b_ref and l_ref and b_ref == l_ref:
                    ref_equal = True

            matches.append({
                "id": mid,
                "match_type": mr["match_type"],
                "rule_score": mr["rule_score"],
                "rule_score_description": "Deterministic heuristic rule score, not a statistical probability.",
                "status": mr["status"],
                "features": {
                    "amount_delta_cents": amount_delta,
                    "date_delta_days": date_delta_days,
                    "descriptor_similarity": SequenceMatcher(
                        None,
                        (bank_m[0]["description"] or "").casefold() if bank_m else "",
                        (ledger_m[0]["description"] or "").casefold() if ledger_m else ""
                    ).ratio() if bank_m and ledger_m else 0.0,
                    "reference_equal": ref_equal
                },
                "bank_records": [dict(m) for m in bank_m],
                "ledger_records": [dict(m) for m in ledger_m]
            })

        # Exceptions
        cursor.execute("""
        SELECT e.id, e.record_id, e.side, e.type AS reason, e.status,
               CASE WHEN e.side = 'bank' THEN b.amount_cents ELSE l.amount_cents END AS amount_cents,
               CASE WHEN e.side = 'bank' THEN b.date ELSE l.date END AS date,
               CASE WHEN e.side = 'bank' THEN b.description ELSE l.description END AS description,
               CASE WHEN e.side = 'bank' THEN b.reference ELSE l.reference END AS reference
        FROM exceptions e
        LEFT JOIN bank_records b ON e.side = 'bank' AND e.record_id = b.id
        LEFT JOIN ledger_records l ON e.side = 'ledger' AND e.record_id = l.id
        WHERE e.run_id = ?
        ORDER BY e.id ASC
        """, (run_id,))
        exceptions = [dict(r) for r in cursor.fetchall()]

        # Arithmetic Consistency Check
        cursor.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM bank_records WHERE run_id = ?", (run_id,))
        bank_confirmed_total = cursor.fetchone()[0]

        cursor.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM ledger_records WHERE run_id = ?", (run_id,))
        ledger_confirmed_total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM bank_records WHERE run_id = ?", (run_id,))
        bank_record_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM ledger_records WHERE run_id = ?", (run_id,))
        ledger_record_count = cursor.fetchone()[0]
        cursor.execute("""
        SELECT mm.side, COUNT(DISTINCT mm.record_id) AS count
        FROM match_members mm JOIN matches m ON m.match_id = mm.match_id
        WHERE m.run_id = ? GROUP BY mm.side
        """, (run_id,))
        matched_counts = {row["side"]: row["count"] for row in cursor.fetchall()}
        review_required_count = sum(
            1 for item in exceptions if str(item.get("status", "")).lower() in ("open", "pending_review", "needs_review")
        ) + sum(
            1 for item in matches if str(item.get("status", "")).lower() in ("pending_review", "needs_review")
        )
        amount_difference_cents = sum(
            int(item["features"].get("amount_delta_cents") or 0) for item in matches
        )

        bank_opening = run["bank_opening_cents"]
        bank_closing = run["bank_closing_cents"]
        ledger_opening = run["ledger_opening_cents"]
        ledger_closing = run["ledger_closing_cents"]
        bank_has_balance_control = bank_opening is not None and bank_closing is not None
        ledger_has_balance_control = ledger_opening is not None and ledger_closing is not None
        bank_computed_closing = bank_opening + bank_confirmed_total if bank_has_balance_control else None
        ledger_computed_closing = ledger_opening + ledger_confirmed_total if ledger_has_balance_control else None

        return {
            "run_id": run_id,
            "status": run["status"],
            "currency": run["currency"],
            "matches": matches,
            "exceptions": exceptions,
            "summary": {
                "bank_records": bank_record_count,
                "ledger_records": ledger_record_count,
                "matched_items": len(matches),
                "matched_bank_records": matched_counts.get("bank", 0),
                "matched_ledger_records": matched_counts.get("ledger", 0),
                "unmatched_bank_records": max(0, bank_record_count - matched_counts.get("bank", 0)),
                "unmatched_ledger_records": max(0, ledger_record_count - matched_counts.get("ledger", 0)),
                "review_required_items": review_required_count,
                "bank_net_activity_cents": bank_confirmed_total,
                "ledger_net_activity_cents": ledger_confirmed_total,
                "net_activity_difference_cents": bank_confirmed_total - ledger_confirmed_total,
                "matched_amount_difference_cents": amount_difference_cents,
            },
            "arithmetic_consistency_check": {
                "label": "Arithmetic Consistency Check (Not a Reconciliation Verdict)",
                "bank": {
                    "balance_control_available": bank_has_balance_control,
                    "opening_cents": bank_opening,
                    "confirmed_total_cents": bank_confirmed_total,
                    "computed_closing_cents": bank_computed_closing,
                    "user_closing_cents": bank_closing,
                    "difference_cents": bank_computed_closing - bank_closing if bank_has_balance_control else None
                },
                "ledger": {
                    "balance_control_available": ledger_has_balance_control,
                    "opening_cents": ledger_opening,
                    "confirmed_total_cents": ledger_confirmed_total,
                    "computed_closing_cents": ledger_computed_closing,
                    "user_closing_cents": ledger_closing,
                    "difference_cents": ledger_computed_closing - ledger_closing if ledger_has_balance_control else None
                }
            },
            "limitations": [
                "This workflow compares transaction rows and net activity only; it does not verify opening or closing account balances.",
                "Rule scores reflect heuristic matching thresholds, not probabilities.",
                "No generative AI models were invoked during reconciliation; matching is deterministic."
            ]
        }
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 10. CSV Export Generation (Formula Injection Protected)
# ----------------------------------------------------------------------

def export_matches_csv(run_id: str) -> str:
    """
    Generates CSV export of confirmed matches for a run with formula injection prevention.
    Prefixes any cell starting with = + - @ \\t \\r with a single quote.
    """
    results = get_run_results(run_id)
    matches = results.get("matches", [])

    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    # Header
    writer.writerow([
        "match_id",
        "match_type",
        "rule_score",
        "bank_record_ids",
        "ledger_record_ids",
        "amount_delta_cents",
        "date_delta_days",
        "reference_equal"
    ])

    for m in matches:
        b_ids = ";".join(r["record_id"] for r in m.get("bank_records", []))
        l_ids = ";".join(r["record_id"] for r in m.get("ledger_records", []))
        feats = m.get("features", {})

        row = [
            escape_csv_cell(m.get("id")),
            escape_csv_cell(m.get("match_type")),
            escape_csv_cell(str(m.get("rule_score"))),
            escape_csv_cell(b_ids),
            escape_csv_cell(l_ids),
            escape_csv_cell(str(feats.get("amount_delta_cents", 0))),
            escape_csv_cell(str(feats.get("date_delta_days", 0))),
            escape_csv_cell(str(feats.get("reference_equal", False)))
        ]
        writer.writerow(row)

    return output.getvalue()


# ----------------------------------------------------------------------
# 11. Run Lifecycle Management & Scoping
# ----------------------------------------------------------------------

def create_workflow_run(
    owner_user_id: str,
    name: str,
    currency: Optional[str] = None,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    bank_opening: Optional[str] = None,
    bank_closing: Optional[str] = None,
    ledger_opening: Optional[str] = None,
    ledger_closing: Optional[str] = None
) -> Dict[str, Any]:
    """
    Creates a draft transaction reconciliation. Only its short name is required;
    source files may supply optional currency and date-range metadata later.
    """
    start_time = time.perf_counter()
    curr = (currency or "").strip().upper() or None
    if curr and curr not in ALLOWED_CURRENCIES:
        raise ValueError(f"Currency '{currency}' is not supported. Must be one of: {', '.join(ALLOWED_CURRENCIES)}.")

    if not name or not name.strip():
        raise ValueError("Run name cannot be empty.")

    p_start = (period_start or "").strip() or None
    p_end = (period_end or "").strip() or None
    if bool(p_start) != bool(p_end):
        raise ValueError("Provide both period dates or leave both blank; uploads can detect the period.")
    for d_val, label in [(p_start, "period_start"), (p_end, "period_end")]:
        if d_val is None:
            continue
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d_val):
            raise ValueError(f"{label} must be formatted as YYYY-MM-DD.")
        try:
            datetime.strptime(d_val, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"{label} '{d_val}' is not a valid calendar date.")

    if p_start and p_end and p_start > p_end:
        raise ValueError(f"period_start ({p_start}) cannot be after period_end ({p_end}).")

    def optional_cents(value: Optional[str]) -> Optional[int]:
        if value is None or not str(value).strip():
            return None
        return parse_to_cents(str(value))

    bank_open_c = optional_cents(bank_opening)
    bank_close_c = optional_cents(bank_closing)
    ledger_open_c = optional_cents(ledger_opening)
    ledger_close_c = optional_cents(ledger_closing)

    run_id = f"run_{uuid.uuid4().hex[:12]}"

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO runs (
            id, owner_user_id, user_id, name, currency, period_start, period_end,
            bank_opening_cents, bank_closing_cents, ledger_opening_cents, ledger_closing_cents,
            status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (
            run_id, owner_user_id, owner_user_id, name.strip(), curr, p_start, p_end,
            bank_open_c, bank_close_c, ledger_open_c, ledger_close_c
        ))

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        write_audit_log(
            conn, owner_user_id, run_id, "create_run",
            {
                "run_id": run_id,
                "name": name.strip(),
                "currency": curr,
            "period_start": p_start,
            "period_end": p_end
            },
            duration_ms=duration_ms
        )

        conn.commit()

        return {
            "id": run_id,
            "owner_user_id": owner_user_id,
            "name": name.strip(),
            "currency": curr,
            "period_start": p_start,
            "period_end": p_end,
            "bank_opening_cents": bank_open_c,
            "bank_closing_cents": bank_close_c,
            "ledger_opening_cents": ledger_open_c,
            "ledger_closing_cents": ledger_close_c,
            "status": "draft"
        }
    finally:
        conn.close()


def create_sample_reconciliation(owner_user_id: str) -> Dict[str, Any]:
    """Create or return one clearly labeled, persisted bank/ledger sample run per user."""
    sample_name = "Sample reconciliation · Bank and ledger"
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM runs WHERE owner_user_id = ? AND name = ? ORDER BY created_at LIMIT 1",
            (owner_user_id, sample_name),
        ).fetchone()
    finally:
        conn.close()
    sample_dir = DATA_DIR.parent / "sample_data"
    bank_path = sample_dir / "bank_statement.csv"
    ledger_path = sample_dir / "ledger_statement.csv"
    bank_bytes = bank_path.read_bytes()
    ledger_bytes = ledger_path.read_bytes()
    bank_inspection = inspect_csv_document(bank_bytes, bank_path.name, "bank")
    ledger_inspection = inspect_csv_document(ledger_bytes, ledger_path.name, "ledger")
    period_start = min(bank_inspection["period_start"], ledger_inspection["period_start"])
    period_end = max(bank_inspection["period_end"], ledger_inspection["period_end"])

    run = get_workflow_run(existing["id"], owner_user_id, False) if existing else create_workflow_run(
        owner_user_id, sample_name, "USD", period_start, period_end
    )
    for side, path, content, inspection in (
        ("bank", bank_path, bank_bytes, bank_inspection),
        ("ledger", ledger_path, ledger_bytes, ledger_inspection),
    ):
        side_batch = next((batch for batch in run.get("batches", []) if batch["side"] == side and batch["status"] in ("staged", "confirmed")), None)
        if side_batch and side_batch["status"] == "confirmed":
            continue
        uploaded = {"batch_id": side_batch["id"]} if side_batch else ingest_raw_csv_upload(
            run["id"], side, path.name, content, owner_user_id,
            inspection["delimiter"],
        )
        mapping = dict(inspection["mapping"])
        if side == "ledger":
            mapping["ledger_perspective"] = "cash_account"
        parsed = map_and_process_import_batch(uploaded["batch_id"], mapping, owner_user_id)
        if parsed.get("error") or parsed.get("date_format_confirmation_required"):
            raise ValueError(parsed.get("message") or parsed.get("error") or "Sample date format needs confirmation.")
        confirmed = confirm_import_batch(uploaded["batch_id"], owner_user_id, "analyst")
        if confirmed.get("error"):
            raise ValueError("Sample import could not be confirmed: " + " ".join(confirmed.get("blocking_reasons", [])))

    if run["status"] != "reconciled":
        result = run_reconciliation_for_run(run["id"], owner_user_id)
        if result.get("error"):
            raise ValueError(result["error"])
    return get_workflow_run(run["id"], owner_user_id, False)


def get_workflow_run(run_id: str, requesting_user_id: str, is_admin: bool) -> Dict[str, Any]:
    """Retrieves run details. Returns 404 (KeyError) if not found or unauthorized."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        run = cursor.fetchone()
        if not run:
            raise KeyError(f"Run '{run_id}' not found.")

        owner = run["owner_user_id"] or run["user_id"]
        if not is_admin and owner != requesting_user_id:
            raise KeyError(f"Run '{run_id}' not found.")

        run_dict = dict(run)

        cursor.execute("""
        SELECT id, side, filename, sha256, size_bytes, status, date_format,
               row_count, total_records,
               created_at, confirmed_at, confirmed_by
        FROM import_batches
        WHERE run_id = ?
        ORDER BY side ASC
        """, (run_id,))
        batches = [dict(b) for b in cursor.fetchall()]
        run_dict["batches"] = batches

        return run_dict
    finally:
        conn.close()


def list_workflow_runs(requesting_user_id: str, is_admin: bool) -> List[Dict[str, Any]]:
    """Lists runs owned by requesting user (or all runs if admin)."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if is_admin:
            cursor.execute("SELECT * FROM runs ORDER BY created_at DESC")
        else:
            cursor.execute("""
            SELECT * FROM runs
            WHERE owner_user_id = ? OR user_id = ?
            ORDER BY created_at DESC
            """, (requesting_user_id, requesting_user_id))

        runs = []
        for r in cursor.fetchall():
            rd = dict(r)
            cursor.execute("SELECT side, filename, status, row_count, total_records, created_at, confirmed_at FROM import_batches WHERE run_id = ?", (rd["id"],))
            rd["batches"] = [dict(b) for b in cursor.fetchall()]
            runs.append(rd)
        return runs
    finally:
        conn.close()


def verify_batch_ownership(batch_id: str, requesting_user_id: str, is_admin: bool) -> Dict[str, Any]:
    """Verifies that requesting user owns the batch (or is admin). Returns batch or raises KeyError."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT b.*, r.owner_user_id AS run_owner
        FROM import_batches b
        JOIN runs r ON b.run_id = r.id
        WHERE b.id = ?
        """, (batch_id,))
        batch = cursor.fetchone()
        if not batch:
            raise KeyError(f"Batch '{batch_id}' not found.")

        batch_owner = batch["owner_user_id"] or batch["user_id"] or batch["run_owner"]
        if not is_admin and batch_owner != requesting_user_id:
            raise KeyError(f"Batch '{batch_id}' not found.")

        return dict(batch)
    finally:
        conn.close()
