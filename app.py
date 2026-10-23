import os
import time
import sqlite3
import logging
import secrets
import psycopg2

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

ADMIN_EMAIL = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()

MAX_ADS = 25
AD_WINDOW = timedelta(hours=2)
POINTS_PER_AD = 3
AD_WATCH_SECONDS = 30
REFERRAL_POINTS = 50
MIN_REDEEM_POINTS = 100
POINT_USD_RATE = Decimal("0.005")  # 200 points = $1.00
MIN_WITHDRAWAL_USD = Decimal("50.00")
MIN_ACTIVE_REFERRALS = 3
WITHDRAWAL_TOKEN = "USDT"
WITHDRAWAL_NETWORK = "Polygon"

logging.basicConfig(level=logging.INFO)


def get_db():
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL, sslmode="require", connect_timeout=10)
    conn = sqlite3.connect("poly_earn.db", timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def placeholder():
    return "%s" if DATABASE_URL else "?"


def new_referral_code():
    return secrets.token_hex(5).upper()


def record_transaction(cursor, user_id, kind, points_change=0, usd_change=0, description=""):
    p = placeholder()
    cursor.execute(
        f"""INSERT INTO transactions
            (user_id, type, points_change, usd_change, description)
            VALUES ({p}, {p}, {p}, {p}, {p})""",
        (user_id, kind, points_change, float(usd_change), description),
    )


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    try:
        if DATABASE_URL:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(80) UNIQUE NOT NULL,
                    email VARCHAR(120) UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0,
                    ads_watched_count INTEGER NOT NULL DEFAULT 0,
                    last_ad_time TIMESTAMP,
                    ad_window_started_at TIMESTAMP,
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                    usd_balance NUMERIC(12,2) NOT NULL DEFAULT 0,
                    referral_code VARCHAR(32) UNIQUE,
                    referred_by INTEGER REFERENCES users(id),
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            user_migrations = [
                ("points", "INTEGER NOT NULL DEFAULT 0"),
                ("ads_watched_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_ad_time", "TIMESTAMP"),
                ("ad_window_started_at", "TIMESTAMP"),
                ("is_admin", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("usd_balance", "NUMERIC(12,2) NOT NULL DEFAULT 0"),
                ("referral_code", "VARCHAR(32)"),
                ("referred_by", "INTEGER REFERENCES users(id)"),
                ("created_at", "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"),
            ]
            for name, definition in user_migrations:
                cursor.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {name} {definition}")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    points INTEGER NOT NULL DEFAULT 0,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP,
                    amount_usd NUMERIC(12,2) NOT NULL DEFAULT 0,
                    wallet_address TEXT,
                    token VARCHAR(20) NOT NULL DEFAULT 'USDT',
                    network VARCHAR(30) NOT NULL DEFAULT 'Polygon'
                )
            """)
            for name, definition in [
                ("processed_at", "TIMESTAMP"),
                ("amount_usd", "NUMERIC(12,2) NOT NULL DEFAULT 0"),
                ("wallet_address", "TEXT"),
                ("token", "VARCHAR(20) NOT NULL DEFAULT 'USDT'"),
                ("network", "VARCHAR(30) NOT NULL DEFAULT 'Polygon'"),
            ]:
                cursor.execute(f"ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS {name} {definition}")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_login_history (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(120) NOT NULL,
                    success BOOLEAN NOT NULL,
                    ip_address TEXT,
                    user_agent TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    type VARCHAR(40) NOT NULL,
                    points_change INTEGER NOT NULL DEFAULT 0,
                    usd_change NUMERIC(12,2) NOT NULL DEFAULT 0,
                    description TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0,
                    ads_watched_count INTEGER NOT NULL DEFAULT 0,
                    last_ad_time TEXT,
                    ad_window_started_at TEXT,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    usd_balance NUMERIC NOT NULL DEFAULT 0,
                    referral_code TEXT UNIQUE,
                    referred_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (referred_by) REFERENCES users(id)
                )
            """)
            cursor.execute("PRAGMA table_info(users)")
            columns = {row[1] for row in cursor.fetchall()}
            for name, definition in [
                ("points", "INTEGER NOT NULL DEFAULT 0"),
                ("ads_watched_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_ad_time", "TEXT"),
                ("ad_window_started_at", "TEXT"),
                ("is_admin", "INTEGER NOT NULL DEFAULT 0"),
                ("usd_balance", "NUMERIC NOT NULL DEFAULT 0"),
                ("referral_code", "TEXT"),
                ("referred_by", "INTEGER"),
                ("created_at", "TEXT"),
            ]:
                if name not in columns:
                    cursor.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processed_at TEXT,
                    amount_usd NUMERIC NOT NULL DEFAULT 0,
                    wallet_address TEXT,
                    token TEXT NOT NULL DEFAULT 'USDT',
                    network TEXT NOT NULL DEFAULT 'Polygon',
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            cursor.execute("PRAGMA table_info(withdrawals)")
            columns = {row[1] for row in cursor.fetchall()}
            for name, definition in [
                ("processed_at", "TEXT"),
                ("amount_usd", "NUMERIC NOT NULL DEFAULT 0"),
                ("wallet_address", "TEXT"),
                ("token", "TEXT NOT NULL DEFAULT 'USDT'"),
                ("network", "TEXT NOT NULL DEFAULT 'Polygon'"),
            ]:
                if name not in columns:
                    cursor.execute(f"ALTER TABLE withdrawals ADD COLUMN {name} {definition}")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_login_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    ip_address TEXT,
                    user_agent TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    points_change INTEGER NOT NULL DEFAULT 0,
                    usd_change NUMERIC NOT NULL DEFAULT 0,
                    description TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

        p = placeholder()
        cursor.execute("SELECT id, referral_code FROM users")
        for row in cursor.fetchall():
            if not row[1]:
                cursor.execute(
                    f"UPDATE users SET referral_code = {p} WHERE id = {p}",
                    (new_referral_code(), row[0]),
                )
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS users_referral_code_unique
            ON users(referral_code)
        """)
        if ADMIN_EMAIL:
            admin_true = "TRUE" if DATABASE_URL else "1"
            cursor.execute(
                f"UPDATE users SET is_admin = {admin_true} WHERE LOWER(email) = {p}",
                (ADMIN_EMAIL,),
            )
        conn.commit()
        app.logger.info("Database initialized successfully.")
    except Exception:
        conn.rollback()
        app.logger.exception("Database initialization failed.")
        raise
    finally:
        cursor.close()
        conn.close()


def parse_datetime(value):
    """Parse stored timestamps into comparable naive datetimes."""
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
        # Normalize timezone-aware values to UTC, then remove tzinfo so they
        # can safely be compared with datetime.now() throughout this app.
        if parsed.tzinfo is not None:
            from datetime import timezone
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (ValueError, TypeError, OverflowError):
        app.logger.warning("Could not parse ad-window timestamp: %r", value)
        return None


def is_admin_user():
    if "user_id" not in session or not ADMIN_EMAIL:
        return False
    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()
    try:
        cursor.execute(f"SELECT email FROM users WHERE id = {p}", (session["user_id"],))
        row = cursor.fetchone()
        return bool(row and str(row[0]).strip().lower() == ADMIN_EMAIL)
    except Exception:
        app.logger.exception("Admin authorization check failed.")
        return False
    finally:
        cursor.close()
        conn.close()


def reset_expired_ad_window(cursor, user_id, count, window_start, now=None):
    """Reset the ad allowance after two hours, including stale legacy rows."""
    now = now or datetime.now()
    expired = bool(window_start and now - window_start >= AD_WINDOW)
    # Repair old/stale rows where the count reached 25 but the window timestamp
    # is missing or unreadable; otherwise that account can remain locked forever.
    stale_without_start = window_start is None and int(count or 0) >= MAX_ADS
    if expired or stale_without_start:
        p = placeholder()
        cursor.execute(
            f"UPDATE users SET ads_watched_count = 0, ad_window_started_at = NULL WHERE id = {p}",
            (user_id,),
        )
        return 0, None
    return int(count or 0), window_start


def get_user_ad_status(user_id):
    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()
    try:
        cursor.execute(
            f"SELECT ads_watched_count, ad_window_started_at FROM users WHERE id = {p}",
            (user_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        count = int(row[0] or 0)
        window_start = parse_datetime(row[1])
        count, window_start = reset_expired_ad_window(cursor, user_id, count, window_start)
        conn.commit()
        return count, window_start
    finally:
        cursor.close()
        conn.close()


def count_active_referrals(cursor, user_id):
    p = placeholder()
    cursor.execute(
        f"""SELECT COUNT(DISTINCT u.id)
            FROM users u
            WHERE u.referred_by = {p}
              AND EXISTS (
                  SELECT 1 FROM transactions t
                  WHERE t.user_id = u.id
                    AND t.type = 'ad_reward'
                    AND t.points_change > 0
              )""",
        (user_id,),
    )
    return int(cursor.fetchone()[0] or 0)


def record_admin_login_attempt(email, success):
    normalized_email = (email or "").strip().lower()
    if not ADMIN_EMAIL or normalized_email != ADMIN_EMAIL:
        return
    conn = cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        p = placeholder()
        cursor.execute(
            f"""INSERT INTO admin_login_history
                (email, success, ip_address, user_agent)
                VALUES ({p}, {p}, {p}, {p})""",
            (
                normalized_email,
                bool(success) if DATABASE_URL else int(bool(success)),
                request.remote_addr,
                (request.headers.get("User-Agent") or "")[:250],
            ),
        )
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Could not record admin login attempt.")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/")
def home():
    return redirect(url_for("dashboard" if "user_id" in session else "login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        code = (request.args.get("ref") or "").strip().upper()
        if code:
            session["referral_code"] = code
        return render_template("register.html")

    username = (request.form.get("username") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    if not username or not email or not password:
        flash("All fields are required.", "error")
        return redirect(url_for("register"))
    if len(username) > 80 or len(email) > 120:
        flash("Username or email is too long.", "error")
        return redirect(url_for("register"))
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("register"))

    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()
    try:
        referral_code = new_referral_code()
        referred_by = None
        invited_code = session.get("referral_code")
        if invited_code:
            cursor.execute(f"SELECT id FROM users WHERE referral_code = {p}", (invited_code,))
            inviter = cursor.fetchone()
            if inviter:
                referred_by = inviter[0]

        hashed_password = generate_password_hash(password)
        if DATABASE_URL:
            cursor.execute(
                """INSERT INTO users (username, email, password, referral_code, referred_by)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (username, email, hashed_password, referral_code, referred_by),
            )
            new_user_id = cursor.fetchone()[0]
        else:
            cursor.execute(
                """INSERT INTO users (username, email, password, referral_code, referred_by)
                   VALUES (?, ?, ?, ?, ?)""",
                (username, email, hashed_password, referral_code, referred_by),
            )
            new_user_id = cursor.lastrowid

        if referred_by and referred_by != new_user_id:
            cursor.execute(
                f"UPDATE users SET points = points + {p} WHERE id = {p}",
                (REFERRAL_POINTS, referred_by),
            )
            record_transaction(
                cursor, referred_by, "referral", points_change=REFERRAL_POINTS,
                description=f"Referral joined: {username}",
            )
        conn.commit()
        session.pop("referral_code", None)
        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("login"))
    except Exception:
        conn.rollback()
        app.logger.exception("Registration failed.")
        flash("Registration failed. Username or email may already exist.", "error")
        return redirect(url_for("register"))
    finally:
        cursor.close()
        conn.close()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        conn = get_db()
        cursor = conn.cursor()
        p = placeholder()
        try:
            cursor.execute(
                f"SELECT id, username, email, password FROM users WHERE LOWER(email) = {p}",
                (email,),
            )
            user = cursor.fetchone()
            login_ok = bool(user and check_password_hash(user[3], password))
            record_admin_login_attempt(email, login_ok)
            if login_ok:
                session.clear()
                session["user_id"] = user[0]
                session["username"] = user[1]
                return redirect(url_for("dashboard"))
            flash("Invalid email or password.", "error")
        except Exception:
            app.logger.exception("Login failed.")
            flash("Login error. Please try again.", "error")
        finally:
            cursor.close()
            conn.close()
    return render_template("login.html")


