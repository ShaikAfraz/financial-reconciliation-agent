"""
Tool 2: check_against_budget_rule
Queries the SQLite ledger database to evaluate cumulative category spend against defined monthly limits.
"""

from typing import Dict, Any
from data.db import get_connection

def check_against_budget_rule(category: str, additional_spend: float = 0.0, month: str = "2026-08") -> Dict[str, Any]:
    """
    Checks the cumulative spending for a category in a given month (plus any incoming additional_spend)
    against the budget limits stored in the SQLite database.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Fetch budget rule for category
    cursor.execute("""
    SELECT category, monthly_limit, category_type, flexibility_weight, description
    FROM budget_rules WHERE category = ?
    """, (category,))
    rule = cursor.fetchone()

    if not rule:
        conn.close()
        return {
            "error": f"Unknown category '{category}'.",
            "status": "ERROR"
        }

    monthly_limit = float(rule["monthly_limit"])
    cat_type = rule["category_type"]
    flex_weight = float(rule["flexibility_weight"])

    # 2. Query actual spend in SQLite
    cursor.execute("""
    SELECT COALESCE(SUM(amount), 0.0) AS total_actual
    FROM transactions
    WHERE category = ? AND strftime('%Y-%m', date) = ?
    """, (category, month))
    row = cursor.fetchone()
    existing_spend = round(float(row["total_actual"]), 2) if row else 0.0
    conn.close()

    total_spend = round(existing_spend + float(additional_spend), 2)
    utilization_pct = round((total_spend / monthly_limit) * 100, 1) if monthly_limit > 0 else 0.0

    overage = max(0.0, round(total_spend - monthly_limit, 2))
    remaining = max(0.0, round(monthly_limit - total_spend, 2))

    if total_spend > monthly_limit:
        status = "EXCEEDED"
        msg = f"{category} budget is EXCEEDED by ${overage:.2f} ({utilization_pct}% of ${monthly_limit:.2f} cap)."
    elif utilization_pct >= 80.0:
        status = "WARNING"
        msg = f"{category} budget is at WARNING level: ${total_spend:.2f} of ${monthly_limit:.2f} used ({utilization_pct}%)."
    else:
        status = "OK"
        msg = f"{category} budget is HEALTHY: ${total_spend:.2f} of ${monthly_limit:.2f} used ({utilization_pct}%)."

    return {
        "category": category,
        "month": month,
        "existing_spend": existing_spend,
        "additional_spend": round(float(additional_spend), 2),
        "total_spend": total_spend,
        "budget_limit": monthly_limit,
        "utilization_pct": utilization_pct,
        "status": status,
        "remaining_budget": remaining,
        "overage_amount": overage,
        "category_type": cat_type,
        "flexibility_weight": flex_weight,
        "explainable_summary": msg
    }
