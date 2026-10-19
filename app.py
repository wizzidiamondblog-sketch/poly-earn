import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'poly_earn_super_secret_key_2026')

DB_NAME = 'poly_earn.db'

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_verified INTEGER DEFAULT 1,
            points INTEGER DEFAULT 0,
            ads_watched INTEGER DEFAULT 0,
            balance REAL DEFAULT 0.0,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not first_name or not last_name or not email or not password:
            flash('All fields are required.', 'danger')
            return render_template('register.html')

        conn = get_db()
        cursor = conn.cursor()
        existing_user = cursor.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
        
        if existing_user:
            conn.close()
            flash('Email already registered. Please login.', 'info')
            return redirect(url_for('login'))

        hashed_password = generate_password_hash(password)
        cursor.execute('''
            INSERT INTO users (first_name, last_name, email, password, is_verified, points)
            VALUES (?, ?, ?, ?, 1, 0)
        ''', (first_name, last_name, email, hashed_password))
        
        conn.commit()
        user_id = cursor.lastrowid
        conn.close()

        session['user_id'] = user_id
        session['user_name'] = f"{first_name} {last_name}"
        flash('Account created successfully! Welcome to Poly Earn.', 'success')
        return redirect(url_for('dashboard'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        conn = get_db()
        cursor = conn.cursor()
        user = cursor.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['user_name'] = f"{user['first_name']} {user['last_name']}"
            session['is_admin'] = user['is_admin']
            flash('Logged in successfully!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password.', 'danger')

    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        flash('Please login to access dashboard.', 'warning')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    user = cursor.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()

    return render_template('dashboard.html', user=user)

@app.route('/watch_ad', methods=['POST'])
def watch_ad():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    conn = get_db()
    cursor = conn.cursor()
    user = cursor.execute('SELECT points, ads_watched FROM users WHERE id = ?', (session['user_id'],)).fetchone()

    if user:
        new_ads = (user['ads_watched'] or 0) + 1
        new_points = (user['points'] or 0) + 2
        new_balance = round(new_points * 0.01, 2)  # 100 points = $1.00 USDT

        cursor.execute('''
            UPDATE users SET points = ?, ads_watched = ?, balance = ? WHERE id = ?
        ''', (new_points, new_ads, new_balance, session['user_id']))
        conn.commit()
        conn.close()

        return jsonify({
            'success': True, 
            'points': new_points, 
            'ads_watched': new_ads,
            'balance': new_balance
        })

    conn.close()
    return jsonify({'success': False, 'message': 'User not found'}), 404

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
@app.route('/watch-ad')
def watch_ad():
    return render_template('watch_ad.html')
    