@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if not ADMIN_EMAIL:
        flash("Admin login is not configured. Set ADMIN_EMAIL in Render environment variables.", "error")
        return render_template("admin_login.html")
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        conn = get_db()
        cursor = conn.cursor()
        p = placeholder()
        try:
            cursor.execute(
                f"SELECT id, username, password FROM users WHERE LOWER(email) = {p}",
                (ADMIN_EMAIL,),
            )
            user = cursor.fetchone()
            success = bool(email == ADMIN_EMAIL and user and check_password_hash(user[2], password))
            record_admin_login_attempt(email, success)
            if success:
                session.clear()
                session["user_id"] = user[0]
                session["username"] = user[1]
                return redirect(url_for("admin_dashboard"))
            flash("Invalid admin email or password.", "error")
        except Exception:
            app.logger.exception("Admin login failed.")
            flash("Login failed. Please try again.", "error")
        finally:
            cursor.close()
            conn.close()
    return render_template("admin_login.html")


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()
    try:
        cursor.execute(
            f"""SELECT points, ads_watched_count, ad_window_started_at, usd_balance
                FROM users WHERE id = {p}""",
            (session["user_id"],),
        )
        row = cursor.fetchone()
        if not row:
            session.clear()
            return redirect(url_for("login"))
        points, count = int(row[0] or 0), int(row[1] or 0)
        window_start = parse_datetime(row[2])
        count, window_start = reset_expired_ad_window(cursor, session["user_id"], count, window_start)
        conn.commit()
        usd_balance = float(row[3] or 0)
        usd_value = float((Decimal(points) * POINT_USD_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        return render_template(
            "dashboard.html", username=session.get("username", "User"),
            points=points, usd_value=usd_value, usd_balance=usd_balance, ads_watched=count,
        )
    except Exception:
        app.logger.exception("Dashboard failed.")
        flash("Could not load your balance.", "error")
        return redirect(url_for("login"))
    finally:
        cursor.close()
        conn.close()


@app.route("/watch-ad")
def watch_ad():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()
    try:
        if DATABASE_URL:
            cursor.execute(
                "SELECT ads_watched_count, ad_window_started_at FROM users WHERE id = %s FOR UPDATE",
                (session["user_id"],),
            )
        else:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "SELECT ads_watched_count, ad_window_started_at FROM users WHERE id = ?",
                (session["user_id"],),
            )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            session.clear()
            return redirect(url_for("login"))
        count = int(row[0] or 0)
        window_start = parse_datetime(row[1])
        count, window_start = reset_expired_ad_window(cursor, session["user_id"], count, window_start)
        if count >= MAX_ADS:
            conn.commit()
            flash("You have reached 25 ads in this 2-hour window.", "error")
            return redirect(url_for("tasks"))
        if window_start is None:
            timestamp = datetime.now() if DATABASE_URL else datetime.now().isoformat(sep=" ")
            cursor.execute(
                f"UPDATE users SET ad_window_started_at = {p} WHERE id = {p}",
                (timestamp, session["user_id"]),
            )
        conn.commit()
        session["ad_page_opened_at"] = time.time()
        return render_template("watch_ad.html")
    except Exception:
        conn.rollback()
        app.logger.exception("Watch-ad page failed.")
        flash("Could not open the ad page.", "error")
        return redirect(url_for("dashboard"))
    finally:
        cursor.close()
        conn.close()


@app.route("/start-ad", methods=["POST"])
def start_ad():
    if "user_id" not in session:
        return jsonify({"ok": False, "message": "Please log in again."}), 401

    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()
    try:
        if DATABASE_URL:
            cursor.execute(
                "SELECT ads_watched_count, ad_window_started_at FROM users WHERE id = %s FOR UPDATE",
                (session["user_id"],),
            )
        else:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "SELECT ads_watched_count, ad_window_started_at FROM users WHERE id = ?",
                (session["user_id"],),
            )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            session.clear()
            return jsonify({"ok": False, "message": "Please log in again."}), 401

        count = int(row[0] or 0)
        window_start = parse_datetime(row[1])
        count, window_start = reset_expired_ad_window(cursor, session["user_id"], count, window_start)
        if count >= MAX_ADS:
            conn.commit()
            session.pop("ad_page_opened_at", None)
            return jsonify({
                "ok": False,
                "message": "You have reached 25 ads in this 2-hour window."
            }), 429

        if window_start is None:
            timestamp = datetime.now() if DATABASE_URL else datetime.now().isoformat(sep=" ")
            cursor.execute(
                f"UPDATE users SET ad_window_started_at = {p} WHERE id = {p}",
                (timestamp, session["user_id"]),
            )
        conn.commit()
        session["ad_page_opened_at"] = time.time()
        return jsonify({"ok": True, "wait_seconds": AD_WATCH_SECONDS})
    except Exception:
        conn.rollback()
        app.logger.exception("Could not start ad.")
        return jsonify({"ok": False, "message": "Could not start the ad. Please try again."}), 500
    finally:
        cursor.close()
        conn.close()


