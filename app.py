
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

app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    app.secret_key = os.urandom(32)
    app.logger.warning(
        "SECRET_KEY is not configured. Set it in Render for persistent sessions."
    )

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://", "postgresql://", 1
    )

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
    conn = None
    cursor = None

    try:
        conn = get_db()
        cursor = conn.cursor()

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

        conn.commit()
        app.logger.info("Database initialized successfully.")

    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Database initialization failed.")
        raise

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


def get_user_ad_status(user_id):
    conn = None
    cursor = None

    try:
        conn = get_db()
        cursor = conn.cursor()

        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(
            "SELECT ads_watched_count, last_ad_time "
            f"FROM users WHERE id = {placeholder}",
            (user_id,)
        )

        row = cursor.fetchone()

        if not row:
            return None

        count = row[0] or 0
        last_time = parse_datetime(row[1])
        now = datetime.now()

        if last_time and now - last_time >= AD_WINDOW:
            cursor.execute(
                "UPDATE users SET ads_watched_count = 0 "
                f"WHERE id = {placeholder}",
                (user_id,)
            )
            conn.commit()
            count = 0
            last_time = None

        return count, last_time

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

        conn = None
        cursor = None

        try:
            conn = get_db()
            cursor = conn.cursor()

            if DATABASE_URL:
                cursor.execute(
                    "INSERT INTO users (username, email, password) "
                    "VALUES (%s, %s, %s)",
                    (username, email, generate_password_hash(password))
                )
            else:
                cursor.execute(
                    "INSERT INTO users (username, email, password) "
                    "VALUES (?, ?, ?)",
                    (username, email, generate_password_hash(password))
                )

            conn.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))

        except Exception:
            if conn:
                conn.rollback()
            app.logger.exception("Registration failed.")
            flash(
                "Registration failed. Check whether the username or email "
                "is already registered.",
                "error"
            )
            return redirect(url_for("register"))

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        conn = None
        cursor = None

        try:
            conn = get_db()
            cursor = conn.cursor()
            placeholder = "%s" if DATABASE_URL else "?"

            cursor.execute(
                "SELECT id, username, email, password "
                f"FROM users WHERE email = {placeholder}",
                (email,)
            )
            user = cursor.fetchone()

            if user and check_password_hash(user[3], password):
                session.clear()
                session["user_id"] = user[0]
                session["username"] = user[1]
                return redirect(url_for("dashboard"))

            flash("Invalid email or password.", "error")

        except Exception:
            app.logger.exception("Login failed.")
            flash("Login error. Please try again.", "error")

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = None
    cursor = None

    try:
        conn = get_db()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"

        cursor.execute(
            "SELECT points, ads_watched_count, last_ad_time "
            f"FROM users WHERE id = {placeholder}",
            (session["user_id"],)
        )
        row = cursor.fetchone()

        if not row:
            session.clear()
            flash("Account not found. Please log in again.", "error")
            return redirect(url_for("login"))

        points = row[0] or 0
        ads_watched = row[1] or 0
        last_time = parse_datetime(row[2])

        if last_time and datetime.now() - last_time >= AD_WINDOW:
            cursor.execute(
                "UPDATE users SET ads_watched_count = 0 "
                f"WHERE id = {placeholder}",
                (session["user_id"],)
            )
            conn.commit()
            ads_watched = 0

        usd_value = round(points * 0.005, 2)

    except Exception:
        app.logger.exception("Dashboard loading failed.")
        flash("Could not load your balance. Please try again.", "error")
        return redirect(url_for("login"))

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return render_template(
        "dashboard.html",
        username=session.get("username", "User"),
        points=points,
        usd_value=usd_value,
        ads_watched=ads_watched
    )


