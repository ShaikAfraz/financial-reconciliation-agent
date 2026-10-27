"""
Deterministic Validator for GenAI Reconciliation Adjudication.
Runs on EVERY LLM output before it can affect any downstream financial state.
Enforces:
1. Pydantic schema validity.
2. Selected IDs are a strict subset of supplied candidates.
3. Selection passes deterministic arithmetic re-check within tolerance (computed in integer cents).
4. Every evidence_ref points to a real supplied record and field.
5. Every number and ID in the rationale appears in the supplied evidence (anti-hallucination check).
Failure on ANY check forces decision to 'escalate' with an explicit rejection reason.
"""

import re
import hashlib
import hmac
from typing import Dict, Any, List, Set, Optional, Union
from pydantic import BaseModel, Field

VALIDATOR_SECRET = "reconcile-validator-pass-key-2026"

def generate_validator_token(target_id: str, selected_ids: List[str]) -> str:
    """
    Generates a deterministic cryptographic pass-token asserting that the match selection
    passed all deterministic schema, arithmetic, and evidence checks.
    Required by commit_match.
    """
    sorted_candidates = ",".join(sorted(selected_ids or []))
    payload = f"{VALIDATOR_SECRET}:{target_id}:{sorted_candidates}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

def verify_validator_token(token: str, target_id: str, selected_ids: List[str]) -> bool:
    """Verifies that a validator pass-token matches the target item and selected candidates."""
    if not token or not isinstance(token, str):
        return False
    expected = generate_validator_token(target_id, selected_ids)
    return hmac.compare_digest(token, expected)

class ValidationResult(BaseModel):
    is_valid: bool
    decision: str  # "select", "none", "escalate"
    selected_candidate_ids: List[str] = Field(default_factory=list)
    exception_type: str  # "timing_difference", "bank_fee", "duplicate", "amount_mismatch", "missing_record", "none"
    rationale: str
    evidence_refs: List[Dict[str, str]] = Field(default_factory=list)
    rejection_reason: Optional[str] = None
    arithmetic_delta_cents: int = 0
    validator_token: Optional[str] = None

def extract_numbers_and_ids_from_text(text: str) -> List[str]:
    """
    Extracts all alphanumeric identifiers (e.g. BANK-001, LEDGER-101, INV-88210)
    and numeric/monetary tokens (e.g. 125.00, $125.00, 500) from text.
    Removes alphanumeric IDs first so their internal digits are not treated as standalone numbers.
    """
    # Alphanumeric IDs with hyphens/underscores/digits (e.g. BANK-101, REF-2026, INV-883)
    id_pattern = r"\b[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+\b"
    ids = re.findall(id_pattern, text)

    # Strip identified IDs from text so digits inside IDs are not matched as standalone numbers
    text_without_ids = re.sub(id_pattern, " ", text)

    # Standalone numbers with optional dollar sign and decimals (e.g. $125.50, 125.50, 12500)
    num_pattern = r"\$?\b\d+(?:\.\d{1,2})?\b"
    nums = re.findall(num_pattern, text_without_ids)

    return ids + nums