@app.route("/claim-reward", methods=["POST"])
def claim_reward():
    if "user_id" not in session:
        return redirect(url_for("login"))
    opened_at = session.get("ad_page_opened_at")
    if opened_at is None or time.time() - opened_at < AD_WATCH_SECONDS:
        flash(f"Please watch the ad for at least {AD_WATCH_SECONDS} seconds before claiming.", "error")
        return redirect(url_for("watch_ad"))

    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()
    try:
        if DATABASE_URL:
            cursor.execute(
                """SELECT points, ads_watched_count, ad_window_started_at
                   FROM users WHERE id = %s FOR UPDATE""",
                (session["user_id"],),
            )
        else:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                """SELECT points, ads_watched_count, ad_window_started_at
                   FROM users WHERE id = ?""",
                (session["user_id"],),
            )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            session.clear()
            return redirect(url_for("login"))

        count = int(row[1] or 0)
        window_start = parse_datetime(row[2])
        now = datetime.now()
        count, window_start = reset_expired_ad_window(cursor, session["user_id"], count, window_start)
        if count >= MAX_ADS:
            conn.commit()
            session.pop("ad_page_opened_at", None)
            flash("You have reached the 25-ad limit for this 2-hour window.", "error")
            return redirect(url_for("tasks"))

        if window_start is None:
            window_start = now
        window_timestamp = window_start if DATABASE_URL else window_start.isoformat(sep=" ")
        ad_timestamp = now if DATABASE_URL else now.isoformat(sep=" ")
        cursor.execute(
            f"""UPDATE users SET points = points + {p},
                ads_watched_count = {p}, ad_window_started_at = {p},
                last_ad_time = {p} WHERE id = {p}""",
            (POINTS_PER_AD, count + 1, window_timestamp, ad_timestamp, session["user_id"]),
        )
        record_transaction(cursor, session["user_id"], "ad_reward",
                           points_change=POINTS_PER_AD, description="Watch-ad reward")
        conn.commit()
        session.pop("ad_page_opened_at", None)
        flash("3 Poly Points added to your balance!", "success")
    except Exception:
        conn.rollback()
        app.logger.exception("Reward claim failed.")
        flash("Reward claim failed. Please try again.", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("tasks"))