@app.route("/watch-ad", methods=["GET"])
def watch_ad():
    if "user_id" not in session:
        return redirect(url_for("login"))

    try:
        status = get_user_ad_status(session["user_id"])

        if status is None:
            session.clear()
            flash("Account not found. Please log in again.", "error")
            return redirect(url_for("login"))

        ads_watched, _ = status

        if ads_watched >= MAX_ADS:
            flash(
                "You have reached 25 ads in this 2-hour window.",
                "error"
            )
            return redirect(url_for("dashboard"))

        # This timestamp alone does not prove an ad was completed.
        session["ad_page_opened_at"] = time.time()

        return render_template("watch_ad.html")

    except Exception:
        app.logger.exception("Watch-ad page failed.")
        flash("Could not open the ad page. Please try again.", "error")
        return redirect(url_for("dashboard"))


@app.route("/claim-reward", methods=["POST"])
def claim_reward():
    if "user_id" not in session:
        return redirect(url_for("login"))

    # Basic delay against instant claims; this is NOT ad-provider verification.
    opened_at = session.get("ad_page_opened_at")

    if opened_at is None or time.time() - opened_at < 10:
        flash("Please complete the ad before claiming a reward.", "error")
        return redirect(url_for("watch_ad"))

    conn = None
    cursor = None

    try:
        conn = get_db()
        cursor = conn.cursor()
        user_id = session["user_id"]
        now = datetime.now()
        placeholder = "%s" if DATABASE_URL else "?"

        # Lock the PostgreSQL user row to avoid simultaneous claims.
        if DATABASE_URL:
            cursor.execute(
                "SELECT points, ads_watched_count, last_ad_time "
                "FROM users WHERE id = %s FOR UPDATE",
                (user_id,)
            )
        else:
            cursor.execute(
                "SELECT points, ads_watched_count, last_ad_time "
                "FROM users WHERE id = ?",
                (user_id,)
            )

        row = cursor.fetchone()

        if not row:
            conn.rollback()
            session.clear()
            flash("Account not found. Please log in again.", "error")
            return redirect(url_for("login"))

        points = row[0] or 0
        count = row[1] or 0
        last_time = parse_datetime(row[2])

        if last_time and now - last_time >= AD_WINDOW:
            count = 0

        if count >= MAX_ADS:
            conn.rollback()
            session.pop("ad_page_opened_at", None)
            flash("You have reached the 25-ad limit. Please wait.", "error")
            return redirect(url_for("dashboard"))

        if DATABASE_URL:
            cursor.execute(
                "UPDATE users SET points = %s, "
                "ads_watched_count = %s, last_ad_time = %s "
                "WHERE id = %s",
                (points + POINTS_PER_AD, count + 1, now, user_id)
            )
        else:
            cursor.execute(
                "UPDATE users SET points = ?, "
                "ads_watched_count = ?, last_ad_time = ? "
                "WHERE id = ?",
                (
                    points + POINTS_PER_AD,
                    count + 1,
                    now.isoformat(sep=" "),
                    user_id
                )
            )

        if cursor.rowcount != 1:
            raise RuntimeError("Reward update did not update one user.")

        conn.commit()
        session.pop("ad_page_opened_at", None)
        flash("3 Poly Points added to your balance!", "success")

    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Claim reward failed.")
        flash("Reward claim failed. Please try again later.", "error")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for("dashboard"))


@app.route("/withdraw", methods=["POST"])
def withdraw():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = None
    cursor = None

    try:
        conn = get_db()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"

        cursor.execute(
            f"SELECT points FROM users WHERE id = {placeholder}",
            (session["user_id"],)
        )
        row = cursor.fetchone()

        if not row:
            session.clear()
            flash("Account not found. Please log in again.", "error")
            return redirect(url_for("login"))

        points = row[0] or 0

        if points < MIN_WITHDRAWAL_POINTS:
            flash(
                f"Minimum withdrawal is 6,000 points ($30). "
                f"Your balance is {points} points.",
                "error"
            )
        else:
            # A real payout system has not been connected yet.
            flash(
                "You meet the minimum balance. Payout processing is not "
                "configured yet, so no withdrawal has been submitted.",
                "error"
            )

    except Exception:
        app.logger.exception("Withdrawal check failed.")
        flash("Could not check your withdrawal balance.", "error")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    init_db()
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
        )
            