def build_grounded_evidence_tokens(
    target_item: Dict[str, Any],
    candidate_records: List[Dict[str, Any]],
    deterministic_features: Optional[List[Dict[str, Any]]] = None
) -> Set[str]:
    """
    Constructs the authoritative whitelist of numbers, amounts, IDs, references,
    and tokens that exist in the supplied evidence.
    """
    grounded = set()

    # Small common prose integers allowed for counts and numbering
    prose_allowed = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}
    grounded.update(prose_allowed)

    all_records = [target_item] + list(candidate_records or [])
    for rec in all_records:
        rec_id = str(rec.get("id", "")).strip()
        if rec_id:
            grounded.add(rec_id.upper())
            grounded.add(rec_id)
            # Add component parts of the ID as well
            for part in re.split(r"[-_]", rec_id):
                if part:
                    grounded.add(part)
                    grounded.add(part.upper())

        ref = str(rec.get("reference", "")).strip()
        if ref and ref.lower() != "none":
            grounded.add(ref.upper())
            grounded.add(ref)
            for part in re.split(r"[-_]", ref):
                if part:
                    grounded.add(part)
                    grounded.add(part.upper())

        amt = rec.get("amount_cents")
        if amt is not None and isinstance(amt, int):
            grounded.add(str(amt))
            # Format variations
            dollars_2dec = f"{amt / 100:.2f}"
            dollars_clean = f"{amt / 100:.2f}".rstrip("0").rstrip(".") if amt % 100 == 0 else f"{amt / 100:.2f}"
            grounded.add(dollars_2dec)
            grounded.add(dollars_clean)
            grounded.add(f"${dollars_2dec}")
            grounded.add(f"${dollars_clean}")

        date_str = str(rec.get("date", "")).strip()
        if date_str:
            grounded.add(date_str)
            for part in date_str.split("-"):
                if part:
                    grounded.add(part)

        desc = str(rec.get("description", ""))
        for desc_token in extract_numbers_and_ids_from_text(desc):
            grounded.add(desc_token)
            grounded.add(desc_token.upper())
            grounded.add(desc_token.lstrip("$"))

    # Features: deltas and similarity numbers
    if deterministic_features:
        for feat in deterministic_features:
            amt_delta = feat.get("amount_delta_cents")
            if amt_delta is not None:
                grounded.add(str(amt_delta))
                d_dollars = f"{amt_delta / 100:.2f}"
                grounded.add(d_dollars)
                grounded.add(f"${d_dollars}")
            date_delta = feat.get("date_delta_days")
            if date_delta is not None:
                grounded.add(str(date_delta))

    return grounded

def verify_rationale_grounding(
    rationale: str,
    target_item: Dict[str, Any],
    candidate_records: Optional[List[Dict[str, Any]]] = None,
    deterministic_features: Optional[List[Dict[str, Any]]] = None
) -> Tuple[bool, List[str]]:
    """
    Verifies that every number, amount, date, and identifier in rationale
    strictly exists in the supplied evidence set.
    Returns (is_grounded, ungrounded_tokens).
    """
    grounded_tokens = build_grounded_evidence_tokens(target_item, candidate_records, deterministic_features)
    rationale_tokens = extract_numbers_and_ids_from_text(rationale)
    ungrounded = []

    for tok in rationale_tokens:
        tok_clean = tok.lstrip("$")
        tok_upper = tok.upper()
        is_grounded = (
            tok in grounded_tokens or
            tok_clean in grounded_tokens or
            tok_upper in grounded_tokens or
            f"${tok_clean}" in grounded_tokens
        )
        if not is_grounded:
            ungrounded.append(tok)

    return (len(ungrounded) == 0, ungrounded)