@app.route("/tasks")
def tasks():
    if "user_id" not in session:
        return redirect(url_for("login"))
    status = get_user_ad_status(session["user_id"])
    if status is None:
        session.clear()
        return redirect(url_for("login"))
    count, window_start = status
    remaining_seconds = 0
    if window_start:
        elapsed = (datetime.now() - window_start).total_seconds()
        remaining_seconds = max(0, int(AD_WINDOW.total_seconds() - elapsed))
    return render_template("tasks.html", username=session.get("username", "User"),
                           ads_watched=count, remaining_seconds=remaining_seconds)


@app.route("/invite")
def invite():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()
    try:
        cursor.execute(f"SELECT referral_code, points FROM users WHERE id = {p}", (session["user_id"],))
        user = cursor.fetchone()
        if not user:
            session.clear()
            return redirect(url_for("login"))
        referral_link = url_for("register", ref=user[0], _external=True)
        cursor.execute(
            f"SELECT username, created_at FROM users WHERE referred_by = {p} ORDER BY id DESC",
            (session["user_id"],),
        )
        referrals = cursor.fetchall()
        active_referrals = count_active_referrals(cursor, session["user_id"])
        return render_template(
            "invite.html", referral_code=user[0], referral_link=referral_link,
            referrals=referrals, referral_count=len(referrals),
            active_referrals=active_referrals, referral_points=REFERRAL_POINTS,
        )
    except Exception:
        app.logger.exception("Invite page failed.")
        flash("Could not load your referral details.", "error")
        return redirect(url_for("dashboard"))
    finally:
        cursor.close()
        conn.close()


