"""
Tool 1: categorize_transaction
Assigns a spending category to a transaction by parsing and normalizing raw statement
descriptors, applying category taxonomy matching with fuzzy confidence scoring.
"""

import re
from difflib import SequenceMatcher
from typing import Dict, Any

CATEGORY_TAXONOMY = {
    "Housing": {
        "merchants": ["equity residential", "greystar", "landlord", "rent payment", "mortgage", "apartments"],
        "keywords": ["rent", "mortgage", "lease", "property tax"]
    },
    "Utilities": {
        "merchants": ["conedison", "national grid", "verizon fios", "city water", "duke energy", "pge", "at&t", "spectrum"],
        "keywords": ["electric", "power grid", "gas web pmt", "broadband", "fiber", "water board", "utility", "elec ach"]
    },
    "Groceries": {
        "merchants": ["trader joe's", "whole foods", "wholefds", "costco", "farmers market", "safeway", "kroger", "aldi"],
        "keywords": ["grocery", "supermarket", "produce", "pantry", "bulk restock", "whse"]
    },
    "Dining": {
        "merchants": ["sweetgreen", "chipotle", "blue bottle coffee", "blue bottle", "trattoria bella", "shake shack", "joe's pizza", "le bernardin", "starbucks"],
        "keywords": ["coffee", "pour-over", "cafe", "dinner", "lunch", "restaurant", "pizza", "takeout", "burger", "bistro"]
    },
    "Transport": {
        "merchants": ["mta subway", "mta nyct", "mta", "uber", "lyft", "shell oil", "shell", "exxon", "chevron", "amtrak"],
        "keywords": ["subway", "rideshare", "trip", "fuel", "gas station", "pump", "paygo", "metro", "transit", "fare"]
    },
    "Healthcare": {
        "merchants": ["cvs pharmacy", "cvs", "walgreens", "quest diagnostics", "citymd", "labcorp", "duane reade"],
        "keywords": ["pharmacy", "copay", "prescription", "urgent care", "clinic", "medical", "health"]
    },
    "Entertainment": {
        "merchants": ["netflix", "spotify", "amc theatres", "steam games", "steam", "adobe", "playstation", "hulu", "disney"],
        "keywords": ["streaming", "subscription", "cinema", "movie", "gaming", "creative cloud", "entertainment"]
    },
    "Shopping": {
        "merchants": ["amazon", "amzn", "target", "uniqlo", "apple store", "best buy", "bitpay", "walmart", "zara"],
        "keywords": ["retail", "apparel", "clothing", "electronics", "hardware", "crypto", "laptop", "online order"]
    }
}

def normalize_descriptor(raw: str) -> str:
    """Strips bank clearing prefixes, store IDs, POS codes, and trailing states."""
    s = (raw or "").strip()
    # Strip common payment prefixes
    s = re.sub(r'^(?:SQ\s*\*|TST\*\s*|AMZN\s+Mktp\s+US\*|PAYPAL\s*\*|MTA\*NYCT\s+)', '', s, flags=re.IGNORECASE)
    # Strip store IDs like #542, T-2849, 042, etc.
    s = re.sub(r'#?\b[T0-9]{3,8}\b', '', s)
    # Strip trailing phone numbers and state codes
    s = re.sub(r'\b\d{3}[-\.]?\d{3}[-\.]?\d{4}\b', '', s)
    s = re.sub(r'\b(?:NY|NJ|CA|WA|CL|NYC|USA|ACH|PMT|WEB)\b', '', s, flags=re.IGNORECASE)
    # Clean punctuation and extra spaces
    s = re.sub(r'[*_\-/]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def categorize_transaction(merchant: str, description: str = "", amount: float = 0.0) -> Dict[str, Any]:
    """
    Categorizes a transaction based on normalized merchant name, description keywords,
    and fuzzy similarity scoring against category taxonomies.
    """
    raw_input = merchant or description or ""
    if not raw_input.strip():
        return {
            "category": "Uncategorized",
            "clean_merchant": "Unknown",
            "confidence": 0.0,
            "matched_pattern": None,
            "is_ambiguous": True,
            "explainable_reasoning": "Missing merchant and description. Transaction cannot be categorized."
        }

    clean_name = normalize_descriptor(raw_input)
    clean_lower = clean_name.lower()
    full_lower = f"{raw_input} {description}".lower()

    best_cat = None
    best_confidence = 0.0
    best_pattern = None

    for cat, rules in CATEGORY_TAXONOMY.items():
        # 1. Exact / Substring merchant matches
        for km in rules["merchants"]:
            if km in clean_lower or km in full_lower:
                conf = 0.95
                if conf > best_confidence:
                    best_confidence = conf
                    best_cat = cat
                    best_pattern = f"Known merchant '{km}'"

            # Fuzzy similarity
            ratio = SequenceMatcher(None, clean_lower, km).ratio()
            if ratio > 0.82 and ratio > best_confidence:
                best_confidence = round(ratio, 2)
                best_cat = cat
                best_pattern = f"Fuzzy similarity ({int(ratio*100)}%) with '{km}'"

        # 2. Keyword matches
        for kw in rules["keywords"]:
            if kw in full_lower:
                conf = 0.85
                if conf > best_confidence:
                    best_confidence = conf
                    best_cat = cat
                    best_pattern = f"Keyword match '{kw}'"

    # Ambiguity check
    if not best_cat or best_confidence < 0.60:
        return {
            "category": "Shopping",
            "clean_merchant": clean_name or "Unknown Merchant",
            "confidence": 0.50,
            "matched_pattern": "Fallback general category",
            "is_ambiguous": True,
            "explainable_reasoning": f"Descriptor '{raw_input}' has ambiguous merchant patterns with low similarity (< 60%). Assigned to Shopping pending review."
        }

    return {
        "category": best_cat,
        "clean_merchant": clean_name,
        "confidence": best_confidence,
        "matched_pattern": best_pattern,
        "is_ambiguous": False,
        "explainable_reasoning": f"Normalized '{clean_name}' matched {best_pattern} for '{best_cat}' with {int(best_confidence*100)}% confidence."
    }
