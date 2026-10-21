import os
import time
import sqlite3
import logging
import psycopg2

from datetime import datetime, timedelta
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash
)
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
MIN_WITHDRAWAL_POINTS = 6000

logging.basicConfig(level=logging.INFO)


def get_db():
    if DATABASE_URL:
        return psycopg2.connect(
            DATABASE_URL,
            sslmode="require",
            connect_timeout=10
        )

    conn = sqlite3.connect("poly_earn.db", timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


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
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE
                )
            """)
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS points INTEGER NOT NULL DEFAULT 0
            """)
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS ads_watched_count INTEGER NOT NULL DEFAULT 0
            """)
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS last_ad_time TIMESTAMP
            """)
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    points INTEGER NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP
                )
            """)
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
                    is_admin INTEGER NOT NULL DEFAULT 0
                )
            """)
            cursor.execute("PRAGMA table_info(users)")
            columns = {row[1] for row in cursor.fetchall()}

            for name, definition in [
                ("points", "INTEGER NOT NULL DEFAULT 0"),
                ("ads_watched_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_ad_time", "TEXT"),
                ("is_admin", "INTEGER NOT NULL DEFAULT 0")
            ]:
                if name not in columns:
                    cursor.execute(
                        f"ALTER TABLE users ADD COLUMN {name} {definition}"
                    )

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    points INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processed_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
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

        if ADMIN_EMAIL:
            p = "%s" if DATABASE_URL else "?"
            admin_value = "TRUE" if DATABASE_URL else "1"
            cursor.execute(
                f"UPDATE users SET is_admin = {admin_value} WHERE LOWER(email) = {p}",
                (ADMIN_EMAIL,)
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


def record_admin_login_attempt(email, success):
    normalized_email = (email or "").strip().lower()
    if not ADMIN_EMAIL or normalized_email != ADMIN_EMAIL:
        return

    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        p = "%s" if DATABASE_URL else "?"
        cursor.execute(
            f"""INSERT INTO admin_login_history
                (email, success, ip_address, user_agent)
                VALUES ({p}, {p}, {p}, {p})""",
            (
                normalized_email,
                bool(success) if DATABASE_URL else int(bool(success)),
                request.remote_addr,
                (request.headers.get("User-Agent") or "")[:250]
            )
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


def parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.fromisoformat(str(value))


def is_admin_user():
    if "user_id" not in session or not ADMIN_EMAIL:
        return False

    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        p = "%s" if DATABASE_URL else "?"
        cursor.execute(
            f"SELECT email FROM users WHERE id = {p}",
            (session["user_id"],)
        )
        row = cursor.fetchone()
        email = row[0] if row else None
        return bool(email and email.strip().lower() == ADMIN_EMAIL)
    except Exception:
        app.logger.exception("Admin authorization check failed.")
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_user_ad_status(user_id):
    conn = get_db()
    cursor = conn.cursor()
    try:
        p = "%s" if DATABASE_URL else "?"
        cursor.execute(
            f"SELECT ads_watched_count, last_ad_time FROM users WHERE id = {p}",
            (user_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None

        count = row[0] or 0
        last_time = parse_datetime(row[1])

        if last_time and datetime.now() - last_time >= AD_WINDOW:
            cursor.execute(
                f"UPDATE users SET ads_watched_count = 0 WHERE id = {p}",
                (user_id,)
            )
            conn.commit()
            count, last_time = 0, None

        return count, last_time
    finally:
        cursor.close()
        conn.close()


@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
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
        try:
            p = "%s" if DATABASE_URL else "?"
            cursor.execute(
                f"INSERT INTO users (username, email, password) VALUES ({p}, {p}, {p})",
                (username, email, generate_password_hash(password))
            )
            conn.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
        except Exception:
            conn.rollback()
            app.logger.exception("Registration failed.")
            flash("Registration failed. Username or email may already exist.", "error")
        finally:
            cursor.close()
            conn.close()

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        conn = get_db()
        cursor = conn.cursor()
        try:
            p = "%s" if DATABASE_URL else "?"
            cursor.execute(
                f"SELECT id, username, email, password FROM users WHERE LOWER(email) = {p}",
                (email,)
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


# SPECIAL ADMIN LOGIN PAGE
@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if not ADMIN_EMAIL:
        flash("Admin login is not configured. Set ADMIN_EMAIL in Render environment variables.", "error")
        return render_template("admin_login.html")

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        conn = None
        cursor = None
        success = False

        try:
            conn = get_db()
            cursor = conn.cursor()
            p = "%s" if DATABASE_URL else "?"
            cursor.execute(
                f"SELECT id, username, password FROM users WHERE LOWER(email) = {p}",
                (ADMIN_EMAIL,)
            )
            user = cursor.fetchone()

            success = bool(
                email == ADMIN_EMAIL
                and user
                and check_password_hash(user[2], password)
            )
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
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    return render_template("admin_login.html")


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    try:
        p = "%s" if DATABASE_URL else "?"
        cursor.execute(
            f"SELECT points, ads_watched_count, last_ad_time FROM users WHERE id = {p}",
            (session["user_id"],)
        )
        row = cursor.fetchone()
        if not row:
            session.clear()
            return redirect(url_for("login"))

        points, count, last_time = row
        points = points or 0
        count = count or 0
        last_time = parse_datetime(last_time)

        if last_time and datetime.now() - last_time >= AD_WINDOW:
            cursor.execute(
                f"UPDATE users SET ads_watched_count = 0 WHERE id = {p}",
                (session["user_id"],)
            )
            conn.commit()
            count = 0

        return render_template(
            "dashboard.html",
            username=session.get("username", "User"),
            points=points,
            usd_value=round(points * 0.005, 2),
            ads_watched=count
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
    try:
        status = get_user_ad_status(session["user_id"])
        if status is None:
            session.clear()
            return redirect(url_for("login"))
        if status[0] >= MAX_ADS:
            flash("You have reached 25 ads in this 2-hour window.", "error")
            return redirect(url_for("dashboard"))

        session["ad_page_opened_at"] = time.time()
        return render_template("watch_ad.html")
    except Exception:
        app.logger.exception("Watch-ad page failed.")
        flash("Could not open the ad page.", "error")
        return redirect(url_for("dashboard"))


@app.route("/claim-reward", methods=["POST"])
def claim_reward():
    if "user_id" not in session:
        return redirect(url_for("login"))

    opened_at = session.get("ad_page_opened_at")
    if opened_at is None or time.time() - opened_at < 10:
        flash("Please wait at least 10 seconds before claiming.", "error")
        return redirect(url_for("watch_ad"))

    conn = get_db()
    cursor = conn.cursor()
    try:
        p = "%s" if DATABASE_URL else "?"
        if DATABASE_URL:
            cursor.execute(
                "SELECT points, ads_watched_count, last_ad_time FROM users WHERE id = %s FOR UPDATE",
                (session["user_id"],)
            )
        else:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "SELECT points, ads_watched_count, last_ad_time FROM users WHERE id = ?",
                (session["user_id"],)
            )

        row = cursor.fetchone()
        if not row:
            conn.rollback()
            session.clear()
            return redirect(url_for("login"))

        points, count, last_time = row
        count = count or 0
        last_time = parse_datetime(last_time)
        if last_time and datetime.now() - last_time >= AD_WINDOW:
            count = 0

        if count >= MAX_ADS:
            conn.rollback()
            session.pop("ad_page_opened_at", None)
            flash("You have reached the 25-ad limit.", "error")
            return redirect(url_for("dashboard"))

        now = datetime.now()
        timestamp = now if DATABASE_URL else now.isoformat(sep=" ")
        cursor.execute(
            f"UPDATE users SET points = points + {p}, ads_watched_count = {p}, last_ad_time = {p} WHERE id = {p}",
            (POINTS_PER_AD, count + 1, timestamp, session["user_id"])
        )
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


@app.route("/withdraw", methods=["POST"])
def withdraw():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    p = "%s" if DATABASE_URL else "?"
    try:
        if DATABASE_URL:
            cursor.execute(
                "SELECT points FROM users WHERE id = %s FOR UPDATE",
                (session["user_id"],)
            )
        else:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "SELECT points FROM users WHERE id = ?",
                (session["user_id"],)
            )

        row = cursor.fetchone()
        if not row:
            conn.rollback()
            session.clear()
            return redirect(url_for("login"))

        points = row[0] or 0
        if points < MIN_WITHDRAWAL_POINTS:
            conn.rollback()
            flash("Minimum withdrawal is 6,000 points ($30).", "error")
            return redirect(url_for("dashboard"))

        cursor.execute(
            f"SELECT id FROM withdrawals WHERE user_id = {p} AND status = 'pending'",
            (session["user_id"],)
        )
        if cursor.fetchone():
            conn.rollback()
            flash("You already have a pending withdrawal request.", "error")
            return redirect(url_for("dashboard"))

        cursor.execute(
            f"UPDATE users SET points = points - {p} WHERE id = {p}",
            (points, session["user_id"])
        )
        cursor.execute(
            f"INSERT INTO withdrawals (user_id, points, status) VALUES ({p}, {p}, 'pending')",
            (session["user_id"], points)
        )
        conn.commit()
        flash("Withdrawal request submitted for review.", "success")
    except Exception:
        conn.rollback()
        app.logger.exception("Withdrawal request failed.")
        flash("Could not submit your withdrawal request.", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("dashboard"))


@app.route("/admin")
def admin_dashboard():
    if "user_id" not in session:
        return redirect(url_for("admin_login"))

    if not is_admin_user():
        flash("You are not authorized to access the admin dashboard.", "error")
        return redirect(url_for("dashboard"))

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, username, email, points FROM users ORDER BY id DESC"
        )
        users = cursor.fetchall()
        cursor.execute("""
            SELECT w.id, u.username, u.email, w.points, w.status, w.created_at
            FROM withdrawals w
            JOIN users u ON u.id = w.user_id
            ORDER BY w.id DESC
        """)
        withdrawals = cursor.fetchall()

        return render_tem
