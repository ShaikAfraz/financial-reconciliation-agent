"""
Command-line utility to securely provision an initial administrator account.
Never stores or logs plaintext passwords. Prompts securely using getpass.
"""

import sys
import getpass
from typing import Optional

from auth.service import create_user, validate_password_strength, normalize_username
import data.db as db

def run_create_admin(username_arg: Optional[str] = None, password_arg: Optional[str] = None) -> int:
    """Interactively or programmatically provisions an initial administrator."""
    db.init_database()

    print("=" * 64)
    print("VERITAS TREASURY • INITIAL ADMINISTRATOR PROVISIONING")
    print("=" * 64)

    # 1. Obtain Username
    if username_arg:
        username = normalize_username(username_arg)
    else:
        try:
            raw_user = input("Admin Username / Email: ").strip()
            username = normalize_username(raw_user)
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return 1

    if not username:
        print("[ERROR] Username cannot be empty.")
        return 1

    # 2. Obtain Password
    if password_arg:
        password = password_arg
    else:
        try:
            password = getpass.getpass("Admin Password (min 12 characters): ")
            confirm = getpass.getpass("Confirm Admin Password: ")
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return 1

        if password != confirm:
            print("[ERROR] Passwords do not match.")
            return 1

    # 3. Validate Strength
    is_valid, err = validate_password_strength(password)
    if not is_valid:
        print(f"[ERROR] {err}")
        return 1

    # 4. Create User
    try:
        user = create_user(username=username, password=password, role="admin", is_active=True)
        print("=" * 64)
        print(f"[SUCCESS] Administrator '{user['username']}' provisioned successfully.")
        print(f"User ID: {user['id']}")
        print(f"Role:    {user['role']}")
        print("=" * 64)
        return 0
    except ValueError as e:
        print(f"[ERROR] Could not create administrator: {e}")
        return 1
    except Exception as e:
        print(f"[ERROR] An unexpected error occurred: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(run_create_admin())