@app.route("/redeem", methods=["GET", "POST"])
def redeem():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()
    try:
        if request.method == "POST":
            raw_points = (request.form.get("points") or "").strip()
            try:
                points = int(raw_points)
            except (ValueError, TypeError):
                flash("Enter a valid number of points.", "error")
                return redirect(url_for("redeem"))
            if points < MIN_REDEEM_POINTS:
                flash(f"Minimum redemption is {MIN_REDEEM_POINTS} points.", "error")
                return redirect(url_for("redeem"))
            cursor.execute(f"SELECT points FROM users WHERE id = {p}", (session["user_id"],))
            user = cursor.fetchone()
            if not user:
                session.clear()
                return redirect(url_for("login"))
            if int(user[0] or 0) < points:
                flash("You do not have enough points.", "error")
                return redirect(url_for("redeem"))
            usd_amount = (Decimal(points) * POINT_USD_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            cursor.execute(
                f"UPDATE users SET points = points - {p}, usd_balance = usd_balance + {p} WHERE id = {p}",
                (points, float(usd_amount), session["user_id"]),
            )
            record_transaction(cursor, session["user_id"], "points_redeemed",
                               points_change=-points, usd_change=usd_amount,
                               description=f"Redeemed {points} points for ${usd_amount:.2f}")
            conn.commit()
            flash(f"{points} points redeemed for ${usd_amount:.2f}.", "success")
            return redirect(url_for("redeem"))
        cursor.execute(f"SELECT points, usd_balance FROM users WHERE id = {p}", (session["user_id"],))
        user = cursor.fetchone()
        if not user:
            session.clear()
            return redirect(url_for("login"))
        return render_template("redeem.html", points=int(user[0] or 0), usd_balance=float(user[1] or 0))
    except Exception:
        conn.rollback()
        app.logger.exception("Points redemption failed.")
        flash("Redemption failed. Please try again.", "error")
        return redirect(url_for("redeem"))
    finally:
        cursor.close()
        conn.close()


@app.route("/profile")
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()
    try:
        cursor.execute(
            f"""SELECT id, username, email, points, usd_balance, referral_code, created_at
                FROM users WHERE id = {p}""",
            (session["user_id"],),
        )
        user = cursor.fetchone()
        if not user:
            session.clear()
            return redirect(url_for("login"))
        return render_template("profile.html", user=user)
    finally:
        cursor.close()
        conn.close()


@app.route("/history")
def history():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()
    try:
        cursor.execute(
            f"""SELECT type, points_change, usd_change, description, created_at
                FROM transactions WHERE user_id = {p} ORDER BY id DESC""",
            (session["user_id"],),
        )
        transactions = cursor.fetchall()
        cursor.execute(
            f"""SELECT points, amount_usd, status, created_at, wallet_address, token, network
                FROM withdrawals WHERE user_id = {p} ORDER BY id DESC""",
            (session["user_id"],),
        )
        withdrawals = cursor.fetchall()
        return render_template("history.html", transactions=transactions, withdrawals=withdrawals)
    finally:
        cursor.close()
        conn.close()


@app.route("/withdraw", methods=["GET", "POST"])
def withdraw():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()
    try:
        if request.method == "GET":
            cursor.execute(f"SELECT usd_balance FROM users WHERE id = {p}", (session["user_id"],))
            user = cursor.fetchone()
            if not user:
                session.clear()
                return redirect(url_for("login"))
            active_referrals = count_active_referrals(cursor, session["user_id"])
            cursor.execute(
                f"""SELECT id FROM withdrawals WHERE user_id = {p}
                    AND status = 'pending' AND amount_usd > 0""",
                (session["user_id"],),
            )
            pending_withdrawal = cursor.fetchone() is not None
            return render_template(
                "withdraw.html", usd_balance=float(user[0] or 0),
                active_referrals=active_referrals,
                min_withdrawal=float(MIN_WITHDRAWAL_USD),
                min_active_referrals=MIN_ACTIVE_REFERRALS,
                pending_withdrawal=pending_withdrawal,
                token=WITHDRAWAL_TOKEN, network=WITHDRAWAL_NETWORK,
            )

        raw_amount = (request.form.get("amount") or "").strip()
        wallet_address = (request.form.get("wallet_address") or "").strip()
        try:
            amount = Decimal(raw_amount)
            if not amount.is_finite():
                raise InvalidOperation
            amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError, TypeError):
            flash("Enter a valid withdrawal amount.", "error")
            return redirect(url_for("withdraw"))

        if amount < MIN_WITHDRAWAL_USD:
            flash(f"Minimum withdrawal is ${MIN_WITHDRAWAL_USD:.2f} USDT.", "error")
            return redirect(url_for("withdraw"))
        if (len(wallet_address) != 42 or not wallet_address.startswith("0x")
                or any(ch not in "0123456789abcdefABCDEF" for ch in wallet_address[2:])):
            flash("Enter a valid Polygon wallet address beginning with 0x.", "error")
            return redirect(url_for("withdraw"))

        if DATABASE_URL:
            cursor.execute("SELECT usd_balance FROM users WHERE id = %s FOR UPDATE", (session["user_id"],))
        else:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("SELECT usd_balance FROM users WHERE id = ?", (session["user_id"],))
        user = cursor.fetchone()
        if not user:
            conn.rollback()
            session.clear()
            return redirect(url_for("login"))
        balance = Decimal(str(user[0] or 0))
        if balance < amount:
            conn.rollback()
            flash("Your USD wallet balance is not enough.", "error")
            return redirect(url_for("withdraw"))
        active_referrals = count_active_referrals(cursor, session["user_id"])
        if active_referrals < MIN_ACTIVE_REFERRALS:
            conn.rollback()
            flash(
                f"You need at least {MIN_ACTIVE_REFERRALS} active referrals. "
                f"You currently have {active_referrals}. Each referral must claim at least one ad reward.",
                "error",
            )
            return redirect(url_for("withdraw"))
        cursor.execute(
            f"""SELECT id FROM withdrawals WHERE user_id = {p}
                AND status = 'pending' AND amount_usd > 0""",
            (session["user_id"],),
        )
        if cursor.fetchone():
            conn.rollback()
            flash("You already have a pending USD withdrawal request.", "error")
            return redirect(url_for("withdraw"))

        created_at = datetime.now() if DATABASE_URL else datetime.now().isoformat(sep=" ")
        cursor.execute(
            f"""INSERT INTO withdrawals
                (user_id, points, status, created_at, amount_usd, wallet_address, token, network)
                VALUES ({p}, 0, 'pending', {p}, {p}, {p}, {p}, {p})""",
            (session["user_id"], created_at, float(amount), wallet_address, WITHDRAWAL_TOKEN, WITHDRAWAL_NETWORK),
        )
        cursor.execute(
            f"UPDATE users SET usd_balance = usd_balance - {p} WHERE id = {p}",
            (float(amount), session["user_id"]),
        )
        record_transaction(
            cursor, session["user_id"], "usd_withdrawal", usd_change=-amount,
            description=f"Pending {WITHDRAWAL_TOKEN} withdrawal on {WITHDRAWAL_NETWORK} to {wallet_address}",
        )
        conn.commit()
        flash(f"${amount:.2f} USDT withdrawal request submitted. It is pending admin review and payment.", "success")
        return redirect(url_for("withdraw"))
    except Exception:
        conn.rollback()
        app.logger.exception("USD withdrawal failed.")
        flash("Could not process your withdrawal. Please try again.", "error")
        return redirect(url_for("withdraw"))
    finally:
        cursor.close()
        conn.close()