def validate_adjudication_response(
    response_obj: Any,
    target_item: Dict[str, Any],
    candidate_records: List[Dict[str, Any]],
    deterministic_features: Optional[List[Dict[str, Any]]] = None,
    fee_tolerance_cents: int = 1000
) -> ValidationResult:
    """
    Deterministic validator running on every GenAI adjudication output.
    Returns ValidationResult. If invalid, forces decision='escalate'.
    """
    from reconcile.adjudicator import AdjudicationResponse

    # 1. Schema Validation Check
    if isinstance(response_obj, AdjudicationResponse):
        parsed = response_obj
    elif isinstance(response_obj, dict):
        try:
            parsed = AdjudicationResponse.model_validate(response_obj)
        except Exception as e:
            return ValidationResult(
                is_valid=False,
                decision="escalate",
                selected_candidate_ids=[],
                exception_type="amount_mismatch",
                rationale="REJECTED: Schema validation error.",
                evidence_refs=[],
                rejection_reason=f"Schema validation error: {e}"
            )
    elif isinstance(response_obj, str):
        try:
            parsed = AdjudicationResponse.model_validate_json(response_obj)
        except Exception as e:
            return ValidationResult(
                is_valid=False,
                decision="escalate",
                selected_candidate_ids=[],
                exception_type="amount_mismatch",
                rationale="REJECTED: Malformed JSON or invalid schema.",
                evidence_refs=[],
                rejection_reason=f"Malformed JSON: {e}"
            )
    else:
        return ValidationResult(
            is_valid=False,
            decision="escalate",
            selected_candidate_ids=[],
            exception_type="amount_mismatch",
            rationale="REJECTED: Unrecognized response type.",
            evidence_refs=[],
            rejection_reason=f"Unrecognized response type: {type(response_obj).__name__}"
        )

    decision = str(parsed.decision.value if hasattr(parsed.decision, "value") else parsed.decision).lower()
    selected_ids = list(parsed.selected_candidate_ids or [])
    exception_type = str(parsed.exception_type.value if hasattr(parsed.exception_type, "value") else parsed.exception_type).lower()
    rationale = parsed.rationale or ""
    evidence_refs = [
        {"record_id": ref.record_id, "field": ref.field}
        if hasattr(ref, "record_id") else ref
        for ref in (parsed.evidence_refs or [])
    ]

    candidate_map = {c["id"]: c for c in candidate_records}
    valid_candidate_ids = set(candidate_map.keys())
    target_id = target_item["id"]

    # 2. Candidate ID Subset Check
    if decision == "select":
        if not selected_ids:
            return ValidationResult(
                is_valid=False,
                decision="escalate",
                selected_candidate_ids=[],
                exception_type=exception_type,
                rationale=rationale,
                evidence_refs=evidence_refs,
                rejection_reason="Decision is 'select' but selected_candidate_ids is empty."
            )
        out_of_pool = set(selected_ids) - valid_candidate_ids
        if out_of_pool:
            return ValidationResult(
                is_valid=False,
                decision="escalate",
                selected_candidate_ids=[],
                exception_type=exception_type,
                rationale=rationale,
                evidence_refs=evidence_refs,
                rejection_reason=f"Selected candidate IDs {sorted(list(out_of_pool))} are not in the supplied candidates."
            )
    elif decision in ("none", "escalate"):
        if selected_ids:
            return ValidationResult(
                is_valid=False,
                decision="escalate",
                selected_candidate_ids=[],
                exception_type=exception_type,
                rationale=rationale,
                evidence_refs=evidence_refs,
                rejection_reason=f"Decision is '{decision}' but selected_candidate_ids is non-empty."
            )

    # 3. Deterministic Arithmetic Re-check (Computed in integer cents)
    target_cents = target_item.get("amount_cents")
    if not isinstance(target_cents, int) or isinstance(target_cents, bool):
        return ValidationResult(
            is_valid=False,
            decision="escalate",
            selected_candidate_ids=[],
            exception_type="amount_mismatch",
            rationale=rationale,
            evidence_refs=evidence_refs,
            rejection_reason="Target item amount_cents must be integer cents."
        )

    delta_cents = 0
    if decision == "select":
        selected_sum_cents = 0
        for cid in selected_ids:
            c_rec = candidate_map[cid]
            c_amt = c_rec.get("amount_cents")
            if not isinstance(c_amt, int) or isinstance(c_amt, bool):
                return ValidationResult(
                    is_valid=False,
                    decision="escalate",
                    selected_candidate_ids=[],
                    exception_type="amount_mismatch",
                    rationale=rationale,
                    evidence_refs=evidence_refs,
                    rejection_reason=f"Candidate {cid} amount_cents is not integer cents."
                )
            selected_sum_cents += c_amt

        delta_cents = abs(target_cents - selected_sum_cents)

        if exception_type in ("none", "duplicate"):
            if delta_cents != 0:
                return ValidationResult(
                    is_valid=False,
                    decision="escalate",
                    selected_candidate_ids=[],
                    exception_type="amount_mismatch",
                    rationale=rationale,
                    evidence_refs=evidence_refs,
                    rejection_reason=(
                        f"Arithmetic check failed: target {target_cents} cents != selected sum "
                        f"{selected_sum_cents} cents (delta={delta_cents} cents, expected 0)."
                    ),
                    arithmetic_delta_cents=delta_cents
                )
        elif exception_type == "bank_fee":
            if delta_cents > fee_tolerance_cents:
                return ValidationResult(
                    is_valid=False,
                    decision="escalate",
                    selected_candidate_ids=[],
                    exception_type="amount_mismatch",
                    rationale=rationale,
                    evidence_refs=evidence_refs,
                    rejection_reason=(
                        f"Arithmetic check failed for bank_fee: discrepancy {delta_cents} cents exceeds "
                        f"configured fee tolerance of {fee_tolerance_cents} cents."
                    ),
                    arithmetic_delta_cents=delta_cents
                )
        elif exception_type == "amount_mismatch":
            if delta_cents > fee_tolerance_cents:
                return ValidationResult(
                    is_valid=False,
                    decision="escalate",
                    selected_candidate_ids=[],
                    exception_type="amount_mismatch",
                    rationale=rationale,
                    evidence_refs=evidence_refs,
                    rejection_reason=(
                        f"Arithmetic check failed for amount_mismatch: discrepancy {delta_cents} cents exceeds "
                        f"tolerance of {fee_tolerance_cents} cents."
                    ),
                    arithmetic_delta_cents=delta_cents
                )

    # 4. Evidence References Existence Check
    valid_record_ids = {target_id} | valid_candidate_ids
    valid_fields = {"amount_cents", "date", "description", "reference", "id"}

    if decision == "select" and not evidence_refs:
        return ValidationResult(
            is_valid=False,
            decision="escalate",
            selected_candidate_ids=[],
            exception_type=exception_type,
            rationale=rationale,
            evidence_refs=[],
            rejection_reason="Decision is 'select' but evidence_refs is empty."
        )

    for ref in evidence_refs:
        r_id = ref.get("record_id")
        r_field = ref.get("field")
        if not r_id or r_id not in valid_record_ids:
            return ValidationResult(
                is_valid=False,
                decision="escalate",
                selected_candidate_ids=[],
                exception_type=exception_type,
                rationale=rationale,
                evidence_refs=evidence_refs,
                rejection_reason=f"Evidence ref references non-existent record ID '{r_id}'."
            )
        if not r_field or r_field not in valid_fields:
            return ValidationResult(
                is_valid=False,
                decision="escalate",
                selected_candidate_ids=[],
                exception_type=exception_type,
                rationale=rationale,
                evidence_refs=evidence_refs,
                rejection_reason=f"Evidence ref references invalid field '{r_field}' on record '{r_id}'."
            )

    # 5. Anti-Hallucination Grounding Check (every number/ID in rationale must be in evidence)
    is_grounded, ungrounded_tokens = verify_rationale_grounding(
        rationale=rationale,
        target_item=target_item,
        candidate_records=candidate_records,
        deterministic_features=deterministic_features
    )
    if not is_grounded:
        return ValidationResult(
            is_valid=False,
            decision="escalate",
            selected_candidate_ids=[],
            exception_type=exception_type,
            rationale=rationale,
            evidence_refs=evidence_refs,
            rejection_reason=f"Rationale contains ungrounded number or fabricated ID '{ungrounded_tokens[0]}' not in supplied evidence."
        )

    # All checks passed!
    token = generate_validator_token(target_id, selected_ids) if decision == "select" else None
    return ValidationResult(
        is_valid=True,
        decision=decision,
        selected_candidate_ids=selected_ids,
        exception_type=exception_type,
        rationale=rationale,
        evidence_refs=evidence_refs,
        rejection_reason=None,
        arithmetic_delta_cents=delta_cents,
        validator_token=token
    )
