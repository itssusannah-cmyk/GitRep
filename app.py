"""
app.py
Web version of Retail Shop Manager updated for Render & Supabase deployment.
"""

import os
import io
import csv
import secrets
from functools import wraps
from datetime import date, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, Response, send_file, abort,
)

from database import Database

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


# ---------------------------------------------------------------------------
# App setup & Database URL configuration
# ---------------------------------------------------------------------------
app = Flask(__name__)

# Fetch database URI from Render environment variables
DATABASE_URL = os.environ.get('DATABASE_URL')

# SQLAlchemy/psycopg2 requires 'postgresql://' instead of 'postgres://'
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Fallback Secret Key logic for cloud ephemeral filesystems
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# Pass the dynamic database URL into your Database layer
db = Database(db_url=DATABASE_URL)


def money(value):
    symbol = db.get_setting("currency_symbol", "$")
    try:
        return f"{symbol}{float(value):,.2f}"
    except (TypeError, ValueError):
        return f"{symbol}0.00"


app.jinja_env.filters["money"] = money


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    cur = db.conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    return cur.fetchone()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not db.has_users():
            return redirect(url_for("setup"))
        if current_user() is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None or user["role"] != "admin":
            flash("Only admins can do that.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_globals():
    return {
        "business_name": db.get_setting("business_name", "Retail Shop Manager"),
        "logged_in_user": current_user(),
        "csrf_token": session.get("_csrf_token", ""),
    }


@app.before_request
def csrf_protect():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(16)
    if request.method == "POST":
        expected = session.get("_csrf_token")
        got = request.form.get("_csrf_token")
        if not expected or not got or not secrets.compare_digest(expected, got):
            abort(400, "Your session expired or the form was out of date. Go back and try again.")


# ---------------------------------------------------------------------------
# Setup / Login / Logout
# ---------------------------------------------------------------------------
@app.route("/setup", methods=["GET", "POST"])
def setup():
    if db.has_users():
        return redirect(url_for("login"))

    if request.method == "POST":
        business = request.form.get("business_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not username or not password:
            flash("Username and password are required.", "error")
        elif len(password) < 4:
            flash("Password should be at least 4 characters.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        else:
            db.create_user(username, password, role="admin")
            if business:
                db.set_setting("business_name", business)
            user = db.get_user_by_username(username)
            session["user_id"] = user["id"]
            flash(f"Welcome, {username}! Your account is ready.", "success")
            return redirect(url_for("dashboard"))

    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not db.has_users():
        return redirect(url_for("setup"))
    if current_user() is not None:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.verify_user(username, password)
        if user is None:
            flash("Invalid username or password.", "error")
        else:
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.route("/")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        inv=db.inventory_value(),
        summary=db.profit_loss_summary(),
        low_stock=db.low_stock(),
    )


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
@app.route("/inventory")
@login_required
def inventory():
    search = request.args.get("q", "")
    return render_template("inventory.html", products=db.get_products(search), search=search)


@app.route("/inventory/add", methods=["POST"])
@login_required
def inventory_add():
    try:
        name = request.form["name"].strip()
        category = request.form.get("category", "").strip()
        cost = float(request.form["cost_price"])
        price = float(request.form["selling_price"])
        qty = int(request.form.get("quantity") or 0)
        reorder = int(request.form.get("reorder_level") or 5)
        if not name or cost < 0 or price < 0 or qty < 0 or reorder < 0:
            raise ValueError
        db.add_product(name, category, cost, price, qty, reorder)
        flash(f"Added {name}.", "success")
    except (ValueError, KeyError):
        flash("Check the product details — numbers must be valid and non-negative.", "error")
    return redirect(url_for("inventory"))


@app.route("/inventory/<int:product_id>/edit", methods=["POST"])
@login_required
def inventory_edit(product_id):
    try:
        name = request.form["name"].strip()
        category = request.form.get("category", "").strip()
        cost = float(request.form["cost_price"])
        price = float(request.form["selling_price"])
        reorder = int(request.form.get("reorder_level") or 5)
        if not name or cost < 0 or price < 0 or reorder < 0:
            raise ValueError
        db.update_product(product_id, name, category, cost, price, reorder)
        flash(f"Updated {name}.", "success")
    except (ValueError, KeyError):
        flash("Check the product details — numbers must be valid and non-negative.", "error")
    return redirect(url_for("inventory"))


@app.route("/inventory/<int:product_id>/delete", methods=["POST"])
@login_required
def inventory_delete(product_id):
    db.delete_product(product_id)
    flash("Product deleted.", "success")
    return redirect(url_for("inventory"))


@app.route("/inventory/<int:product_id>/restock", methods=["POST"])
@login_required
def inventory_restock(product_id):
    try:
        qty = int(request.form["quantity"])
        if qty <= 0:
            raise ValueError
        db.restock(product_id, qty)
        flash("Stock updated.", "success")
    except (ValueError, KeyError):
        flash("Restock quantity must be a positive whole number.", "error")
    return redirect(url_for("inventory"))


@app.route("/inventory/export.xlsx")
@login_required
def inventory_export_xlsx():
    if not HAS_OPENPYXL:
        flash("Excel export needs the 'openpyxl' package on the server (pip install openpyxl).", "error")
        return redirect(url_for("inventory"))
    products = db.get_products(request.args.get("q", ""))
    headers = ["ID", "Name", "Category", "Cost Price", "Selling Price", "Stock", "Reorder Level"]
    rows = [
        (p["id"], p["name"], p["category"], p["cost_price"], p["selling_price"], p["quantity"], p["reorder_level"])
        for p in products
    ]
    return _send_excel(headers, rows, "Inventory", "inventory.xlsx")


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------
@app.route("/sales")
@login_required
def sales():
    return render_template("sales.html", products=db.get_products(), sales=db.get_sales())


@app.route("/sales/sell", methods=["POST"])
@login_required
def sell():
    try:
        product_id = int(request.form["product_id"])
        qty = int(request.form["quantity"])
        if qty <= 0:
            raise ValueError("Quantity must be a positive whole number.")
        result = db.record_sale(product_id, qty)
        flash(f"Sold {result['quantity']} x {result['name']} for {money(result['total'])}.", "success")
    except ValueError as e:
        flash(str(e) or "Please choose a product and a valid quantity.", "error")
    except KeyError:
        flash("Please choose a product and a valid quantity.", "error")
    return redirect(url_for("sales"))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@app.route("/reports")
@login_required
def reports():
    start = request.args.get("start") or (date.today() - timedelta(days=30)).isoformat()
    end = request.args.get("end") or date.today().isoformat()
    return render_template(
        "reports.html",
        summary=db.profit_loss_summary(start, end),
        trend=db.daily_profit_trend(days=14),
        start=start, end=end,
    )


@app.route("/reports/export.csv")
@login_required
def reports_export_csv():
    sales_list = db.get_sales(request.args.get("start"), request.args.get("end"))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Product", "Quantity", "Unit Price", "Total", "Profit", "Date"])
    for s in sales_list:
        writer.writerow([s["id"], s["product_name"], s["quantity"], s["sale_price"], s["total"], s["profit"], s["sale_date"]])
    return Response(
        output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=sales.csv"},
    )


@app.route("/reports/export.xlsx")
@login_required
def reports_export_xlsx():
    if not HAS_OPENPYXL:
        flash("Excel export needs the 'openpyxl' package on the server (pip install openpyxl).", "error")
        return redirect(url_for("reports"))
    sales_list = db.get_sales(request.args.get("start"), request.args.get("end"))
    headers = ["ID", "Product", "Quantity", "Unit Price", "Total", "Profit", "Date"]
    rows = [
        (s["id"], s["product_name"], s["quantity"], s["sale_price"], s["total"], s["profit"], s["sale_date"])
        for s in sales_list
    ]
    return _send_excel(headers, rows, "Sales", "sales.xlsx")


def _send_excel(headers, rows, sheet_title, filename):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1D5C8A", end_color="1D5C8A", fill_type="solid")
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    for row_idx, row in enumerate(rows, start=2):
        for col_idx, value in enumerate(row, start=1):
            ws.cell(row=row_idx, column=col_idx, value=value)
    for col_idx, header in enumerate(headers, start=1):
        widths = [len(str(header))] + [len(str(r[col_idx - 1])) for r in rows]
        ws.column_dimensions[get_column_letter(col_idx)].width = max(widths) + 4
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@app.route("/settings")
@login_required
def settings_page():
    return render_template("settings.html", settings=db.get_all_settings(), users=db.list_users())


@app.route("/settings/business", methods=["POST"])
@login_required
def settings_business():
    name = request.form.get("business_name", "").strip()
    currency = request.form.get("currency_symbol", "").strip()
    if not name or not currency:
        flash("Business name and currency symbol cannot be empty.", "error")
        return redirect(url_for("settings_page"))
    db.set_settings({
        "business_name": name,
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
        "address": request.form.get("address", "").strip(),
        "currency_symbol": currency,
        "receipt_footer": request.form.get("receipt_footer", "").strip(),
    })
    flash("Business settings updated.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/password", methods=["POST"])
@login_required
def settings_password():
    user = current_user()
    new_pw = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    if len(new_pw) < 4:
        flash("Password should be at least 4 characters.", "error")
    elif new_pw != confirm:
        flash("Passwords do not match.", "error")
    else:
        db.change_password(user["id"], new_pw)
        flash("Your password has been updated.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/users/add", methods=["POST"])
@login_required
@admin_required
def settings_add_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "staff")
    if not username or not password:
        flash("Username and password are required.", "error")
    elif db.username_exists(username):
        flash("That username is already taken.", "error")
    elif len(password) < 4:
        flash("Password should be at least 4 characters.", "error")
    else:
        db.create_user(username, password, role)
        flash(f"User '{username}' added.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def settings_delete_user(user_id):
    target = next((u for u in db.list_users() if u["id"] == user_id), None)
    me = current_user()
    if target is None:
        flash("User not found.", "error")
    elif target["username"] == me["username"]:
        flash("You cannot delete the account you're logged in with.", "error")
    elif target["role"] == "admin" and db.count_admins() <= 1:
        flash("You cannot delete the last remaining admin account.", "error")
    else:
        db.delete_user(user_id)
        flash(f"User '{target['username']}' deleted.", "success")
    return redirect(url_for("settings_page"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)