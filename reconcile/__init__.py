"""
Deterministic Financial Reconciliation Engine Package.
Modules:
- normalize: descriptor normalization and reference extraction
- candidates: candidate blocking, feature extraction, bounded subset-sum
- baselines: B0 exact matcher, B1 fuzzy/date-drift/split matcher
- balance_proof: authoritative two-sided balance proof verification
- tune: dev-only hyperparameter tuning
"""

from reconcile.normalize import normalize_descriptor, extract_reference, analyze_descriptor
from reconcile.candidates import generate_candidate_pairs, find_bounded_subset_sum_n1, find_bounded_subset_sum_1n
from reconcile.baselines import reconcile_b0, reconcile_b1, load_reconcile_config
from reconcile.balance_proof import compute_balance_proof
from reconcile.validator import validate_adjudication_response, ValidationResult
from reconcile.adjudicator import (
    adjudicate_item,
    commit_adjudication_to_db,
    AdjudicationResponse,
    AdjudicationOutcome,
    build_adjudication_prompt
)
from reconcile.cache import get_llm_cache

__all__ = [
    "normalize_descriptor",
    "extract_reference",
    "analyze_descriptor",
    "generate_candidate_pairs",
    "find_bounded_subset_sum_n1",
    "find_bounded_subset_sum_1n",
    "reconcile_b0",
    "reconcile_b1",
    "load_reconcile_config",
    "compute_balance_proof",
    "validate_adjudication_response",
    "ValidationResult",
    "adjudicate_item",
    "commit_adjudication_to_db",
    "AdjudicationResponse",
    "AdjudicationOutcome",
    "build_adjudication_prompt",
    "get_llm_cache",
]
