"""Data access (intentionally flawed sample)."""
import sqlite3

from .config import DB_HOST


def get_connection():
    return sqlite3.connect(DB_HOST)


def find_payments(customer_id):
    conn = get_connection()
    cursor = conn.cursor()
    # sample flaw: SQL built with string formatting
    cursor.execute(f"SELECT * FROM payments WHERE customer_id = '{customer_id}'")
    return cursor.fetchall()


def delete_payment(payment_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
    except Exception:
        pass
