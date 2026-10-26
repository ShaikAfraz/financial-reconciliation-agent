"""
Reproducible synthetic transaction generator for the Autonomous Financial Reconciliation Agent.
Generates 4 months of realistic personal transactions with 8 deliberately planted anomalies.
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent

MERCHANT_POOL = {
    "Housing": [("Equity Residential", 1800.0, 0.0)],
    "Utilities": [
        ("ConEdison Electric", 115.0, 18.0),
        ("National Grid Gas", 45.0, 8.0),
        ("Verizon Fios Internet", 79.99, 0.0),
        ("City Water Board", 32.50, 5.0)
    ],
    "Groceries": [
        ("Trader Joe's", 78.40, 22.0),
        ("Whole Foods Market", 112.50, 35.0),
        ("Costco Wholesale", 185.00, 45.0),
        ("Local Farmers Market", 34.00, 12.0)
    ],
    "Dining": [
        ("Sweetgreen", 16.50, 3.5),
        ("Chipotle Mexican Grill", 14.80, 2.5),
        ("Blue Bottle Coffee", 6.75, 1.5),
        ("Trattoria Bella", 72.00, 18.0),
        ("Shake Shack", 19.20, 4.0),
        ("Joe's Pizza", 12.00, 3.0)
    ],
    "Transport": [
        ("MTA Subway Transit", 34.00, 0.0),
        ("Uber Technologies", 24.50, 8.0),
        ("Lyft Rides", 21.00, 6.5),
        ("Shell Oil Gas Station", 48.00, 9.0)
    ],
    "Healthcare": [
        ("CVS Pharmacy", 28.50, 14.0),
        ("Walgreens Health", 22.00, 9.0),
        ("Quest Diagnostics Copay", 40.00, 0.0),
        ("CityMD Urgent Care Copay", 50.00, 0.0)
    ],
    "Entertainment": [
        ("Netflix Subscription", 19.99, 0.0),
        ("Spotify Premium", 11.99, 0.0),
        ("AMC Theatres", 36.50, 10.0),
        ("Steam Games", 49.99, 20.0),
        ("Brooklyn Bowl", 55.00, 15.0)
    ],
    "Shopping": [
        ("Amazon.com", 58.20, 28.0),
        ("Target Stores", 64.50, 32.0),
        ("Uniqlo Apparel", 85.00, 30.0),
        ("Apple Store Digital", 9.99, 5.0),
        ("Best Buy Electronics", 149.00, 60.0)
    ]
}

def generate_dataset(seed: int = 42) -> list:
    random.seed(seed)
    transactions = []
    txn_id_counter = 1000

    # Generate 4 months: 2026-05-01 through 2026-08-31
    base_date = datetime(2026, 5, 1, 9, 0, 0)
    total_days = 122

    # Regular recurring transactions per month
    for month_offset in range(4):
        m_date = base_date + timedelta(days=month_offset * 30.5)
        m_str = m_date.strftime("%Y-%m")

        # Rent on 1st
        transactions.append({
            "transaction_id": f"TXN-{txn_id_counter}",
            "date": f"{m_str}-01T09:00:00",
            "merchant": "Equity Residential",
            "description": "Monthly Apartment Rent Payment",
            "amount": 1800.00,
            "category": "Housing",
            "is_anomaly": False
        })
        txn_id_counter += 1

        # Fixed subscriptions
        transactions.append({
            "transaction_id": f"TXN-{txn_id_counter}",
            "date": f"{m_str}-05T12:00:00",
            "merchant": "Netflix Subscription",
            "description": "Monthly Streaming Subscription",
            "amount": 19.99,
            "category": "Entertainment",
            "is_anomaly": False
        })
        txn_id_counter += 1

        transactions.append({
            "transaction_id": f"TXN-{txn_id_counter}",
            "date": f"{m_str}-07T08:30:00",
            "merchant": "Verizon Fios Internet",
            "description": "Home Fiber Broadband",
            "amount": 79.99,
            "category": "Utilities",
            "is_anomaly": False
        })
        txn_id_counter += 1

        transactions.append({
            "transaction_id": f"TXN-{txn_id_counter}",
            "date": f"{m_str}-10T14:15:00",
            "merchant": "Spotify Premium",
            "description": "Music Family Plan",
            "amount": 11.99,
            "category": "Entertainment",
            "is_anomaly": False
        })
        txn_id_counter += 1

    # Daily / Weekly variable transactions
    categories = ["Groceries", "Dining", "Transport", "Utilities", "Healthcare", "Shopping", "Entertainment"]
    for day in range(total_days):
        current_date = base_date + timedelta(days=day)
        # 1-3 transactions per day
        num_txns = random.choices([0, 1, 2, 3], weights=[0.2, 0.45, 0.25, 0.1])[0]
        for _ in range(num_txns):
            cat = random.choice(categories)
            merchant, mean_amt, std_amt = random.choice(MERCHANT_POOL[cat])
            amt = max(3.50, round(random.normalvariate(mean_amt, max(1.0, std_amt)), 2))
            hour = random.randint(8, 22)
            minute = random.randint(0, 59)
            second = random.randint(0, 59)
            txn_dt = current_date.replace(hour=hour, minute=minute, second=second)

            transactions.append({
                "transaction_id": f"TXN-{txn_id_counter}",
                "date": txn_dt.isoformat(),
                "merchant": merchant,
                "description": f"Purchase at {merchant}",
                "amount": amt,
                "category": cat,
                "is_anomaly": False
            })
            txn_id_counter += 1

    # 8 Deliberately Planted Anomalies with rich detection grounds:
    anomalies = [
        # Anomaly 1: Massive Dining Spike (e.g. VIP Chef Table $745.00 vs $40 avg)
        {
            "transaction_id": "TXN-ANOM-01",
            "date": "2026-06-14T21:30:00",
            "merchant": "Le Bernardin NYC",
            "description": "Private Dining Room Celebration",
            "amount": 745.00,
            "category": "Dining",
            "is_anomaly": True,
            "anomaly_type": "amount_spike",
            "anomaly_details": "Amount $745.00 is 10.3x above typical Dining mean ($72.00)."
        },
        # Anomaly 2: Rapid Duplicate Burst (Identical SaaS charge within 3 minutes)
        {
            "transaction_id": "TXN-ANOM-02A",
            "date": "2026-07-02T10:14:10",
            "merchant": "Adobe Creative Cloud",
            "description": "Annual License Renewal",
            "amount": 59.99,
            "category": "Entertainment",
            "is_anomaly": False,
            "anomaly_type": "normal_parent",
            "anomaly_details": "Original transaction."
        },
        {
            "transaction_id": "TXN-ANOM-02B",
            "date": "2026-07-02T10:17:22",
            "merchant": "Adobe Creative Cloud",
            "description": "Annual License Renewal",
            "amount": 59.99,
            "category": "Entertainment",
            "is_anomaly": True,
            "anomaly_type": "rapid_duplicate",
            "anomaly_details": "Exact duplicate amount $59.99 billed 192 seconds after previous charge."
        },
        # Anomaly 3: Unknown Foreign Merchant / Crypto
        {
            "transaction_id": "TXN-ANOM-03",
            "date": "2026-07-19T03:15:00",
            "merchant": "BitPay Global Valparaiso CL",
            "description": "Crypto Checkout Gateway POS",
            "amount": 289.50,
            "category": "Shopping",
            "is_anomaly": True,
            "anomaly_type": "suspicious_merchant",
            "anomaly_details": "Unrecognized offshore merchant with no prior history and unusual timestamp (03:15 AM)."
        },
        # Anomaly 4: Massive Grocery Spike (bulk order $490 vs $90 avg)
        {
            "transaction_id": "TXN-ANOM-04",
            "date": "2026-08-05T15:45:00",
            "merchant": "Costco Wholesale",
            "description": "Commercial Kitchen Pantry Stocking",
            "amount": 490.80,
            "category": "Groceries",
            "is_anomaly": True,
            "anomaly_type": "amount_spike",
            "anomaly_details": "Single transaction consumed 89.2% of total monthly grocery budget."
        },
        # Anomaly 5: Electricity Bill Surge (Heating/AC fault $410 vs $115 avg)
        {
            "transaction_id": "TXN-ANOM-05",
            "date": "2026-08-12T11:00:00",
            "merchant": "ConEdison Electric",
            "description": "Monthly Grid Residential Utility Bill",
            "amount": 412.50,
            "category": "Utilities",
            "is_anomaly": True,
            "anomaly_type": "amount_spike",
            "anomaly_details": "Bill amount $412.50 is 3.58x normal electricity baseline."
        },
        # Anomaly 6: High Frequency Rideshare Burst (4 rides within 45 minutes)
        {
            "transaction_id": "TXN-ANOM-06A",
            "date": "2026-08-18T22:05:00",
            "merchant": "Uber Technologies",
            "description": "Uber Ride Downtown",
            "amount": 26.50,
            "category": "Transport",
            "is_anomaly": False
        },
        {
            "transaction_id": "TXN-ANOM-06B",
            "date": "2026-08-18T22:15:00",
            "merchant": "Uber Technologies",
            "description": "Uber Ride Cancellation Fee",
            "amount": 10.00,
            "category": "Transport",
            "is_anomaly": False
        },
        {
            "transaction_id": "TXN-ANOM-06C",
            "date": "2026-08-18T22:25:00",
            "merchant": "Uber Technologies",
            "description": "Uber XL Airport Transfer",
            "amount": 78.00,
            "category": "Transport",
            "is_anomaly": True,
            "anomaly_type": "frequency_burst",
            "anomaly_details": "Third consecutive rideshare charge in under 20 minutes totaling $114.50."
        },
        # Anomaly 7: Extraordinary Electronics purchase ($1,299 vs $60 shopping avg)
        {
            "transaction_id": "TXN-ANOM-07",
            "date": "2026-08-22T14:20:00",
            "merchant": "Apple Store Fifth Ave",
            "description": "Hardware Laptop Repair & Memory Upgrade",
            "amount": 1299.00,
            "category": "Shopping",
            "is_anomaly": True,
            "anomaly_type": "amount_spike",
            "anomaly_details": "Amount $1,299.00 is 4.33x the entire monthly shopping allowance ($300)."
        },
        # Anomaly 8: Rapid Double Gas Station Swipe
        {
            "transaction_id": "TXN-ANOM-08A",
            "date": "2026-08-28T17:40:00",
            "merchant": "Shell Oil Gas Station",
            "description": "Automated Fuel Dispenser",
            "amount": 55.00,
            "category": "Transport",
            "is_anomaly": False
        },
        {
            "transaction_id": "TXN-ANOM-08B",
            "date": "2026-08-28T17:42:30",
            "merchant": "Shell Oil Gas Station",
            "description": "Automated Fuel Dispenser",
            "amount": 55.00,
            "category": "Transport",
            "is_anomaly": True,
            "anomaly_type": "rapid_duplicate",
            "anomaly_details": "Duplicate pump transaction of $55.00 occurring 150 seconds after first pump authorization."
        }
    ]

    transactions.extend(anomalies)
    # Sort chronologically
    transactions.sort(key=lambda x: x["date"])
    return transactions

if __name__ == "__main__":
    txns = generate_dataset()
    output_path = DATA_DIR / "synthetic_transactions.json"
    with open(output_path, "w") as f:
        json.dump(txns, f, indent=2)
    print(f"Generated {len(txns)} transactions with 8 planted anomalies at {output_path}")
