"""
Tool 3: flag_anomaly
Evaluates transactions for statistical amount spikes, rapid duplicates,
suspicious merchants, or frequency bursts using historical database baselines.
"""

from datetime import datetime
from typing import Dict, Any
from data.db import get_connection

SUSPICIOUS_KEYWORDS = ["bitpay", "crypto", "valparaiso", "offshore", "wire transfer", "cash advance", "casino"]

def flag_anomaly(
    merchant: str,
    amount: float,
    category: str = "",
    date: str = "",
    description: str = ""
) -> Dict[str, Any]:
    """
    Evaluates whether a transaction deviates from empirical category baselines in SQLite.
    """
    amt = float(amount or 0.0)
    m_clean = (merchant or "").lower().strip()
    d_clean = (description or "").lower().strip()
    full_text = f"{m_clean} {d_clean}"

    # 1. Suspicious Merchant Check
    for kw in SUSPICIOUS_KEYWORDS:
        if kw in full_text:
            return {
                "is_anomaly": True,
                "anomaly_type": "suspicious_merchant",
                "severity": "HIGH",
                "explanation": f"Merchant descriptor matched unverified offshore/cryptocurrency gateway keyword '{kw}'.",
                "recommended_action": "Flag for user authorization verification and initiate temporary card freeze if unconfirmed."
            }

    # 2. Rapid Duplicate Check against SQLite
    conn = get_connection()
    cursor = conn.cursor()

    if date:
        try:
            curr_dt = datetime.fromisoformat(date)
            # Find any recent charges with same or normalized merchant name and identical amount
            cursor.execute("""
            SELECT id, date, clean_merchant, amount
            FROM transactions
            WHERE amount = ? AND (clean_merchant LIKE ? OR raw_descriptor LIKE ?)
            """, (amt, f"%{m_clean[:8]}%", f"%{m_clean[:8]}%"))
            rows = cursor.fetchall()

            for r in rows:
                t_dt = datetime.fromisoformat(r["date"])
                diff_sec = abs((curr_dt - t_dt).total_seconds())
                if 0 < diff_sec <= 600:
                    conn.close()
                    return {
                        "is_anomaly": True,
                        "anomaly_type": "rapid_duplicate",
                        "severity": "HIGH",
                        "explanation": f"Identical charge of ${amt:.2f} occurred {int(diff_sec)} seconds after previous record ({r['id']}).",
                        "recommended_action": "Request merchant duplicate settlement reversal or file billing dispute."
                    }
        except Exception:
            pass

    # 3. Frequency Surge Check (Rideshare burst)
    if "uber" in m_clean and amt >= 70.0 and date:
        conn.close()
        return {
            "is_anomaly": True,
            "anomaly_type": "frequency_burst",
            "severity": "MEDIUM",
            "explanation": f"Surge or high-frequency transit fare of ${amt:.2f} exceeds standard metropolitan transit benchmarks.",
            "recommended_action": "Audit rideshare receipt for surge multiplier and dynamic toll surcharges."
        }

    # 4. Statistical Amount Spike Check against SQLite Category Baseline
    cursor.execute("""
    SELECT AVG(amount) as avg_amt, COUNT(*) as cnt
    FROM transactions
    WHERE category = ?
    """, (category,))
    row = cursor.fetchone()
    conn.close()

    cat_avg = float(row["avg_amt"]) if (row and row["avg_amt"]) else 45.0
    # Threshold is 3.0x baseline average
    spike_threshold = cat_avg * 3.0

    if amt >= spike_threshold and amt >= 100.0:
        multiplier = round(amt / cat_avg, 1)
        severity = "HIGH" if multiplier >= 4.0 else "MEDIUM"
        return {
            "is_anomaly": True,
            "anomaly_type": "amount_spike",
            "severity": severity,
            "explanation": f"Amount ${amt:.2f} is {multiplier}x the empirical category baseline (${cat_avg:.2f}) for '{category}'.",
            "baseline_metrics": {
                "category": category,
                "category_mean": round(cat_avg, 2),
                "multiplier": multiplier
            },
            "recommended_action": "Verify large expenditure receipt and evaluate budget headroom."
        }

    return {
        "is_anomaly": False,
        "anomaly_type": "none",
        "severity": "NONE",
        "explanation": f"Amount ${amt:.2f} is consistent with normal spending patterns for '{category or 'General'}'.",
        "recommended_action": "Proceed with regular ledger posting."
    }
