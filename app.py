import os
import sqlite3
import psycopg2
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'poly_earn_super_secret_2026')

DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

def get_db():
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return conn
    else:
        conn = sqlite3.connect('poly_earn.db')
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(80) UNIQUE NOT NULL,
                    email VARCHAR(120) UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    points INT DEFAULT 0,
                    ads_watched_count INT DEFAULT 0,
                    last_ad_time TIMESTAMP,
                    is_admin BOOLEAN DEFAULT FALSE
                );
            ''')
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    points INTEGER DEFAULT 0,
                    ads_watched_count INTEGER DEFAULT 0,
                    last_ad_time TEXT,
                    is_admin INTEGER DEFAULT 0
                );
            ''')
        
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Database Init Error: {e}")

init_db()

@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not username or not email or not password:
            flash("All fields are required!", "error")
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(password)
        
        try:
            conn = get_db()
            cursor = conn.cursor()
            if DATABASE_URL:
                cursor.execute(
                    "INSERT INTO users (username, email, password) VALUES (%s, %s, %s)",
                    (username, email, hashed_password)
                )
            else:
                cursor.execute(
                    "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                    (username, email, hashed_password)
                )
            conn.commit()
            cursor.close()
            conn.close()
            flash("Registration successful! Please login.", "success")
            return redirect(url_for('login'))
        except Exception as e:
            flash("Username or Email already exists!", "error")
            return redirect(url_for('register'))
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        try:
            conn = get_db()
            cursor = conn.cursor()
            
            if DATABASE_URL:
                cursor.execute("SELECT id, username, email, password, points FROM users WHERE email = %s", (email,))
            else:
                cursor.execute("SELECT id, username, email, password, points FROM users WHERE email = ?", (email,))
                
            user_row = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if user_row:
                user_id, username, db_email, db_password, points = user_row
                if check_password_hash(db_password, password):
                    session['user_id'] = user_id
                    session['username'] = username
                    return redirect(url_for('dashboard'))
            
            flash("Invalid email or password!", "error")
        except Exception as e:
            flash("Login error occurred.", "error")
            
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    points = 0
    ads_watched = 0
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute("SELECT points, ads_watched_count, last_ad_time FROM users WHERE id = %s", (session['user_id'],))
        else:
            cursor.execute("SELECT points, ads_watched_count, last_ad_time FROM users WHERE id = ?", (session['user_id'],))
            
        row = cursor.fetchone()
        if row:
            points = row[0]
            ads_watched = row[1] if row[1] else 0
            last_time = row[2]
            
            # Check if 2 hours passed since last ad reset window
            if last_time:
                if isinstance(last_time, str):
                    last_time = datetime.strptime(last_time, '%Y-%m-%d %H:%M:%S.%f' if '.' in last_time else '%Y-%m-%d %H:%M:%S')
                if datetime.now() - last_time > timedelta(hours=2):
                    if DATABASE_URL:
                        cursor.execute("UPDATE users SET ads_watched_count = 0 WHERE id = %s", (session['user_id'],))
                    else:
                        cursor.execute("UPDATE users SET ads_watched_count = 0 WHERE id = ?", (session['user_id'],))
                    conn.commit()
                    ads_watched = 0
            
        cursor.close()
        conn.close()
    except Exception as e:
        points = 0
    
    # Calculate USD value based on 4,000 points = $20 (1 point = $0.005)
    usd_value = round(points * (20 / 4000), 2)
    
    return render_template('dashboard.html', username=session.get('username', 'User'), points=points, usd_value=usd_value, ads_watched=ads_watched)

@app.route('/watch-ad')
def watch_ad():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    # Check 25 ads per 2 hours limit
    try:
        conn = get_db()
        cursor = conn.cursor()
        if DATABASE_URL:
            cursor.execute("SELECT ads_watched_count, last_ad_time FROM users WHERE id = %s", (session['user_id'],))
        else:
            cursor.execute("SELECT ads_watched_count, last_ad_time FROM users WHERE id = ?", (session['user_id'],))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if row and row[1]:
            last_time = row[1]
            if isinstance(last_time, str):
                last_time = datetime.strptime(last_time, '%Y-%m-%d %H:%M:%S.%f' if '.' in last_time else '%Y-%m-%d %H:%M:%S')
            
            # If 2 hours have passed, reset the ad count
            if datetime.now() - last_time > timedelta(hours=2):
                conn = get_db()
                cursor = conn.cursor()
                if DATABASE_URL:
                    cursor.execute("UPDATE users SET ads_watched_count = 0 WHERE id = %s", (session['user_id'],))
                else:
                    cursor.execute("UPDATE users SET ads_watched_count = 0 WHERE id = ?", (session['user_id'],))
                conn.commit()
                cursor.close()
                conn.close()
            else:
                ads_watched = row[0]
                if ads_watched >= 25:
                    flash("You have reached your limit of 25 ads for this 2-hour window. Please wait for reset!", "error")
                    return redirect(url_for('dashboard'))
    except Exception as e:
        pass
        
    # Store exact start time for timer security validation
    session['ad_start_time'] = datetime.now().timestamp()
    return render_template('watch_ad.html')

@app.route('/claim-reward', methods=['POST'])
def claim_reward():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    start_time = session.get('ad_start_time')
    
    # Verify at least 18 seconds passed on server clock before crediting
    if not start_time or (datetime.now().timestamp() - start_time) < 18:
        flash("Failed to claim reward. Please wait the full 20 seconds.", "error")
        return redirect(url_for('dashboard'))
        
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        now = datetime.now()
        if DATABASE_URL:
            cursor.execute(
                "UPDATE users SET points = points + 3, ads_watched_count = ads_watched_count + 1, last_ad_time = %s WHERE id = %s",
                (now, session['user_id'])
            )
        else:
            cursor.execute(
                "UPDATE users SET points = points + 3, ads_watched_count = ads_watched_count + 1, last_ad_time = ? WHERE id = ?",
                (str(now), session['user_id'])
            )
            
        conn.commit()
        cursor.close()
        conn.close()
        
        # Clear start timestamp so it can't be re-submitted
        session.pop('ad_start_time', None)
        
        flash("🎉 +3 Poly Points added to your balance!", "success")
    except Exception as e:
        flash("Failed to claim reward. Try again.", "error")
        
    return redirect(url_for('dashboard'))

@app.route('/withdraw', methods=['POST'])
def withdraw():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    try:
        conn = get_db()
        cursor = conn.cursor()
        if DATABASE_URL:
            cursor.execute("SELECT points FROM users WHERE id = %s", (session['user_id'],))
        else:
            cursor.execute("SELECT points FROM users WHERE id = ?", (session['user_id'],))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        points = row[0] if row else 0
        min_points_for_30_usd = int(30 * (4000 / 20)) # 6,000 points = $30
        
        if points < min_points_for_30_usd:
            flash(f"Minimum withdrawal is $30 ({min_points_for_30_usd} Poly Points). You currently have {points} Points.", "error")
        else:
            flash("Withdrawal request submitted successfully! Processing payout...", "success")
    except Exception as e:
        flash("Withdrawal error occurred.", "error")
        
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
            
