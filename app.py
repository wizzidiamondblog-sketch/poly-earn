from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3
from datetime import datetime, timedelta
import os

app = Flask(__name__)
app.secret_key = 'poly_earn_secret_key_change_in_production'

# --- CONFIGURATION ---
POINT_RATE_USDT = 0.005       # 4,000 Points = $20.00 USDT ($0.005 per point)
MIN_PAYOUT_POINTS = 4000
MIN_PAYOUT_USDT = 20.00
MONETAG_DIRECT_LINK = "https://uplcm.com/4/12063392"
ADMIN_PASSWORD = "adminsecretkey123"  # Change dis to your preferred admin password later

def get_db_connection():
    conn = sqlite3.connect('poly_earn.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            points INTEGER DEFAULT 0,
            ref_code TEXT UNIQUE NOT NULL,
            referred_by TEXT,
            ads_watched INTEGER DEFAULT 0,
            last_ad_window TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS payouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            wallet_address TEXT NOT NULL,
            points_requested INTEGER NOT NULL,
            usdt_amount REAL NOT NULL,
            status TEXT DEFAULT 'Pending',
            created_at TEXT NOT NULL
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
    ref_code_arg = request.args.get('ref', '')
    if request.method == 'POST':
        username = request.form['username'].strip().lower()
        password = request.form['password']
        referred_by = request.form.get('referred_by', '').strip()

        if not username or not password:
            flash("Please fill in all fields.")
            return redirect(url_for('register'))

        import uuid
        new_ref_code = str(uuid.uuid4())[:8]

        conn = get_db_connection()
        try:
            if referred_by:
                referrer = conn.execute('SELECT * FROM users WHERE ref_code = ?', (referred_by,)).fetchone()
                if referrer:
                    conn.execute('UPDATE users SET points = points + 10 WHERE id = ?', (referrer['id'],))

            conn.execute(
                'INSERT INTO users (username, password, points, ref_code, referred_by, ads_watched) VALUES (?, ?, ?, ?, ?, ?)',
                (username, password, 0, new_ref_code, referred_by, 0)
            )
            conn.commit()
            conn.close()
            flash("Registration successful! Please login.")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            conn.close()
            flash("Username already taken. Pick another one.")
            return redirect(url_for('register'))

    return render_template('register.html', ref_code=ref_code_arg)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip().lower()
        password = request.form['password']

        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, password)).fetchone()
        conn.close()

        if user:
            session['user_id'] = user['id']
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid credentials.")
            return redirect(url_for('login'))

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()

    now = datetime.now()
    ads_watched = user['ads_watched']
    last_window_str = user['last_ad_window']
    cooldown_seconds = 0

    if last_window_str:
        last_window = datetime.fromisoformat(last_window_str)
        time_diff = now - last_window
        if time_diff >= timedelta(hours=2):
            conn.execute('UPDATE users SET ads_watched = 0, last_ad_window = NULL WHERE id = ?', (user['id'],))
            conn.commit()
            ads_watched = 0
        else:
            if ads_watched >= 25:
                cooldown_seconds = int((timedelta(hours=2) - time_diff).total_seconds())

    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    
    # User's recent payout requests
    payouts = conn.execute('SELECT * FROM payouts WHERE user_id = ? ORDER BY id DESC', (session['user_id'],)).fetchall()

    # Calculate Total Points Redeemed / Used
    used_points_row = conn.execute('SELECT SUM(points_requested) as total_used FROM payouts WHERE user_id = ? AND status != "Declined"', (session['user_id'],)).fetchone()
    used_points = used_points_row['total_used'] if used_points_row['total_used'] else 0

    conn.close()

    usdt_balance = round(user['points'] * POINT_RATE_USDT, 2)

    return render_template('dashboard.html', 
                           user=user, 
                           usdt_balance=usdt_balance, 
                           used_points=used_points,
                           cooldown_seconds=cooldown_seconds,
                           monetag_link=MONETAG_DIRECT_LINK,
                           min_payout_points=MIN_PAYOUT_POINTS,
                           min_payout_usdt=MIN_PAYOUT_USDT,
                           payouts=payouts)

