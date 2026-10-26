"""
SQLite Database Layer for Autonomous Financial Reconciliation Agent.
Manages persistent banking ledger transactions, raw statement descriptors,
budget limits, and immutable audit logs.
"""

import sqlite3
import csv
from pathlib import Path
from typing import Dict, Any, List, Optional

DATA_DIR = Path(__file__).parent.resolve()
DB_PATH = DATA_DIR / "reconciliation.db"
CSV_PATH = DATA_DIR / "kaggle_personal_transactions.csv"

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Initializes SQLite schema and seeds realistic Kaggle-style banking transactions."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS budget_rules (
        category TEXT PRIMARY KEY,
        monthly_limit REAL NOT NULL,
        category_type TEXT NOT NULL,
        flexibility_weight REAL NOT NULL,
        description TEXT
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id TEXT PRIMARY KEY,
        date TEXT NOT NULL,
        raw_descriptor TEXT NOT NULL,
        clean_merchant TEXT NOT NULL,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        account_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'posted',
        is_anomaly INTEGER NOT NULL DEFAULT 0,
        anomaly_type TEXT,
        anomaly_details TEXT
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        run_id TEXT NOT NULL,
        step_index INTEGER NOT NULL,
        tool_name TEXT NOT NULL,
        arguments_json TEXT,
        output_json TEXT,
        duration_ms REAL
    );

    CREATE INDEX IF NOT EXISTS idx_txns_date_cat ON transactions(date, category);
    """)

    # Seed budget rules
    budget_rules_data = [
        ("Housing", 1800.0, "non_discretionary", 0.0, "Rent/Mortgage. Protected from cuts."),
        ("Utilities", 250.0, "non_discretionary", 0.1, "Electricity, gas, internet, water."),
        ("Healthcare", 150.0, "non_discretionary", 0.1, "Prescriptions, clinical copays."),
        ("Groceries", 550.0, "semi_discretionary", 0.3, "Supermarket & pantry food staples."),
        ("Transport", 200.0, "semi_discretionary", 0.4, "Subway transit, fuel, rideshare."),
        ("Shopping", 300.0, "discretionary", 0.8, "Retail apparel, electronics, goods."),
        ("Dining", 400.0, "discretionary", 0.9, "Restaurants, cafes, takeout."),
        ("Entertainment", 150.0, "discretionary", 1.0, "Streaming, movies, gaming, events.")
    ]

    cursor.executemany("""
    INSERT OR REPLACE INTO budget_rules (category, monthly_limit, category_type, flexibility_weight, description)
    VALUES (?, ?, ?, ?, ?)
    """, budget_rules_data)

    conn.commit()
    conn.close()

def seed_transactions_from_dataset():
    """Seeds the SQLite database with 4 months of transactions with raw descriptors."""
    init_database()
    conn = get_connection()
    cursor = conn.cursor()

    # Base recurring and variable templates with real messy bank descriptors
    # Format: (raw_descriptor, clean_merchant, category, base_amount, account)
    templates = [
        # Housing
        ("EQUITY RESIDENTIAL ACH PMT 849201", "Equity Residential", "Housing", 1800.00, "Chase Total Checking"),
        # Utilities
        ("CONEDISON NYC ELEC ACH 91024", "ConEdison Electric", "Utilities", 114.50, "Chase Total Checking"),
        ("NATIONAL GRID NY GAS WEB PMT", "National Grid Gas", "Utilities", 42.80, "Chase Total Checking"),
        ("VERIZON FIOS BROADBAND 800-837-4966", "Verizon Fios Internet", "Utilities", 79.99, "Chase Sapphire Preferred"),
        ("NYC WATER BOARD ONLINE PMT", "City Water Board", "Utilities", 34.20, "Chase Total Checking"),
        # Groceries
        ("TRADER JOE'S #542 - BROOKLYN NY", "Trader Joe's", "Groceries", 78.40, "Amex Gold Card"),
        ("WHOLEFDS MKT 10243 NEW YORK NY", "Whole Foods Market", "Groceries", 112.50, "Amex Gold Card"),
        ("COSTCO WHSE #1043 LONG ISLAND CTY", "Costco Wholesale", "Groceries", 185.00, "Citi Double Cash"),
        ("LOCAL FARMERS MARKET UNION SQ NY", "Local Farmers Market", "Groceries", 36.00, "Chase Sapphire Preferred"),
        # Dining
        ("SWEETGREEN #042 NEW YORK NY", "Sweetgreen", "Dining", 16.50, "Amex Gold Card"),
        ("CHIPOTLE 1849 BROADWAY NY", "Chipotle Mexican Grill", "Dining", 14.80, "Amex Gold Card"),
        ("SQ *BLUE BOTTLE COFFEE - WILLIAMSBURG", "Blue Bottle Coffee", "Dining", 6.75, "Amex Gold Card"),
        ("TST* TRATTORIA BELLA - 4920 NYC", "Trattoria Bella", "Dining", 72.00, "Chase Sapphire Preferred"),
        ("SHAKE SHACK 012 MADISON SQ NY", "Shake Shack", "Dining", 19.20, "Amex Gold Card"),
        ("JOE'S PIZZA GREENWICH AVE NY", "Joe's Pizza", "Dining", 12.00, "Chase Sapphire Preferred"),
        # Transport
        ("MTA*NYCT PAYGO NEW YORK NY", "MTA Subway Transit", "Transport", 34.00, "Chase Sapphire Preferred"),
        ("UBER *TRIP 8294 HELP.UBER.COM CA", "Uber Technologies", "Transport", 24.50, "Chase Sapphire Preferred"),
        ("LYFT *RIDE 0492 SAN FRANCISCO CA", "Lyft Rides", "Transport", 21.00, "Chase Sapphire Preferred"),
        ("SHELL OIL 57542918491 QPS BROOKLYN", "Shell Oil Gas Station", "Transport", 48.00, "Citi Double Cash"),
        # Healthcare
        ("CVS/PHARMACY #02941 NEW YORK NY", "CVS Pharmacy", "Healthcare", 28.50, "FSA Debit Card"),
        ("WALGREENS STORE 04829 BROOKLYN NY", "Walgreens Health", "Healthcare", 22.00, "FSA Debit Card"),
        ("QUEST DIAGNOSTICS COPAY ONLINE", "Quest Diagnostics Copay", "Healthcare", 40.00, "FSA Debit Card"),
        # Entertainment
        ("NETFLIX.COM PAYMENT 866-579-7172", "Netflix Subscription", "Entertainment", 19.99, "Chase Sapphire Preferred"),
        ("SPOTIFY USA 877-778-1161 NY", "Spotify Premium", "Entertainment", 11.99, "Chase Sapphire Preferred"),
        ("AMC THEATRES KIPS BAY 15 NYC", "AMC Theatres", "Entertainment", 36.50, "Citi Double Cash"),
        ("STEAM GAMES 425-952-2985 WA", "Steam Games", "Entertainment", 49.99, "Citi Double Cash"),
        ("ADOBE *CREATIVE CLOUD 800-833-6687", "Adobe Creative Cloud", "Entertainment", 59.99, "Chase Sapphire Preferred"),
        # Shopping
        ("AMZN Mktp US*2K83J1 Amzn.com/bill", "Amazon.com", "Shopping", 58.20, "Chase Sapphire Preferred"),
        ("TARGET STORE T-2849 JERSEY CITY NJ", "Target Stores", "Shopping", 64.50, "Citi Double Cash"),
        ("UNIQLO SOHO NEW YORK NY", "Uniqlo Apparel", "Shopping", 85.00, "Amex Gold Card"),
        ("BEST BUY #0492 NEW YORK NY", "Best Buy Electronics", "Shopping", 149.00, "Citi Double Cash")
    ]

    import random
    random.seed(42)

    cursor.execute("DELETE FROM transactions")

    txn_list = []
    txn_id_counter = 1000

    # 4 months: May, June, July, August 2026
    months = ["2026-05", "2026-06", "2026-07", "2026-08"]

    for m in months:
        # Monthly Rent
        txn_list.append((
            f"TXN-{txn_id_counter}",
            f"{m}-01T09:00:00",
            "EQUITY RESIDENTIAL ACH PMT 849201",
            "Equity Residential",
            "Housing",
            1800.00,
            "Chase Total Checking",
            "posted",
            0, None, None
        ))
        txn_id_counter += 1

        # Fixed subscriptions
        txn_list.append((
            f"TXN-{txn_id_counter}",
            f"{m}-05T12:00:00",
            "NETFLIX.COM PAYMENT 866-579-7172",
            "Netflix Subscription",
            "Entertainment",
            19.99,
            "Chase Sapphire Preferred",
            "posted",
            0, None, None
        ))
        txn_id_counter += 1

        txn_list.append((
            f"TXN-{txn_id_counter}",
            f"{m}-07T08:30:00",
            "VERIZON FIOS BROADBAND 800-837-4966",
            "Verizon Fios Internet",
            "Utilities",
            79.99,
            "Chase Sapphire Preferred",
            "posted",
            0, None, None
        ))
        txn_id_counter += 1

        # Distribute 35-45 variable transactions per month
        for day in range(1, 29):
            if random.random() < 0.65:
                template = random.choice(templates[1:])
                amt_var = round(template[3] * random.uniform(0.85, 1.20), 2)
                hour = random.randint(8, 22)
                min_val = random.randint(0, 59)
                date_str = f"{m}-{day:02d}T{hour:02d}:{min_val:02d}:00"

                txn_list.append((
                    f"TXN-{txn_id_counter}",
                    date_str,
                    template[0],
                    template[1],
                    template[2],
                    amt_var,
                    template[4],
                    "posted",
                    0, None, None
                ))
                txn_id_counter += 1

    # Deliberately planted anomalies with messy descriptors
    anomalies = [
        # 1. Le Bernardin Luxury Spike
        ("TXN-ANOM-01", "2026-06-14T21:30:00", "TST* LE BERNARDIN - PRIVATE DINING NYC", "Le Bernardin NYC", "Dining", 745.00, "Amex Gold Card", "posted", 1, "amount_spike", "Amount $745.00 is 10.3x above typical Dining average ($28.00)."),
        # 2. Adobe Duplicate Burst
        ("TXN-ANOM-02A", "2026-07-02T10:14:10", "ADOBE *CREATIVE CLOUD 800-833-6687", "Adobe Creative Cloud", "Entertainment", 59.99, "Chase Sapphire Preferred", "posted", 0, None, None),
        ("TXN-ANOM-02B", "2026-07-02T10:17:22", "ADOBE *CREATIVE CLOUD 800-833-6687", "Adobe Creative Cloud", "Entertainment", 59.99, "Chase Sapphire Preferred", "posted", 1, "rapid_duplicate", "Duplicate $59.99 charge within 192 seconds."),
        # 3. BitPay Crypto Offshore
        ("TXN-ANOM-03", "2026-07-19T03:15:00", "BITPAY GLOBAL VALPARAISO CL 0492", "BitPay Global Valparaiso CL", "Shopping", 289.50, "Citi Double Cash", "posted", 1, "suspicious_merchant", "Unrecognized offshore cryptocurrency checkout merchant at 03:15 AM."),
        # 4. Bulk Grocery Spike
        ("TXN-ANOM-04", "2026-08-05T15:45:00", "COSTCO WHSE #1043 PANTRY BULK RESTOCK", "Costco Wholesale", "Groceries", 490.80, "Citi Double Cash", "posted", 1, "amount_spike", "Bulk charge consumed 89.2% of total monthly grocery allowance."),
        # 5. Electricity Spike
        ("TXN-ANOM-05", "2026-08-12T11:00:00", "CONEDISON NYC ELEC AC SURGE SURCHARGE", "ConEdison Electric", "Utilities", 412.50, "Chase Total Checking", "posted", 1, "amount_spike", "Power grid bill $412.50 is 3.6x typical electricity baseline ($115)."),
        # 6. Uber Rapid Burst
        ("TXN-ANOM-06A", "2026-08-18T22:05:00", "UBER *TRIP 9102 HELP.UBER.COM", "Uber Technologies", "Transport", 26.50, "Chase Sapphire Preferred", "posted", 0, None, None),
        ("TXN-ANOM-06B", "2026-08-18T22:15:00", "UBER *TRIP CANCEL FEE HELP.UBER.COM", "Uber Technologies", "Transport", 10.00, "Chase Sapphire Preferred", "posted", 0, None, None),
        ("TXN-ANOM-06C", "2026-08-18T22:25:00", "UBER *TRIP XL AIRPORT SURGE NY", "Uber Technologies", "Transport", 78.00, "Chase Sapphire Preferred", "posted", 1, "frequency_burst", "Third consecutive rideshare charge within 20 minutes totaling $114.50."),
        # 7. Apple Store Laptop Upgrade
        ("TXN-ANOM-07", "2026-08-22T14:20:00", "APPLE STORE #R042 5TH AVE NY", "Apple Store Fifth Ave", "Shopping", 1299.00, "Citi Double Cash", "posted", 1, "amount_spike", "Hardware repair charge exceeds entire monthly shopping allocation by 4.3x."),
        # 8. Shell Gas Pump Double Swipe
        ("TXN-ANOM-08A", "2026-08-28T17:40:00", "SHELL OIL 57542918491 QPS PUMP 04", "Shell Oil Gas Station", "Transport", 55.00, "Citi Double Cash", "posted", 0, None, None),
        ("TXN-ANOM-08B", "2026-08-28T17:42:30", "SHELL OIL 57542918491 QPS PUMP 04", "Shell Oil Gas Station", "Transport", 55.00, "Citi Double Cash", "posted", 1, "rapid_duplicate", "Identical pump authorization of $55.00 within 150 seconds of previous swipe.")
    ]

    txn_list.extend(anomalies)

    cursor.executemany("""
    INSERT INTO transactions (id, date, raw_descriptor, clean_merchant, category, amount, account_name, status, is_anomaly, anomaly_type, anomaly_details)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, txn_list)

    conn.commit()

    # Also export to CSV for transparency / Kaggle compatibility
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Transaction ID", "Date", "Statement Descriptor", "Normalized Merchant", "Category", "Amount", "Account", "Status", "Is Anomaly", "Anomaly Type"])
        for row in txn_list:
            writer.writerow([row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9]])

    conn.close()
    print(f"Database successfully initialized with {len(txn_list)} transactions at {DB_PATH}")
    print(f"Exported CSV dataset at {CSV_PATH}")

if __name__ == "__main__":
    seed_transactions_from_dataset()