def ensure_admin_extra_tables(cursor):
    """Create admin announcement/support tables using the active DB dialect."""
    if DATABASE_URL:
        cursor.execute("""CREATE TABLE IF NOT EXISTS announcements (
            id SERIAL PRIMARY KEY, title VARCHAR(180) NOT NULL, body TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS support_messages (
            id SERIAL PRIMARY KEY, user_id INTEGER, email VARCHAR(180), subject VARCHAR(180) NOT NULL,
            message TEXT NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'open',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP)""")
    else:
        cursor.execute("""CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS support_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, email TEXT, subject TEXT NOT NULL,
            message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, resolved_at TEXT)""")


def admin_page_guard():
    if not is_admin_user():
        return redirect(url_for("admin_login"))
    return None


@app.route("/admin")
def admin_dashboard():
    guard = admin_page_guard()
    if guard:
        return guard
    conn = get_db(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, username, email, points, usd_balance FROM users ORDER BY id DESC")
        users = cursor.fetchall()
        cursor.execute("""SELECT w.id, u.username, u.email, w.points, w.status, w.created_at,
                          w.amount_usd, w.wallet_address, w.token, w.network
                          FROM withdrawals w JOIN users u ON u.id=w.user_id ORDER BY w.id DESC""")
        withdrawals = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) FROM users"); total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COALESCE(SUM(usd_balance), 0) FROM users"); total_usd_balance = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM withdrawals WHERE LOWER(status)='pending'"); pending_withdrawals = cursor.fetchone()[0]
        cursor.execute("SELECT COALESCE(SUM(points),0) FROM users"); total_points = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM transactions"); total_transactions = cursor.fetchone()[0]
        return render_template("admin.html", users=users, withdrawals=withdrawals,
            total_users=total_users, total_usd_balance=total_usd_balance,
            pending_withdrawals=pending_withdrawals, total_points=total_points,
            total_transactions=total_transactions)
    finally:
        cursor.close(); conn.close()


@app.route("/admin/users")
def admin_users():
    guard = admin_page_guard()
    if guard: return guard
    conn=get_db(); cursor=conn.cursor()
    try:
        cursor.execute("SELECT id, username, email, points, usd_balance, created_at FROM users ORDER BY id DESC")
        return render_template("admin_users.html", users=cursor.fetchall())
    finally: cursor.close(); conn.close()


@app.route("/admin/withdrawals")
def admin_withdrawals():
    guard=admin_page_guard()
    if guard: return guard
    conn=get_db(); cursor=conn.cursor()
    try:
        cursor.execute("""SELECT w.id,u.username,u.email,w.points,w.status,w.created_at,
            w.amount_usd,w.wallet_address,w.token,w.network FROM withdrawals w
            JOIN users u ON u.id=w.user_id ORDER BY w.id DESC""")
        return render_template("admin_withdrawals.html", withdrawals=cursor.fetchall())
    finally: cursor.close(); conn.close()


@app.route("/admin/transactions")
def admin_transactions():
    guard=admin_page_guard()
    if guard: return guard
    conn=get_db(); cursor=conn.cursor()
    try:
        cursor.execute("""SELECT t.id,u.username,u.email,t.type,t.points_change,t.usd_change,t.description,t.created_at
            FROM transactions t JOIN users u ON u.id=t.user_id ORDER BY t.id DESC LIMIT 500""")
        return render_template("admin_transactions.html", transactions=cursor.fetchall())
    finally: cursor.close(); conn.close()


