import os
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask, render_template_string, request, redirect, url_for,
    session, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "poly_earn_secret_key_2026_super_safe")

# Your Monetag Direct Link
MONETAG_DIRECT_LINK = "https://uplcm.com/4/12063392"

DB_FILE = "poly_earn.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            points INTEGER DEFAULT 0,
            usdt_address TEXT DEFAULT '',
            referral_code TEXT UNIQUE NOT NULL,
            referred_by TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Ad Views log for limit tracking (25 ads per 2 hours)
    c.execute('''
        CREATE TABLE IF NOT EXISTS ad_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Withdrawal Requests
    c.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount_usdt REAL NOT NULL,
            points_deducted INTEGER NOT NULL,
            address TEXT NOT NULL,
            status TEXT DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please log in first!", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# HTML Template (Dark Mode Mobile First Tailwind CSS)
BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Poly Earn - Watch & Earn USDT</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col font-sans">
    <nav class="bg-slate-900 border-b border-slate-800 p-4 flex justify-between items-center">
        <a href="/" class="text-xl font-bold text-emerald-400">Poly Earn</a>
        <div>
            {% if session.get('user_id') %}
                <a href="/dashboard" class="text-sm bg-slate-800 px-3 py-1.5 rounded-lg mr-2 border border-slate-700">Dashboard</a>
                <a href="/logout" class="text-sm bg-rose-900/40 text-rose-300 px-3 py-1.5 rounded-lg border border-rose-800">Logout</a>
            {% else %}
                <a href="/login" class="text-sm bg-slate-800 px-3 py-1.5 rounded-lg mr-2 border border-slate-700">Login</a>
                <a href="/register" class="text-sm bg-emerald-500 text-slate-950 font-semibold px-3 py-1.5 rounded-lg">Register</a>
            {% endif %}
        </div>
    </nav>

    <main class="flex-1 max-w-md w-full mx-auto p-4">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="mb-4 p-3 rounded-xl border text-sm {{ 'bg-rose-900/30 border-rose-800 text-rose-200' if category == 'error' else 'bg-emerald-900/30 border-emerald-800 text-emerald-200' }}">
                        {{ message }}
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        {% block content %}{% endblock %}
    </main>

    <footer class="text-center py-4 text-xs text-slate-500 border-t border-slate-900">
        &copy; 2026 Poly Earn. All rights reserved.
    </footer>
</body>
</html>
"""

DASHBOARD_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
<div class="space-y-4">
    <!-- Balance Card -->
    <div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl text-center space-y-2">
        <p class="text-xs font-semibold text-slate-400 uppercase tracking-widest">Poly Earn Balance</p>
        <h1 class="text-4xl font-black text-emerald-400">{{ user.points }} Points</h1>
        <p class="text-sm font-medium text-slate-300">≈ ${{ "%.2f"|format(user.points * 0.01) }} USDT</p>
    </div>

    <!-- Watch Ad Button -->
    <div class="bg-slate-900 border border-slate-800 p-5 rounded-2xl text-center space-y-3">
        <h2 class="font-bold text-slate-200">Watch Ads to Earn Points</h2>
        <p class="text-xs text-slate-400">Limit: 25 ads per 2 hours (2 points per ad)</p>
        <div class="text-xs text-emerald-400 font-semibold">
            Ads watched in this window: {{ ads_in_window }} / 25
        </div>
        <a href="/watch-ad" target="_blank" onclick="setTimeout(function(){ location.reload(); }, 3000);" 
           class="block w-full py-3.5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-extrabold rounded-xl text-center transition shadow-lg shadow-emerald-500/10">
            Watch Ad (+2 Points)
        </a>
    </div>

    <!-- Referral Link -->
    <div class="bg-slate-900 border border-slate-800 p-5 rounded-2xl space-y-2">
        <h3 class="font-bold text-sm text-slate-200">Invite & Earn</h3>
        <p class="text-xs text-slate-400">Share your link to get +10 points when friends join!</p>
        <div class="p-2.5 bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono text-emerald-400 break-all select-all">
            {{ ref_link }}
        </div>
    </div>

    <!-- Withdrawal Button -->
    <a href="/withdraw" class="block w-full text-center py-3 bg-slate-800 hover:bg-slate-700 text-slate-200 font-semibold rounded-xl border border-slate-700">
        Request Payout (Min $20 USDT)
    </a>
</div>
""")

REGISTER_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
<div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl space-y-4">
    <h2 class="text-xl font-bold text-slate-100">Create Poly Earn Account</h2>
    <form method="POST" class="space-y-3">
        <div>
            <label class="text-xs text-slate-400 block mb-1">Email Address</label>
            <input type="email" name="email" required class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm focus:outline-none focus:border-emerald-500">
        </div>
        <div>
            <label class="text-xs text-slate-400 block mb-1">Password</label>
            <input type="password" name="password" required class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm focus:outline-none focus:border-emerald-500">
        </div>
        <div>
            <label class="text-xs text-slate-400 block mb-1">Referral Code (Optional)</label>
            <input type="text" name="ref" value="{{ request.args.get('ref', '') }}" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm focus:outline-none focus:border-emerald-500">
        </div>
        <button type="submit" class="w-full py-3 bg-emerald-500 text-slate-950 font-bold rounded-xl hover:bg-emerald-400">
            Sign Up
        </button>
    </form>
</div>
""")

LOGIN_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
<div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl space-y-4">
    <h2 class="text-xl font-bold text-slate-100">Welcome Back</h2>
    <form method="POST" class="space-y-3">
        <div>
            <label class="text-xs text-slate-400 block mb-1">Email Address</label>
            <input type="email" name="email" required class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm focus:outline-none focus:border-emerald-500">
        </div>
        <div>
            <label class="text-xs text-slate-400 block mb-1">Password</label>
            <input type="password" name="password" required class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm focus:outline-none focus:border-emerald-500">
        </div>
        <button type="submit" class="w-full py-3 bg-emerald-500 text-slate-950 font-bold rounded-xl hover:bg-emerald-400">
            Log In
        </button>
    </form>
</div>
""")

WITHDRAW_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
<div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl space-y-4">
    <h2 class="text-xl font-bold text-slate-100">Withdraw USDT (Polygon)</h2>
    <p class="text-xs text-slate-400">Minimum threshold: $20.00 USDT (2000 Points)</p>
    
    <div class="p-3 bg-slate-950 rounded-xl border border-slate-800 text-xs text-slate-300">
        Your current balance: <strong class="text-emerald-400">{{ user.points }} Points</strong> (≈ ${{ "%.2f"|format(user.points * 0.01) }} USDT)
    </div>

    <form method="POST" class="space-y-3">
        <div>
            <label class="text-xs text-slate-400 block mb-1">Polygon USDT Address</label>
            <input type="text" name="usdt_address" value="{{ user.usdt_address }}" placeholder="0x..." required class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm font-mono focus:outline-none focus:border-emerald-500">
        </div>
        <button type="submit" class="w-full py-3 bg-emerald-500 text-slate-950 font-bold rounded-xl hover:bg-emerald-400">
            Submit Withdrawal Request
        </button>
    </form>
</div>
""")

@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        ref = request.form.get('ref', '').strip()

        db = get_db()
        c = db.cursor()
        
        # Generate user referral code
        import uuid
        ref_code = str(uuid.uuid4())[:8]

        try:
            hashed = generate_password_hash(password)
            c.execute('INSERT INTO users (email, password, referral_code, referred_by) VALUES (?, ?, ?, ?)',
                      (email, hashed, ref_code, ref))
            user_id = c.lastrowid
            
            # Reward referrer (+10 points)
            if ref:
                c.execute('UPDATE users SET points = points + 10 WHERE referral_code = ?', (ref,))

            db.commit()
            session['user_id'] = user_id
            flash("Account created successfully!", "success")
            return redirect(url_for('dashboard'))
        except sqlite3.IntegrityError:
            flash("Email already registered!", "error")
        finally:
            db.close()

    return render_template_string(REGISTER_HTML)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']

        db = get_db()
        user = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        db.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            flash("Logged in successfully!", "success")
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid email or password!", "error")

    return render_template_string(LOGIN_HTML)

@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    
    # Count ad views in the last 2 hours
    two_hours_ago = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
    ads_in_window = db.execute(
        'SELECT COUNT(*) FROM ad_views WHERE user_id = ? AND viewed_at >= ?',
        (session['user_id'], two_hours_ago)
    ).fetchone()[0]
    
    db.close()

    ref_link = request.host_url + "register?ref=" + user['referral_code']
    return render_template_string(DASHBOARD_HTML, user=user, ref_link=ref_link, ads_in_window=ads_in_window)

@app.route('/watch-ad')
@login_required
def watch_ad():
    db = get_db()
    two_hours_ago = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
    ads_in_window = db.execute(
        'SELECT COUNT(*) FROM ad_views WHERE user_id = ? AND viewed_at >= ?',
        (session['user_id'], two_hours_ago)
    ).fetchone()[0]

    if ads_in_window >= 25:
        db.close()
        flash("Watch limit reached (25 ads per 2 hours). Please rest small!", "error")
        return redirect(url_for('dashboard'))

    # Record view & add 2 points
    c = db.cursor()
    c.execute('INSERT INTO ad_views (user_id) VALUES (?)', (session['user_id'],))
    c.execute('UPDATE users SET points = points + 2 WHERE id = ?', (session['user_id'],))
    db.commit()
    db.close()

    # Redirect user to Monetag Direct Link
    return redirect(MONETAG_DIRECT_LINK)

@app.route('/withdraw', methods=['GET', 'POST'])
@login_required
def withdraw():
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()

    if request.method == 'POST':
        address = request.form['usdt_address'].strip()
        
        if user['points'] < 2000: # 2000 points = $20 USDT
            flash("You need at least 2000 points ($20 USDT) to withdraw!", "error")
        elif not address.startswith("0x"):
            flash("Please enter a valid Polygon USDT (0x...) address!", "error")
        else:
            c = db.cursor()
            c.execute('UPDATE users SET points = points - 2000, usdt_address = ? WHERE id = ?', (address, session['user_id']))
            c.execute('INSERT INTO withdrawals (user_id, amount_usdt, points_deducted, address) VALUES (?, ?, ?, ?)',
                      (session['user_id'], 20.0, 2000, address))
            db.commit()
            flash("Withdrawal request submitted successfully!", "success")
            return redirect(url_for('dashboard'))

    db.close()
    return render_template_string(WITHDRAW_HTML, user=user)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
