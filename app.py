import os
import time
import sqlite3
import logging
import secrets
import psycopg2

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from flask import Flask, render_template, request, redirect, url_for, session, flash
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
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
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
        if window_start and datetime.now() - window_start >= AD_WINDOW:
            cursor.execute(
                f"""UPDATE users SET ads_watched_count = 0,
                    ad_window_started_at = NULL WHERE id = {p}""",
                (user_id,),
            )
            conn.commit()
            count, window_start = 0, None
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
        usd_balance = float(row[3] or 0)
        if window_start and datetime.now() - window_start >= AD_WINDOW:
            cursor.execute(
                f"""UPDATE users SET ads_watched_count = 0,
                    ad_window_started_at = NULL WHERE id = {p}""",
                (session["user_id"],),
            )
            conn.commit()
            count = 0
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
        now = datetime.now()
        if window_start and now - window_start >= AD_WINDOW:
            count, window_start = 0, None
        if count >= MAX_ADS:
            conn.rollback()
            flash("You have reached 25 ads in this 2-hour window.", "error")
            return redirect(url_for("tasks"))
        if window_start is None:
            timestamp = now if DATABASE_URL else now.isoformat(sep=" ")
            cursor.execute(
                f"""UPDATE users SET ads_watched_count = {p},
                    ad_window_started_at = {p} WHERE id = {p}""",
                (count, timestamp, session["user_id"]),
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
    """Start the server-side 30-second wait for the current ad page."""
    if "user_id" not in session:
        return jsonify({"ok": False, "message": "Please log in again."}), 401

    session["ad_page_opened_at"] = time.time()
    return jsonify({"ok": True, "wait_seconds": AD_WATCH_SECONDS})


@app.route("/claim-reward", methods=["POST"])
def claim_reward():
    if "user_id" not in session:
        return redirect(url_for("login"))
    opened_at = session.get("ad_page_opened_at")
    if opened_at is None or time.time() - opened_at < AD_WATCH_SECONDS:
        flash(
            f"Please watch the ad for at least {AD_WATCH_SECONDS} seconds before claiming.",
            "error"
        )
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
        if window_start and now - window_start >= AD_WINDOW:
            count, window_start = 0, None
        if count >= MAX_ADS:
            conn.rollback()
            session.pop("ad_page_opened_at", None)
            flash("You have reached the 25-ad limit.", "error")
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
    return redirect(url_for("dashboard"))


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


@app.route("/admin")
def admin_dashboard():
    if not is_admin_user():
        return redirect(url_for("admin_login"))
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, username, email, points, usd_balance FROM users ORDER BY id DESC")
        users = cursor.fetchall()
        cursor.execute(
            """SELECT w.id, u.username, w.points, w.status, w.created_at,
                      w.amount_usd, w.wallet_address, w.token, w.network
               FROM withdrawals w JOIN users u ON u.id = w.user_id ORDER BY w.id DESC"""
        )
        withdrawals = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COALESCE(SUM(usd_balance), 0) FROM users")
        total_usd_balance = cursor.fetchone()[0]
        return render_template("admin.html", users=users, withdrawals=withdrawals,
                               total_users=total_users, total_usd_balance=total_usd_balance)
    finally:
        cursor.close()
        conn.close()


@app.route("/admin/process-withdrawal/<int:withdrawal_id>", methods=["POST"])
def process_withdrawal(withdrawal_id):
    if not is_admin_user():
        return redirect(url_for("admin_login"))
    action = (request.form.get("action") or "").strip().lower()
    if action not in ("approve", "reject"):
        flash("Invalid withdrawal action.", "error")
        return redirect(url_for("admin_dashboard"))
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
            return redirect(url_for("admin_dashboard"))
        user_id, points = withdrawal[1], int(withdrawal[2] or 0)
        status = str(withdrawal[3] or "").lower()
        amount_usd = Decimal(str(withdrawal[4] or 0))
        if status != "pending":
            conn.rollback()
            flash("This withdrawal has already been processed.", "error")
            return redirect(url_for("admin_dashboard"))

        new_status = "approved" if action == "approve" else "rejected"
        cursor.execute(
            f"UPDATE withdrawals SET status = {p}, processed_at = CURRENT_TIMESTAMP WHERE id = {p} AND status = 'pending'",
            (new_status, withdrawal_id),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            flash("Withdrawal status changed. Refresh and try again.", "error")
            return redirect(url_for("admin_dashboard"))

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
        return redirect(url_for("admin_dashboard"))
    except Exception:
        conn.rollback()
        app.logger.exception("Admin withdrawal processing failed.")
        flash("Could not process withdrawal. Please try again.", "error")
        return redirect(url_for("admin_dashboard"))
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


# Initialize database tables when the application starts.
init_db()
