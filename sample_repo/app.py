"""
Intentionally vulnerable sample application used to demo the AI Software
Code Reviewer & Secure Development Agent. DO NOT use in production.
"""
import hashlib
import sqlite3

# CWE-798 / CWE-259: hard-coded credential
API_SECRET_KEY = "sk_live_51Hh2VvVvS3cJj9xK3example_hardcoded_key"

DB_PATH = "app.db"


def get_user_by_username(username):
    """CWE-89: SQL Injection via string formatting."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    query = "SELECT * FROM users WHERE username = '%s'" % username
    cursor.execute(query)
    return cursor.fetchone()


def hash_password(password):
    """CWE-327: use of a broken/risky cryptographic hash for passwords."""
    return hashlib.md5(password.encode()).hexdigest()


def load_session(serialized_session):
    """CWE-502: insecure deserialization of untrusted data."""
    import pickle
    return pickle.loads(serialized_session)


def get_discount(order_total, is_vip):
    # Logic bug: VIP discount never applies because of operator precedence /
    # wrong boolean check — a semantic bug static analysis alone won't catch.
    if is_vip == "true":
        return order_total * 0.8
    return order_total


def process_orders(orders):
    total = 0
    for o in orders:
        try:
            total += o["amount"]
        except:  # noqa: E722 - bare except, swallows all errors silently
            pass
    return total
