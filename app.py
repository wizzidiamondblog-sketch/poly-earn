import os
import sqlite3
import psycopg2
from psycopg2.extras import DictCursor
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'poly_earn_super_secret_2026')

# Render provides postgres:// but psycopg2 requires postgresql://
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# --- Helper DB Functions ---
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
                    is_admin INTEGER DEFAULT 0
                );
            ''')
        
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Database Init Error: {e}")

# Call DB initialization
init_db()

# --- Routes ---

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
            if DATABASE_URL:
                cursor = conn.cursor(cursor_factory=DictCursor)
                cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
                user = cursor.fetchone()
            else:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
                user = cursor.fetchone()

            cursor.close()
            conn.close()
            
            if user and check_password_hash(user['password'], password):
                session['user_id'] = user['id']
                session['username'] = user['username']
                return redirect(url_for('dashboard'))
            else:
                flash("Invalid email or password!", "error")
        except Exception as e:
            flash("Login error occurred. Try again.", "error")
            
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    points = 0
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute("SELECT points FROM users WHERE id = %s", (session['user_id'],))
            row = cursor.fetchone()
            points = row[0] if row else 0
        else:
            cursor.execute("SELECT points FROM users WHERE id = ?", (session['user_id'],))
            row = cursor.fetchone()
            points = row['points'] if row else 0
            
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Dashboard Error: {e}")
    
    return render_template('dashboard.html', username=session.get('username'), points=points)

@app.route('/watch-ad')
def watch_ad():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('watch_ad.html')

@app.route('/claim-reward', methods=['POST'])
def claim_reward():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute("UPDATE users SET points = points + 10 WHERE id = %s", (session['user_id'],))
        else:
            cursor.execute("UPDATE users SET points = points + 10 WHERE id = ?", (session['user_id'],))
            
        conn.commit()
        cursor.close()
        conn.close()
        flash("Reward claimed! +10 Points added.", "success")
    except Exception as e:
        flash("Failed to claim reward. Try again.", "error")
        
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
