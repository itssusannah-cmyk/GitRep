"""
database.py
Persistence layer for the Retail Shop Manager.

All SQL lives in this one module. The GUI code never writes raw SQL —
it only calls these methods. This separation means you could swap SQLite
for another database later without touching a single line of GUI code.
"""

import sqlite3
import os
import sys
import hashlib
import binascii
from datetime import datetime


def _app_data_dir():
    """Where shop.db lives, in a folder the current user can always write to
    — regardless of whether the app is installed in Program Files (Windows),
    /Applications (macOS), or run straight from a folder. This matters once
    the app is installed system-wide: a normal user account cannot write
    inside Program Files, so storing the database next to the executable
    would crash on save."""
    app_name = "RetailShopManager"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    path = os.path.join(base, app_name)
    os.makedirs(path, exist_ok=True)
    return path


DB_FILE = os.path.join(_app_data_dir(), "shop.db")

# Sensible defaults shown the first time the app runs, before anyone has
# filled in the Settings tab. All of these are editable at runtime.
DEFAULT_SETTINGS = {
    "business_name": "My Retail Shop",
    "phone": "",
    "email": "",
    "address": "",
    "currency_symbol": "$",
    "receipt_footer": "Thank you for your business!",
}


def _hash_password(password, salt_hex=None):
    """PBKDF2-HMAC-SHA256 with a random 16-byte salt. Never store plaintext
    passwords or use a fast hash (like plain SHA256) for this — PBKDF2's
    100,000 iterations make brute-forcing a stolen database far slower."""
    salt = os.urandom(16) if salt_hex is None else binascii.unhexlify(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return binascii.hexlify(salt).decode(), binascii.hexlify(digest).decode()


class Database:
    def __init__(self, db_file=DB_FILE):
        # check_same_thread=False: the desktop app only ever touches the
        # database from Tkinter's single thread, but the Flask web app can
        # dispatch different requests on different threads. SQLite's C
        # library handles that fine as long as Python doesn't block it with
        # this check.
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row          # rows behave like dicts
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._create_tables()

    # ------------------------------------------------------------------
    # SCHEMA
    # ------------------------------------------------------------------
    def _create_tables(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                category        TEXT DEFAULT '',
                cost_price      REAL NOT NULL,
                selling_price   REAL NOT NULL,
                quantity        INTEGER NOT NULL DEFAULT 0,
                reorder_level   INTEGER NOT NULL DEFAULT 5
            );

            CREATE TABLE IF NOT EXISTS sales (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id      INTEGER,
                product_name    TEXT NOT NULL,
                quantity        INTEGER NOT NULL,
                sale_price      REAL NOT NULL,
                cost_price      REAL NOT NULL,
                total           REAL NOT NULL,
                profit          REAL NOT NULL,
                sale_date       TEXT NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS purchases (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id      INTEGER,
                product_name    TEXT NOT NULL,
                quantity        INTEGER NOT NULL,
                cost_price      REAL NOT NULL,
                total           REAL NOT NULL,
                purchase_date   TEXT NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT NOT NULL UNIQUE,
                password_hash   TEXT NOT NULL,
                salt            TEXT NOT NULL,
                role            TEXT NOT NULL DEFAULT 'staff',
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key             TEXT PRIMARY KEY,
                value           TEXT
            );
            """
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # USERS / AUTHENTICATION
    # ------------------------------------------------------------------
    def has_users(self):
        """False only on a brand-new install — triggers the first-run
        'create admin account' screen instead of the login screen."""
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM users")
        return cur.fetchone()["c"] > 0

    def username_exists(self, username):
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE username=?", (username,))
        return cur.fetchone() is not None

    def create_user(self, username, password, role="staff"):
        salt_hex, hash_hex = _hash_password(password)
        self.conn.execute(
            "INSERT INTO users (username, password_hash, salt, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, hash_hex, salt_hex, role, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        self.conn.commit()

    def verify_user(self, username, password):
        """Returns the user row on success, or None. Never reveals whether
        the username or the password was the wrong part."""
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        if row is None:
            return None
        _, hash_hex = _hash_password(password, row["salt"])
        return row if hash_hex == row["password_hash"] else None

    def get_user_by_username(self, username):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=?", (username,))
        return cur.fetchone()

    def list_users(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, username, role, created_at FROM users ORDER BY username")
        return cur.fetchall()

    def count_admins(self):
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM users WHERE role='admin'")
        return cur.fetchone()["c"]

    def delete_user(self, user_id):
        self.conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        self.conn.commit()

    def change_password(self, user_id, new_password):
        salt_hex, hash_hex = _hash_password(new_password)
        self.conn.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?", (hash_hex, salt_hex, user_id))
        self.conn.commit()

    # ------------------------------------------------------------------
    # SETTINGS (business profile, currency, etc.)
    # ------------------------------------------------------------------
    def get_setting(self, key, default=""):
        cur = self.conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = cur.fetchone()
        return row["value"] if row is not None else DEFAULT_SETTINGS.get(key, default)

    def get_all_settings(self):
        result = dict(DEFAULT_SETTINGS)
        cur = self.conn.cursor()
        cur.execute("SELECT key, value FROM settings")
        for row in cur.fetchall():
            result[row["key"]] = row["value"]
        return result

    def set_setting(self, key, value):
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def set_settings(self, settings_dict):
        for key, value in settings_dict.items():
            self.set_setting(key, value)

    # ------------------------------------------------------------------
    # PRODUCTS / INVENTORY
    # ------------------------------------------------------------------
    def add_product(self, name, category, cost_price, selling_price, quantity, reorder_level):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO products (name, category, cost_price, selling_price, quantity, reorder_level) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, category, cost_price, selling_price, quantity, reorder_level),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_product(self, product_id, name, category, cost_price, selling_price, reorder_level):
        self.conn.execute(
            "UPDATE products SET name=?, category=?, cost_price=?, selling_price=?, reorder_level=? "
            "WHERE id=?",
            (name, category, cost_price, selling_price, reorder_level, product_id),
        )
        self.conn.commit()

    def delete_product(self, product_id):
        self.conn.execute("DELETE FROM products WHERE id=?", (product_id,))
        self.conn.commit()

    def get_products(self, search=""):
        cur = self.conn.cursor()
        if search:
            like = f"%{search}%"
            cur.execute(
                "SELECT * FROM products WHERE name LIKE ? OR category LIKE ? ORDER BY name",
                (like, like),
            )
        else:
            cur.execute("SELECT * FROM products ORDER BY name")
        return cur.fetchall()

    def get_product(self, product_id):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM products WHERE id=?", (product_id,))
        return cur.fetchone()

    def low_stock(self):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM products WHERE quantity <= reorder_level ORDER BY quantity")
        return cur.fetchall()

    def restock(self, product_id, quantity, new_cost_price=None):
        """Increase stock and log a purchase. Optionally updates the cost price
        (useful when a supplier changes their price)."""
        product = self.get_product(product_id)
        if product is None:
            raise ValueError("Product not found")
        cost_price = new_cost_price if new_cost_price is not None else product["cost_price"]

        self.conn.execute(
            "UPDATE products SET quantity = quantity + ?, cost_price = ? WHERE id=?",
            (quantity, cost_price, product_id),
        )
        self.conn.execute(
            "INSERT INTO purchases (product_id, product_name, quantity, cost_price, total, purchase_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                product_id,
                product["name"],
                quantity,
                cost_price,
                quantity * cost_price,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # SALES
    # ------------------------------------------------------------------
    def record_sale(self, product_id, quantity):
        """Sells `quantity` units of a product. Raises ValueError if stock is
        insufficient (guarantees inventory can never go negative)."""
        product = self.get_product(product_id)
        if product is None:
            raise ValueError("Product not found")
        if product["quantity"] < quantity:
            raise ValueError(f"Insufficient stock. Only {product['quantity']} left.")

        sale_price = product["selling_price"]
        cost_price = product["cost_price"]
        total = sale_price * quantity
        profit = (sale_price - cost_price) * quantity

        self.conn.execute("UPDATE products SET quantity = quantity - ? WHERE id=?", (quantity, product_id))
        self.conn.execute(
            "INSERT INTO sales (product_id, product_name, quantity, sale_price, cost_price, total, "
            "profit, sale_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                product_id,
                product["name"],
                quantity,
                sale_price,
                cost_price,
                total,
                profit,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        self.conn.commit()
        return {"name": product["name"], "quantity": quantity, "sale_price": sale_price, "total": total}

    def get_sales(self, start=None, end=None):
        cur = self.conn.cursor()
        if start and end:
            cur.execute(
                "SELECT * FROM sales WHERE date(sale_date) BETWEEN ? AND ? ORDER BY sale_date DESC",
                (start, end),
            )
        else:
            cur.execute("SELECT * FROM sales ORDER BY sale_date DESC")
        return cur.fetchall()

    # ------------------------------------------------------------------
    # REPORTS
    # ------------------------------------------------------------------
    def profit_loss_summary(self, start=None, end=None):
        cur = self.conn.cursor()
        query = (
            "SELECT COALESCE(SUM(total),0) AS revenue, "
            "COALESCE(SUM(profit),0) AS profit, "
            "COALESCE(SUM(quantity),0) AS units, "
            "COALESCE(SUM(cost_price*quantity),0) AS cost FROM sales"
        )
        params = ()
        if start and end:
            query += " WHERE date(sale_date) BETWEEN ? AND ?"
            params = (start, end)
        cur.execute(query, params)
        row = cur.fetchone()
        return {"revenue": row["revenue"], "cost": row["cost"], "profit": row["profit"], "units_sold": row["units"]}

    def daily_profit_trend(self, days=14):
        cur = self.conn.cursor()
        cur.execute(
            "SELECT date(sale_date) AS d, SUM(profit) AS profit, SUM(total) AS revenue "
            "FROM sales GROUP BY date(sale_date) ORDER BY d DESC LIMIT ?",
            (days,),
        )
        return list(reversed(cur.fetchall()))

    def inventory_value(self):
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(cost_price*quantity),0) AS cost_value, "
            "COALESCE(SUM(selling_price*quantity),0) AS retail_value FROM products"
        )
        return cur.fetchone()