@app.route("/admin/settings")
def admin_settings():
    guard=admin_page_guard()
    if guard: return guard
    return render_template("admin_settings.html", settings={
        "points_per_ad": POINTS_PER_AD, "ad_watch_seconds": AD_WATCH_SECONDS,
        "max_ads": MAX_ADS, "ad_window_hours": int(AD_WINDOW.total_seconds() // 3600),
        "referral_points": REFERRAL_POINTS, "min_redeem_points": MIN_REDEEM_POINTS,
        "point_usd_rate": str(POINT_USD_RATE), "min_withdrawal_usd": str(MIN_WITHDRAWAL_USD),
        "min_active_referrals": MIN_ACTIVE_REFERRALS, "token": WITHDRAWAL_TOKEN,
        "network": WITHDRAWAL_NETWORK})


@app.route("/admin/announcements", methods=["GET", "POST"])
def admin_announcements():
    guard=admin_page_guard()
    if guard: return guard
    conn=get_db(); cursor=conn.cursor(); p=placeholder()
    try:
        ensure_admin_extra_tables(cursor)
        if request.method == "POST":
            title=(request.form.get("title") or "").strip()
            body=(request.form.get("body") or "").strip()
            if not title or not body:
                flash("Enter both announcement title and message.", "error")
            elif len(title)>180:
                flash("Announcement title must be 180 characters or fewer.", "error")
            else:
                cursor.execute(f"INSERT INTO announcements (title,body) VALUES ({p},{p})", (title,body))
                conn.commit(); flash("Announcement published in the admin list.", "success")
            return redirect(url_for("admin_announcements"))
        cursor.execute("SELECT id,title,body,created_at FROM announcements ORDER BY id DESC")
        announcements=cursor.fetchall(); conn.commit()
        return render_template("admin_announcements.html", announcements=announcements)
    finally: cursor.close(); conn.close()


@app.route("/admin/announcements/<int:announcement_id>/delete", methods=["POST"])
def admin_delete_announcement(announcement_id):
    guard=admin_page_guard()
    if guard: return guard
    conn=get_db(); cursor=conn.cursor(); p=placeholder()
    try:
        ensure_admin_extra_tables(cursor)
        cursor.execute(f"DELETE FROM announcements WHERE id={p}", (announcement_id,)); conn.commit()
        flash("Announcement deleted.", "success")
        return redirect(url_for("admin_announcements"))
    finally: cursor.close(); conn.close()


@app.route("/admin/support", methods=["GET", "POST"])
def admin_support():
    guard=admin_page_guard()
    if guard: return guard
    conn=get_db(); cursor=conn.cursor(); p=placeholder()
    try:
        ensure_admin_extra_tables(cursor)
        if request.method == "POST":
            message_id=request.form.get("message_id", type=int)
            action=(request.form.get("action") or "").lower()
            if message_id and action in ("resolve", "reopen"):
                status="resolved" if action=="resolve" else "open"
                cursor.execute(f"UPDATE support_messages SET status={p}, resolved_at=" + ("CURRENT_TIMESTAMP" if action=="resolve" else "NULL") + f" WHERE id={p}", (status,message_id))
                conn.commit(); flash("Support status updated.", "success")
            return redirect(url_for("admin_support"))
        cursor.execute("""SELECT id,user_id,email,subject,message,status,created_at,resolved_at
            FROM support_messages ORDER BY CASE WHEN status='open' THEN 0 ELSE 1 END,id DESC""")
        messages=cursor.fetchall(); conn.commit()
        return render_template("admin_support.html", messages=messages)
    finally: cursor.close(); conn.close()


@app.route("/admin/security")
def admin_security():
    guard=admin_page_guard()
    if guard: return guard
    return render_template("admin_security.html")


@app.route("/admin/reports")
def admin_reports():
    guard=admin_page_guard()
    if guard: return guard
    conn=get_db(); cursor=conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM users"); total_users=cursor.fetchone()[0]
        cursor.execute("SELECT COALESCE(SUM(points),0) FROM users"); total_points=cursor.fetchone()[0]
        cursor.execute("SELECT COALESCE(SUM(usd_balance),0) FROM users"); total_usd=cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM withdrawals"); total_withdrawals=cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM withdrawals WHERE LOWER(status)='pending'"); pending=cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM withdrawals WHERE LOWER(status)='approved'"); approved=cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM withdrawals WHERE LOWER(status)='rejected'"); rejected=cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM transactions"); transactions=cursor.fetchone()[0]
        return render_template("admin_reports.html", total_users=total_users,total_points=total_points,
            total_usd=total_usd,total_withdrawals=total_withdrawals,pending=pending,
            approved=approved,rejected=rejected,transactions=transactions)
    finally: cursor.close(); conn.close()


