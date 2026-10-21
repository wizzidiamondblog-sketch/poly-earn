
import os
import time
import sqlite3
import logging
import secrets
import psycopg2

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
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
REFERRAL_POINTS = 50
MIN_REDEEM_POINTS = 100
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


def placeholder():
    return "%s" if DATABASE_URL else "?"


def new_referral_code():
    return secrets.token_hex(5).upper()


def record_transaction(cursor, user_id, kind, points_change=0,
                       usd_change=0, description=""):
    p = placeholder()
    cursor.execute(
        f"""
        INSERT INTO transactions
        (user_id, type, points_change, usd_change, description)
        VALUES ({p}, {p}, {p}, {p}, {p})
        """,
        (
            user_id, kind, points_change,
            float(usd_change), description
        )
    )


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()

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
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                    usd_balance NUMERIC(12,2) NOT NULL DEFAULT 0,
                    referral_code VARCHAR(32),
                    referred_by INTEGER REFERENCES users(id),
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            migrations = [
                ("points", "INTEGER NOT NULL DEFAULT 0"),
                ("ads_watched_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_ad_time", "TIMESTAMP"),
                ("is_admin", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("usd_balance", "NUMERIC(12,2) NOT NULL DEFAULT 0"),
                ("referral_code", "VARCHAR(32)"),
                ("referred_by", "INTEGER REFERENCES users(id)"),
                ("created_at", "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP")
            ]

            for name, definition in migrations:
                cursor.execute(
                    f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {name} {definition}"
                )

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
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    usd_balance NUMERIC NOT NULL DEFAULT 0,
                    referral_code TEXT,
                    referred_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (referred_by) REFERENCES users(id)
                )
            """)

            cursor.execute("PRAGMA table_info(users)")
            columns = {row[1] for row in cursor.fetchall()}

            migrations = [
                ("points", "INTEGER NOT NULL DEFAULT 0"),
                ("ads_watched_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_ad_time", "TEXT"),
                ("is_admin", "INTEGER NOT NULL DEFAULT 0"),
                ("usd_balance", "NUMERIC NOT NULL DEFAULT 0"),
                ("referral_code", "TEXT"),
                ("referred_by", "INTEGER"),
                ("created_at", "TEXT")
            ]

            for name, definition in migrations:
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

        cursor.execute("SELECT id, referral_code FROM users")
        existing_users = cursor.fetchall()

        for user in existing_users:
            if not user[1]:
                code = new_referral_code()
                cursor.execute(
                    f"UPDATE users SET referral_code = {p} WHERE id = {p}",
                    (code, user[0])
                )

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS users_referral_code_unique
            ON users(referral_code)
        """)

        if ADMIN_EMAIL:
            admin_value = "TRUE" if DATABASE_URL else "1"
            cursor.execute(
                f"""
                UPDATE users SET is_admin = {admin_value}
                WHERE LOWER(email) = {p}
                """,
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
        cursor.execute(
            f"SELECT email FROM users WHERE id = {p}",
            (session["user_id"],)
        )
        row = cursor.fetchone()
        return bool(row and row[0].strip().lower() == ADMIN_EMAIL)

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
            f"""
            SELECT ads_watched_count, last_ad_time
            FROM users WHERE id = {p}
            """,
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
            count = 0

        return count, last_time

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
        p = placeholder()

        cursor.execute(
            f"""
            INSERT INTO admin_login_history
            (email, success, ip_address, user_agent)
            VALUES ({p}, {p}, {p}, {p})
            """,
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

@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


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
            cursor.execute(
                f"SELECT id FROM users WHERE referral_code = {p}",
                (invited_code,)
            )
            inviter = cursor.fetchone()
            if inviter:
                referred_by = inviter[0]

        if DATABASE_URL:
            cursor.execute(
                """
                INSERT INTO users
                (username, email, password, referral_code, referred_by)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    username, email,
                    generate_password_hash(password),
                    referral_code, referred_by
                )
            )
            new_user_id = cursor.fetchone()[0]
        else:
            cursor.execute(
                """
                INSERT INTO users
                (username, email, password, referral_code, referred_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    username, email,
                    generate_password_hash(password),
                    referral_code, referred_by
                )
            )
            new_user_id = cursor.lastrowid

        if referred_by and referred_by != new_user_id:
            cursor.execute(
                f"UPDATE users SET points = points + {p} WHERE id = {p}",
                (REFERRAL_POINTS, referred_by)
            )
            record_transaction(
                cursor, referred_by, "referral",
                points_change=REFERRAL_POINTS,
                description=f"Referral joined: {username}"
            )

        conn.commit()
        session.pop("referral_code", None)
        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("login"))

    except Exception:
        conn.rollback()
        app.logger.exception("Registration failed.")
        flash(
            "Registration failed. Username or email may already exist.",
            "error"
        )
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
                f"""
                SELECT id, username, email, password
                FROM users WHERE LOWER(email) = {p}
                """,
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


@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if not ADMIN_EMAIL:
        flash(
            "Admin login is not configured. Set ADMIN_EMAIL in Render environment variables.",
            "error"
        )
        return render_template("admin_login.html")

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        conn = get_db()
        cursor = conn.cursor()
        p = placeholder()

        try:
            cursor.execute(
                f"""
                SELECT id, username, password
                FROM users WHERE LOWER(email) = {p}
                """,
                (ADMIN_EMAIL,)
            )
            user = cursor.fetchone()
            success = bool(
                email == ADMIN_EMAIL and user
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
            f"""
            SELECT points, ads_watched_count, last_ad_time, usd_balance
            FROM users WHERE id = {p}
            """,
            (session["user_id"],)
        )
        row = cursor.fetchone()

        if not row:
            session.clear()
            return redirect(url_for("login"))

        points = row[0] or 0
        count = row[1] or 0
        last_time = parse_datetime(row[2])
        usd_balance = float(row[3] or 0)

        if last_time and datetime.now() - last_time >= AD_WINDOW:
            cursor.execute(
                f"UPDATE users SET ads_watched_count = 0 WHERE id = {p}",
                (session["user_id"],)
            )
            conn.commit()
            count = 0

        usd_value = float(
            (Decimal(points) * Decimal("0.005")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )

        return render_template(
            "dashboard.html",
            username=session.get("username", "User"),
            points=points,
            usd_value=usd_value,
            usd_balance=usd_balance,
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
    p = placeholder()

    try:
        if DATABASE_URL:
            cursor.execute(
                """
                SELECT points, ads_watched_count, last_ad_time
                FROM users WHERE id = %s FOR UPDATE
                """,
                (session["user_id"],)
            )
        else:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                """
                SELECT points, ads_watched_count, last_ad_time
                FROM users WHERE id = ?
                """,
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
            f"""
            UPDATE users
            SET points = points + {p},
                ads_watched_count = {p},
                last_ad_time = {p}
            WHERE id = {p}
            """,
            (
                POINTS_PER_AD, count + 1,
                timestamp, session["user_id"]
            )
        )

        record_transaction(
            cursor, session["user_id"], "ad_reward",
            points_change=POINTS_PER_AD,
            description="Watch-ad reward"
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
            
@app.route("/tasks")
def tasks():
    if "user_id" not in session:
        return redirect(url_for("login"))

    return render_template(
        "tasks.html",
        username=session.get("username", "User")
    )


@app.route("/invite")
def invite():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()

    try:
        cursor.execute(
            f"""
            SELECT referral_code, points
            FROM users WHERE id = {p}
            """,
            (session["user_id"],)
        )
        user = cursor.fetchone()

        if not user:
            session.clear()
            return redirect(url_for("login"))

        referral_link = url_for(
            "register", ref=user[0], _external=True
        )

        cursor.execute(
            f"""
            SELECT username, created_at
            FROM users WHERE referred_by = {p}
            ORDER BY id DESC
            """,
            (session["user_id"],)
        )
        referrals = cursor.fetchall()

        return render_template(
            "invite.html",
            referral_code=user[0],
            referral_link=referral_link,
            referrals=referrals,
            referral_count=len(referrals),
            referral_points=REFERRAL_POINTS
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
                points_to_redeem = int(raw_points)
            except (ValueError, TypeError):
                flash("Enter a valid whole number of points.", "error")
                return redirect(url_for("redeem"))

            if points_to_redeem < MIN_REDEEM_POINTS:
                flash("Minimum redemption is 100 points ($0.50).", "error")
                return redirect(url_for("redeem"))

            if points_to_redeem % 2 != 0:
                flash(
                    "Enter an even number of points to preserve the exact rate.",
                    "error"
                )
                return redirect(url_for("redeem"))

            if DATABASE_URL:
                cursor.execute(
                    """
                    SELECT points, usd_balance
                    FROM users WHERE id = %s FOR UPDATE
                    """,
                    (session["user_id"],)
                )
            else:
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute(
                    """
                    SELECT points, usd_balance
                    FROM users WHERE id = ?
                    """,
                    (session["user_id"],)
                )

            user = cursor.fetchone()

            if not user:
                conn.rollback()
                session.clear()
                return redirect(url_for("login"))

            current_points = int(user[0] or 0)

            if current_points < points_to_redeem:
                conn.rollback()
                flash("You do not have enough points.", "error")
                return redirect(url_for("redeem"))

            usd_amount = (
                Decimal(points_to_redeem) * Decimal("0.005")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            cursor.execute(
                f"""
                UPDATE users
                SET points = points - {p},
                    usd_balance = usd_balance + {p}
                WHERE id = {p}
                """,
                (
                    points_to_redeem, float(usd_amount),
                    session["user_id"]
                )
            )

            record_transaction(
                cursor, session["user_id"], "redeem",
                points_change=-points_to_redeem,
                usd_change=usd_amount,
                description=f"Redeemed {points_to_redeem} points"
            )

            conn.commit()
            flash(
                f"Redeemed {points_to_redeem} points into ${usd_amount:.2f} "
                "in your Poly Earn USD wallet.",
                "success"
            )
            return redirect(url_for("redeem"))

        cursor.execute(
            f"""
            SELECT points, usd_balance
            FROM users WHERE id = {p}
            """,
            (session["user_id"],)
        )
        user = cursor.fetchone()

        if not user:
            session.clear()
            return redirect(url_for("login"))

        return render_template(
            "redeem.html",
            points=user[0] or 0,
            usd_balance=float(user[1] or 0),
            min_points=MIN_REDEEM_POINTS
        )

    except Exception:
        conn.rollback()
        app.logger.exception("Redemption failed.")
        flash("Could not process redemption. Please try again.", "error")
        return redirect(url_for("dashboard"))

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
            f"""
            SELECT username, email, created_at, referral_code,
                   points, usd_balance
            FROM users WHERE id = {p}
            """,
            (session["user_id"],)
        )
        user = cursor.fetchone()

        if not user:
            session.clear()
            return redirect(url_for("login"))

        return render_template(
            "profile.html",
            user=user
        )

    except Exception:
        app.logger.exception("Profile page failed.")
        flash("Could not load your profile.", "error")
        return redirect(url_for("dashboard"))

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
            f"""
            SELECT type, points_change, usd_change,
                   description, created_at
            FROM transactions
            WHERE user_id = {p}
            ORDER BY id DESC
            LIMIT 200
            """,
            (session["user_id"],)
        )

        transactions = [
            {
                "type": row[0],
                "points_change": row[1],
                "usd_change": float(row[2] or 0),
                "description": row[3] or "",
                "created_at": row[4]
            }
            for row in cursor.fetchall()
        ]

        cursor.execute(
            f"""
            SELECT points, status, created_at, processed_at
            FROM withdrawals
            WHERE user_id = {p}
            ORDER BY id DESC
            LIMIT 100
            """,
            (session["user_id"],)
        )

        for row in cursor.fetchall():
            transactions.append({
                "type": "withdrawal",
                "points_change": -int(row[0]),
                "usd_change": 0,
                "description": f"Withdrawal request — {row[1]}",
                "created_at": row[2]
            })

        transactions.sort(
            key=lambda item: str(item["created_at"] or ""),
            reverse=True
        )

        return render_template(
            "history.html",
            transactions=transactions
        )

    except Exception:
        app.logger.exception("Transaction history failed.")
        flash("Could not load your transaction history.", "error")
        return redirect(url_for("dashboard"))

    finally:
        cursor.close()
        conn.close()
        
@app.route("/withdraw", methods=["POST"])
def withdraw():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()

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

        points = int(row[0] or 0)

        if points < MIN_WITHDRAWAL_POINTS:
            conn.rollback()
            flash("Minimum withdrawal is 6,000 points ($30).", "error")
            return redirect(url_for("dashboard"))

        cursor.execute(
            f"""
            SELECT id FROM withdrawals
            WHERE user_id = {p} AND status = 'pending'
            """,
            (session["user_id"],)
        )

        if cursor.fetchone():
            conn.rollback()
            flash(
                "You already have a pending withdrawal request.",
                "error"
            )
            return redirect(url_for("dashboard"))

        cursor.execute(
            f"UPDATE users SET points = points - {p} WHERE id = {p}",
            (points, session["user_id"])
        )

        cursor.execute(
            f"""
            INSERT INTO withdrawals (user_id, points, status)
            VALUES ({p}, {p}, 'pending')
            """,
            (session["user_id"], points)
        )

        record_transaction(
            cursor, session["user_id"], "withdrawal",
            points_change=-points,
            description="Withdrawal request submitted"
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
    if not is_admin_user():
        session.clear()
        flash("Please log in with your admin account.", "error")
        return redirect(url_for("admin_login"))

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'"
        )
        pending_withdrawals = cursor.fetchone()[0]

        cursor.execute("SELECT COALESCE(SUM(points), 0) FROM users")
        total_points = cursor.fetchone()[0]

        cursor.execute("""
            SELECT id, username, email, points
            FROM users ORDER BY id DESC
        """)
        users = cursor.fetchall()

        cursor.execute("""
            SELECT withdrawals.id, users.username, users.email,
                   withdrawals.points, withdrawals.status,
                   withdrawals.created_at
            FROM withdrawals
            JOIN users ON users.id = withdrawals.user_id
            ORDER BY withdrawals.id DESC
        """)
        withdrawals = cursor.fetchall()

        return render_template(
            "admin.html",
            total_users=total_users,
            pending_withdrawals=pending_withdrawals,
            total_points=total_points,
            users=users,
            withdrawals=withdrawals
        )

    except Exception:
        app.logger.exception("Admin dashboard failed.")
        flash("Could not load the admin dashboard.", "error")
        return redirect(url_for("admin_login"))

    finally:
        cursor.close()
        conn.close()


@app.route(
    "/admin/withdrawal/<int:withdrawal_id>/<action>",
    methods=["POST"]
)
def process_withdrawal(withdrawal_id, action):
    if not is_admin_user():
        session.clear()
        flash("Please log in with your admin account.", "error")
        return redirect(url_for("admin_login"))

    if action not in ("approve", "reject"):
        flash("Invalid withdrawal action.", "error")
        return redirect(url_for("admin_dashboard"))

    conn = get_db()
    cursor = conn.cursor()
    p = placeholder()

    try:
        if DATABASE_URL:
            cursor.execute(
                """
                SELECT user_id, points, status
                FROM withdrawals WHERE id = %s FOR UPDATE
                """,
                (withdrawal_id,)
            )
        else:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                """
                SELECT user_id, points, status
                FROM withdrawals WHERE id = ?
                """,
                (withdrawal_id,)
            )

        withdrawal = cursor.fetchone()

        if not withdrawal:
            conn.rollback()
            flash("Withdrawal request not found.", "error")
            return redirect(url_for("admin_dashboard"))

        user_id, points, status = withdrawal

        if status != "pending":
            conn.rollback()
            flash("This withdrawal has already been processed.", "error")
            return redirect(url_for("admin_dashboard"))

        new_status = "approved" if action == "approve" else "rejected"
        processed_at = (
            datetime.now()
            if DATABASE_URL
            else datetime.now().isoformat(sep=" ")
        )

        cursor.execute(
            f"""
            UPDATE withdrawals
            SET status = {p}, processed_at = {p}
            WHERE id = {p}
            """,
            (new_status, processed_at, withdrawal_id)
        )

        if action == "reject":
            cursor.execute(
                f"UPDATE users SET points = points + {p} WHERE id = {p}",
                (points, user_id)
            )
            record_transaction(
                cursor, user_id, "withdrawal_refund",
                points_change=points,
                description=f"Refund for rejected withdrawal #{withdrawal_id}"
            )

        conn.commit()

        if action == "approve":
            flash("Withdrawal approved successfully.", "success")
        else:
            flash(
                "Withdrawal rejected. Points returned to the user.",
                "success"
            )

    except Exception:
        conn.rollback()
        app.logger.exception("Withdrawal processing failed.")
        flash("Could not process this withdrawal.", "error")

    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("admin_dashboard"))


@app.route("/admin-login-history")
def admin_login_history():
    if not is_admin_user():
        return redirect(url_for("admin_login"))

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT email, success, ip_address, user_agent, created_at
            FROM admin_login_history
            ORDER BY id DESC LIMIT 100
        """)
        attempts = cursor.fetchall()

        return render_template(
            "admin_login_history.html",
            attempts=attempts
        )

    except Exception:
        app.logger.exception("Admin login history failed.")
        flash("Could not load admin login history.", "error")
        return redirect(url_for("admin_dashboard"))

    finally:
        cursor.close()
        conn.close()


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# Initialize database tables on application startup.
init_db()
    
