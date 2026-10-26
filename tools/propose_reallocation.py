"""
Tool 5: propose_reallocation
Given variance data, calculates a mathematically sound, priority-constrained budget
rebalancing plan from surplus discretionary categories to offset deficit categories.
"""

from typing import Dict, Any, Optional
from tools.calculate_category_variance import calculate_category_variance
from data.db import get_connection

def propose_reallocation(month: str = "2026-08", variance_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Proposes an explainable budget reallocation plan to balance over-budget categories.
    - Prioritizes transfers from highly flexible discretionary pools (Entertainment, Dining, Shopping).
    - Strictly protects non-discretionary necessities (Housing, Utilities, Healthcare).
    - Flags uncovered deficits when available discretionary surpluses are exhausted.
    """
    if not variance_data or "category_variances" not in variance_data:
        variance_data = calculate_category_variance(month)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT category, category_type, flexibility_weight FROM budget_rules")
    rules_rows = cursor.fetchall()
    conn.close()

    meta = {r["category"]: {"type": r["category_type"], "flex": float(r["flexibility_weight"])} for r in rules_rows}

    # Separate deficits and eligible surpluses
    deficits = {}
    surpluses = {}

    for item in variance_data.get("category_variances", []):
        cat = item["category"]
        if item["status"] == "DEFICIT" and item["deficit_overage"] > 0.0:
            deficits[cat] = item["deficit_overage"]
        elif item["status"] == "SURPLUS" and item["surplus_headroom"] > 0.0:
            cat_type = meta.get(cat, {}).get("type", "discretionary")
            # Only discretionary & semi-discretionary categories can fund reallocations
            if cat_type != "non_discretionary":
                surpluses[cat] = item["surplus_headroom"]

    if not deficits:
        return {
            "month": month,
            "is_fully_covered": True,
            "reallocations": [],
            "total_deficit_absorbed": 0.0,
            "remaining_uncovered_deficit": 0.0,
            "recommendation_summary": f"All categories in {month} are within budget limits or in surplus. No reallocations required.",
            "post_reallocation_status": "BALANCED_HEALTHY"
        }

    total_deficit_needed = sum(deficits.values())

    # Sort donors by flexibility descending (highest flexibility cut first)
    sorted_donors = sorted(
        surpluses.keys(),
        key=lambda c: meta.get(c, {}).get("flex", 0.0),
        reverse=True
    )

    reallocations = []
    absorbed_amount = 0.0
    remaining_deficits = dict(deficits)
    remaining_surpluses = dict(surpluses)

    for def_cat, def_amt in list(remaining_deficits.items()):
        needed = def_amt
        for don_cat in sorted_donors:
            avail = remaining_surpluses.get(don_cat, 0.0)
            if avail <= 0.01 or needed <= 0.01:
                continue

            transfer_amt = min(needed, avail)
            transfer_amt = round(transfer_amt, 2)

            flex_val = meta.get(don_cat, {}).get("flex", 1.0)
            reallocations.append({
                "from_category": don_cat,
                "to_category": def_cat,
                "amount": transfer_amt,
                "rationale": (
                    f"Transfer ${transfer_amt:.2f} surplus from discretionary '{don_cat}' (flexibility weight: {flex_val}) "
                    f"to offset overage in '{def_cat}'."
                )
            })

            needed = round(needed - transfer_amt, 2)
            remaining_surpluses[don_cat] = round(remaining_surpluses[don_cat] - transfer_amt, 2)
            absorbed_amount = round(absorbed_amount + transfer_amt, 2)

        remaining_deficits[def_cat] = needed

    remaining_uncovered = round(sum(remaining_deficits.values()), 2)
    is_fully_covered = remaining_uncovered <= 0.01

    if is_fully_covered:
        summary = (
            f"Balanced all category deficits for {month}. "
            f"Reallocated ${absorbed_amount:.2f} across {len(reallocations)} transfers from surplus pools "
            f"({', '.join(sorted_donors)}) to offset deficits in ({', '.join(deficits.keys())})."
        )
    else:
        summary = (
            f"Partially reallocated ${absorbed_amount:.2f} across {len(reallocations)} transfers. "
            f"An uncovered deficit of ${remaining_uncovered:.2f} remains in {list(remaining_deficits.keys())} "
            f"because essential categories (Housing, Utilities, Healthcare) are strictly protected from budget cuts."
        )

    return {
        "month": month,
        "is_fully_covered": is_fully_covered,
        "initial_total_deficit": round(total_deficit_needed, 2),
        "total_deficit_absorbed": absorbed_amount,
        "remaining_uncovered_deficit": remaining_uncovered,
        "reallocations": reallocations,
        "remaining_deficits_by_category": remaining_deficits,
        "remaining_surpluses_by_category": remaining_surpluses,
        "recommendation_summary": summary
    }