@app.route('/reward-ad', methods=['POST'])
def reward_ad():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()

    now = datetime.now()
    ads_watched = user['ads_watched']
    last_window_str = user['last_ad_window']

    if last_window_str:
        last_window = datetime.fromisoformat(last_window_str)
        if now - last_window >= timedelta(hours=2):
            ads_watched = 0
            last_window_str = None

    if ads_watched >= 25:
        conn.close()
        return jsonify({'success': False, 'message': '2-hour limit reached.'})

    new_count = ads_watched + 1
    new_window_str = last_window_str if last_window_str else now.isoformat()

    conn.execute('UPDATE users SET points = points + 2, ads_watched = ?, last_ad_window = ? WHERE id = ?',
                 (new_count, new_window_str, user['id']))
    conn.commit()

    updated_user = conn.execute('SELECT points FROM users WHERE id = ?', (user['id'],)).fetchone()
    conn.close()

    new_usdt = round(updated_user['points'] * POINT_RATE_USDT, 2)

    return jsonify({
        'success': True, 
        'new_points': updated_user['points'],
        'new_usdt': new_usdt,
        'ads_watched': new_count
    })

@app.route('/request-payout', methods=['POST'])
def request_payout():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    wallet_address = request.json.get('wallet_address', '').strip()
    if not wallet_address:
        return jsonify({'success': False, 'message': 'Please enter a valid Polygon USDT wallet address.'})

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()

    if user['points'] < MIN_PAYOUT_POINTS:
        conn.close()
        return jsonify({'success': False, 'message': f'Minimum payout requires {MIN_PAYOUT_POINTS} points.'})

    usdt_amount = round(user['points'] * POINT_RATE_USDT, 2)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn.execute('UPDATE users SET points = 0 WHERE id = ?', (user['id'],))
    conn.execute('INSERT INTO payouts (user_id, username, wallet_address, points_requested, usdt_amount, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                 (user['id'], user['username'], wallet_address, user['points'], usdt_amount, 'Pending', now_str))
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'message': 'Payout request submitted successfully!'})

# --- ADMIN ROUTES ---
@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            flash("Invalid admin password.")
            return redirect(url_for('admin_login'))

    return '''
        <div style="max-width:300px;margin:50px auto;font-family:sans-serif;text-align:center;">
            <h2>Poly Earn Admin</h2>
            <form method="POST">
                <input type="password" name="password" placeholder="Admin Password" style="width:100%;padding:10px;margin-bottom:10px;" required>
                <button type="submit" style="width:100%;padding:10px;background:#10b981;color:white;border:none;">Login</button>
            </form>
        </div>
    '''

@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    users = conn.execute('SELECT * FROM users ORDER BY points DESC').fetchall()
    payouts = conn.execute('SELECT * FROM payouts ORDER BY id DESC').fetchall()
    conn.close()

    return render_template('admin.html', users=users, payouts=payouts, point_rate=POINT_RATE_USDT)

@app.route('/admin/action/<int:payout_id>/<string:action>')
def admin_payout_action(payout_id, action):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    payout = conn.execute('SELECT * FROM payouts WHERE id = ?', (payout_id,)).fetchone()

    if payout and payout['status'] == 'Pending':
        if action == 'approve':
            conn.execute('UPDATE payouts SET status = "Approved" WHERE id = ?', (payout_id,))
        elif action == 'decline':
            conn.execute('UPDATE payouts SET status = "Declined" WHERE id = ?', (payout_id,))
            conn.execute('UPDATE users SET points = points + ? WHERE id = ?', (payout['points_requested'], payout['user_id']))
        conn.commit()

    conn.close()
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