@app.route("/admin/process-withdrawal/<int:withdrawal_id>", methods=["POST"])
def process_withdrawal(withdrawal_id):
    if not is_admin_user():
        return redirect(url_for("admin_login"))
    action = (request.form.get("action") or request.args.get("action") or "").strip().lower()
    if action not in ("approve", "reject"):
        flash("Invalid withdrawal action.", "error")
        return redirect(url_for("admin_withdrawals"))
    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()
    try:
        if DATABASE_URL:
            cursor.execute(
                f"SELECT id, user_id, points, status, amount_usd FROM withdrawals WHERE id = {p} FOR UPDATE",
                (withdrawal_id,),
            )
        else:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                f"SELECT id, user_id, points, status, amount_usd FROM withdrawals WHERE id = {p}",
                (withdrawal_id,),
            )
        withdrawal = cursor.fetchone()
        if not withdrawal:
            conn.rollback()
            flash("Withdrawal request not found.", "error")
            return redirect(url_for("admin_withdrawals"))
        user_id, points = withdrawal[1], int(withdrawal[2] or 0)
        status = str(withdrawal[3] or "").lower()
        amount_usd = Decimal(str(withdrawal[4] or 0))
        if status != "pending":
            conn.rollback()
            flash("This withdrawal has already been processed.", "error")
            return redirect(url_for("admin_withdrawals"))

        new_status = "approved" if action == "approve" else "rejected"
        cursor.execute(
            f"UPDATE withdrawals SET status = {p}, processed_at = CURRENT_TIMESTAMP WHERE id = {p} AND status = 'pending'",
            (new_status, withdrawal_id),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            flash("Withdrawal status changed. Refresh and try again.", "error")
            return redirect(url_for("admin_withdrawals"))

        if action == "reject":
            if amount_usd > 0:
                cursor.execute(
                    f"UPDATE users SET usd_balance = usd_balance + {p} WHERE id = {p}",
                    (float(amount_usd), user_id),
                )
                record_transaction(cursor, user_id, "withdrawal_refund", usd_change=amount_usd,
                                   description=f"Refund for rejected withdrawal #{withdrawal_id}")
            elif points > 0:
                cursor.execute(
                    f"UPDATE users SET points = points + {p} WHERE id = {p}",
                    (points, user_id),
                )
                record_transaction(cursor, user_id, "withdrawal_refund", points_change=points,
                                   description=f"Refund for rejected withdrawal #{withdrawal_id}")
        else:
            record_transaction(cursor, user_id, "withdrawal_approved", usd_change=Decimal("0"),
                               description=f"Admin approved withdrawal #{withdrawal_id} for ${amount_usd:.2f}")
        conn.commit()
        if action == "approve":
            flash("Withdrawal marked approved. Send the actual USDT payment separately.", "success")
        else:
            flash("Withdrawal rejected and balance refunded.", "success")
        return redirect(url_for("admin_withdrawals"))
    except Exception:
        conn.rollback()
        app.logger.exception("Admin withdrawal processing failed.")
        flash("Could not process withdrawal. Please try again.", "error")
        return redirect(url_for("admin_withdrawals"))
    finally:
        cursor.close()
        conn.close()


@app.route("/admin/login-history")
def admin_login_history():
    if not is_admin_user():
        return redirect(url_for("admin_login"))
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT id, email, success, ip_address, user_agent, created_at
               FROM admin_login_history ORDER BY id DESC"""
        )
        login_history = cursor.fetchall()
        return render_template("admin_login_history.html", login_history=login_history)
    finally:
        cursor.close()
        conn.close()


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))



@app.route("/admin/users/<int:user_id>")
def admin_user_detail(user_id):
    guard = admin_page_guard()
    if guard:
        return guard

    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()

    try:
        cursor.execute(
            f"""SELECT id, username, email, points, usd_balance,
                       ads_watched_count, referral_code, referred_by, created_at
                FROM users
                WHERE id = {p}""",
            (user_id,)
        )
        user = cursor.fetchone()

        if not user:
            flash("User account not found.", "error")
            return redirect(url_for("admin_users"))

        cursor.execute(
            f"SELECT COUNT(*) FROM users WHERE referred_by = {p}",
            (user_id,)
        )
        referral_count = cursor.fetchone()[0]

        cursor.execute(
            f"""SELECT type, points_change, usd_change, description, created_at
                FROM transactions
                WHERE user_id = {p}
                ORDER BY id DESC LIMIT 50""",
            (user_id,)
        )
        transactions = cursor.fetchall()

        cursor.execute(
            f"""SELECT id, amount_usd, status, wallet_address, token,
                       network, created_at, processed_at
                FROM withdrawals
                WHERE user_id = {p}
                ORDER BY id DESC""",
            (user_id,)
        )
        withdrawals = cursor.fetchall()

        return render_template(
            "admin_user_detail.html",
            user=user,
            referral_count=referral_count,
            transactions=transactions,
            withdrawals=withdrawals
        )

    except Exception:
        app.logger.exception("Could not load admin user details.")
        flash("Could not load this user's details.", "error")
        return redirect(url_for("admin_users"))

    finally:
        cursor.close()
        conn.close()
        

@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    if "user_id" not in session:
        flash("Please log in before changing your password.", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        current_password = request.form.get("current_password") or ""
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if not current_password or not new_password or not confirm_password:
            flash("Please fill in all password fields.", "error")
            return redirect(url_for("change_password"))

        if len(new_password) < 8:
            flash("New password must be at least 8 characters.", "error")
            return redirect(url_for("change_password"))

        if new_password != confirm_password:
            flash("The new passwords do not match.", "error")
            return redirect(url_for("change_password"))

        conn = get_db()
        cursor = conn.cursor()
        p = placeholder()

        try:
            cursor.execute(
                f"SELECT password FROM users WHERE id = {p}",
                (session["user_id"],)
            )
            user = cursor.fetchone()

            if not user:
                session.clear()
                return redirect(url_for("login"))

            if not check_password_hash(user[0], current_password):
                flash("Your current password is incorrect.", "error")
                return redirect(url_for("change_password"))

            hashed_password = generate_password_hash(new_password)
            cursor.execute(
                f"UPDATE users SET password = {p} WHERE id = {p}",
                (hashed_password, session["user_id"])
            )
            conn.commit()

            flash("Password changed successfully.", "success")
            return redirect(url_for("profile"))

        except Exception:
            conn.rollback()
            app.logger.exception("Password change failed.")
            flash("Could not change your password. Please try again.", "error")
            return redirect(url_for("change_password"))

        finally:
            cursor.close()
            conn.close()

    return render_template("reset_password.html")


# Initialize database tables when the application starts.
init_db()

