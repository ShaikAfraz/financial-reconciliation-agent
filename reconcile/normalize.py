"""
Descriptor Normalization & Reference Extraction Module for Two-Sided Reconciliation.
Cleans raw bank statement descriptors and ERP ledger strings to isolate core vendor tokens
and extract authoritative transaction references.
"""

import re
from typing import Optional, List, Dict, Any

# Banking & processor noise prefixes (ordered longest match first)
PREFIX_PATTERNS = [
    r"^PURCHASE\s+AUTHORIZED\s+ON\s+",
    r"^DEBIT\s+CRD\s+WDL\s+",
    r"^DEBIT\s+CARD\s+",
    r"^PAYROLL\s+SVC\s+",
    r"^DIRECT\s+DEP\s+",
    r"^NET\s+DEP\s+",
    r"^POS\s+DEBIT\s*[-–—:]\s*",
    r"^POS\s+DEBIT\s+",
    r"^POS\s+PURCHASE\s+",
    r"^POS\s+",
    r"^ACH\s+PAYMENT\s*[-–—:]\s*",
    r"^ACH\s+PAYMENT\s+",
    r"^ACH\s+PMT\s*[-–—:]\s*",
    r"^ACH\s+PMT\s+",
    r"^ACH\s+DEP\s+",
    r"^ACH\s+",
    r"^WIRE\s+TRANSFER\s*[-–—:]\s*",
    r"^WIRE\s+TRF\s*[-–—:]\s*",
    r"^WIRE\s+TRF\s+",
    r"^WIRE\s+",
    r"^ONLINE\s+PMT\s*[-–—:]\s*",
    r"^ONLINE\s+PMT\s+",
    r"^TST\*\s*",
    r"^SQ\s*\*\s*",
    r"^CHECK\s*#?\s*",
    r"^CHK\s*#?\s*"
]

# Noise suffixes: store numbers, terminal IDs, date stamps (e.g. 08/12), state codes
NOISE_SUFFIX_PATTERNS = [
    r"\s+#\d+",
    r"\s+STORE\s+\d+",
    r"\s+TERM(?:INAL)?[- ]?\d+",
    r"\s+\*\d{3,5}",
    r"\s+\d{2}/\d{2}(?:/\d{2,4})?",  # Dates like 08/12 or 08/12/2026
    r"\s+[A-Z]{2}\b(?:\s+\d{5})?$",  # State codes like NY or CA at end
    r"\s+8\d{2}[-.]?\d{3}[-.]?\d{4}\b",  # Phone numbers
]

# Explicit reference pattern extractors
REFERENCE_PATTERNS = [
    r"\b(INV(?:OICE)?[-# ]?[A-Z0-9-]+)\b",
    r"\b(REF[-# ]?[A-Z0-9-]+)\b",
    r"\b(PO[-# ]?[A-Z0-9-]+)\b",
    r"\b(SPLIT[-# ]?[A-Z0-9-]+)\b",
    r"\b(FEE[-# ]?[A-Z0-9-]+)\b",
    r"\b(DUP[-# ]?[A-Z0-9-]+)\b",
    r"\b(CHECK[-# ]?\d+)\b",
    r"\b(CHK[-# ]?\d+)\b",
]

def extract_reference(text: str) -> Optional[str]:
    """
    Extracts an explicit invoice, purchase order, check, or tracking reference from text.
    Standardizes whitespace and delimiters (e.g. 'INV 88210' -> 'INV-88210').
    """
    if not text:
        return None

    clean_text = text.strip()
    for pattern in REFERENCE_PATTERNS:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            raw_ref = match.group(1).upper()
            # Normalize whitespace/delimiters
            normalized_ref = re.sub(r"[\s#]+", "-", raw_ref)
            normalized_ref = re.sub(r"-+", "-", normalized_ref)
            return normalized_ref
    return None

def normalize_descriptor(text: str) -> str:
    """
    Normalizes a descriptor by:
    1. Stripping standard processor prefixes (POS, ACH, WIRE, SQ*, TST*, etc.)
    2. Removing digits noise (phone numbers, terminal codes, store numbers, trailing dates)
    3. Stripping punctuation and extra whitespace
    4. Converting to uppercase
    """
    if not text:
        return ""

    s = text.strip()

    # 1. Strip known prefixes (case-insensitive)
    for pat in PREFIX_PATTERNS:
        s = re.sub(pat, "", s, flags=re.IGNORECASE)

    # 2. Strip phone numbers (e.g. 800-837-4966, 212-555-0199)
    s = re.sub(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", " ", s)

    # 3. Strip noise suffixes
    for pat in NOISE_SUFFIX_PATTERNS:
        s = re.sub(pat, " ", s, flags=re.IGNORECASE)

    # 3. Replace non-alphanumeric punctuation with spaces (preserve alphanumeric)
    s = re.sub(r"[^A-Za-z0-9\s]", " ", s)

    # 4. Collapse whitespace and uppercase
    tokens = [t.upper() for t in s.split() if t]
    return " ".join(tokens)

def analyze_descriptor(text: str) -> Dict[str, Any]:
    """
    Returns structured analysis of a descriptor:
    - raw: original string
    - normalized: normalized vendor string
    - reference: extracted reference (if any)
    - tokens: list of clean uppercase tokens
    """
    norm = normalize_descriptor(text)
    ref = extract_reference(text)
    return {
        "raw": text,
        "normalized": norm,
        "reference": ref,
        "tokens": norm.split()
    }
