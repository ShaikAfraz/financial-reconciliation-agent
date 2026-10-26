"""
Tool 4: calculate_category_variance
Calculates monthly budget vs. actual spending variance across all categories using SQLite aggregations.
"""

from typing import Dict, Any, List
from data.db import get_connection

def calculate_category_variance(month: str = "2026-08") -> Dict[str, Any]:
    """
    Computes budget versus actual spending variance for all categories in the specified month.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT 
        b.category,
        b.monthly_limit,
        b.category_type,
        b.flexibility_weight,
        COALESCE(SUM(t.amount), 0.0) as actual_spend
    FROM budget_rules b
    LEFT JOIN transactions t 
        ON b.category = t.category 
        AND strftime('%Y-%m', t.date) = ?
    GROUP BY b.category
    ORDER BY (COALESCE(SUM(t.amount), 0.0) - b.monthly_limit) DESC
    """, (month,))

    rows = cursor.fetchall()
    conn.close()

    category_details = []
    total_deficits = 0.0
    total_surpluses = 0.0
    deficit_categories = []
    surplus_categories = []
    total_budget = 0.0
    total_actual = 0.0

    for r in rows:
        cat = r["category"]
        limit = float(r["monthly_limit"])
        actual = round(float(r["actual_spend"]), 2)
        variance = round(actual - limit, 2)
        variance_pct = round(((actual - limit) / limit) * 100, 1) if limit > 0 else 0.0

        total_budget += limit
        total_actual += actual

        if variance > 0.01:
            status = "DEFICIT"
            total_deficits += variance
            deficit_categories.append(cat)
        elif variance < -0.01:
            status = "SURPLUS"
            total_surpluses += abs(variance)
            surplus_categories.append(cat)
        else:
            status = "BALANCED"

        category_details.append({
            "category": cat,
            "category_type": r["category_type"],
            "flexibility_weight": float(r["flexibility_weight"]),
            "budget_limit": limit,
            "actual_spend": actual,
            "variance_amount": variance,
            "variance_pct": variance_pct,
            "status": status,
            "surplus_headroom": max(0.0, round(limit - actual, 2)),
            "deficit_overage": max(0.0, round(actual - limit, 2))
        })

    net_variance = round(total_actual - total_budget, 2)

    return {
        "month": month,
        "total_budget": round(total_budget, 2),
        "total_actual": round(total_actual, 2),
        "net_variance": net_variance,
        "total_deficits": round(total_deficits, 2),
        "total_surpluses": round(total_surpluses, 2),
        "deficit_categories": deficit_categories,
        "surplus_categories": surplus_categories,
        "category_variances": category_details,
        "explainable_summary": (
            f"Month {month}: Net variance ${net_variance:+.2f}. "
            f"{len(deficit_categories)} categories in deficit (${total_deficits:.2f}), "
            f"{len(surplus_categories)} categories with surplus (${total_surpluses:.2f})."
        )
    }
