import os
import random
import string
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, url_for, flash, session
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
            is_verified INTEGER DEFAULT 0,
            verification_code TEXT,
            balance REAL DEFAULT 0.0,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def send_verification_email(recipient_email, code):
    sender_email = os.environ.get('SENDER_EMAIL')
    sender_password = os.environ.get('SENDER_PASSWORD')

    if not sender_email or not sender_password:
        print("SENDER_EMAIL or SENDER_PASSWORD not configured in environment variables.")
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = f"Poly Earn <{sender_email}>"
        msg['To'] = recipient_email
        msg['Subject'] = 'Poly Earn - Email Verification Code'

        body = f"""Hello,

Thank you for signing up on Poly Earn!

Your 6-digit email verification code is: {code}

Please enter this code on the verification page to activate your account.

Best regards,
Poly Earn Team"""

        msg.attach(MIMEText(body, 'plain'))

        # Connect using SSL port 465 with strict 5-second timeout to prevent Gunicorn crashes
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=5)
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipient_email, msg.as_string())
        server.quit()
        print(f"Verification email sent to {recipient_email}")
        return True
    except Exception as e:
        print(f"SMTP Error during email send: {e}")
        return False

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
        
        existing_user = cursor.execute('SELECT id, is_verified FROM users WHERE email = ?', (email,)).fetchone()
        
        code = ''.join(random.choices(string.digits, k=6))
        hashed_password = generate_password_hash(password)

        if existing_user:
            if existing_user['is_verified'] == 1:
                conn.close()
                flash('Email already registered and verified. Please login.', 'info')
                return redirect(url_for('login'))
            else:
                cursor.execute('''
                    UPDATE users SET first_name = ?, last_name = ?, password = ?, verification_code = ?
                    WHERE email = ?
                ''', (first_name, last_name, hashed_password, code, email))
        else:
            cursor.execute('''
                INSERT INTO users (first_name, last_name, email, password, verification_code, is_verified)
                VALUES (?, ?, ?, ?, ?, 0)
            ''', (first_name, last_name, email, hashed_password, code))

        conn.commit()
        conn.close()

        # Send mail in background attempt
        send_verification_email(email, code)

        session['pending_email'] = email
        flash('Verification code sent to your email! Please check your inbox/spam.', 'success')
        return redirect(url_for('verify'))

    return render_template('register.html')

@app.route('/verify', methods=['GET', 'POST'])
def verify():
    email = session.get('pending_email')
    if not email:
        flash('No pending verification found. Please register first.', 'warning')
        return redirect(url_for('register'))

    if request.method == 'POST':
        entered_code = request.form.get('code', '').strip()

        conn = get_db()
        cursor = conn.cursor()
        user = cursor.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

        if user and user['verification_code'] == entered_code:
            cursor.execute('UPDATE users SET is_verified = 1, verification_code = NULL WHERE email = ?', (email,))
            conn.commit()
            conn.close()

            session.pop('pending_email', None)
            session['user_id'] = user['id']
            session['user_name'] = f"{user['first_name']} {user['last_name']}"

            flash('Account verified successfully! Welcome to Poly Earn.', 'success')
            return redirect(url_for('dashboard'))
        else:
            conn.close()
            flash('Invalid verification code. Please try again.', 'danger')

    return render_template('verify.html', email=email)

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
            if user['is_verified'] == 0:
                session['pending_email'] = email
                flash('Please verify your email before logging in.', 'warning')
                return redirect(url_for('verify'))

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

@app.route('/admin')
def admin():
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin rights required.', 'danger')
        return redirect(url_for('dashboard'))

    conn = get_db()
    cursor = conn.cursor()
    users = cursor.execute('SELECT id, first_name, last_name, email, is_verified, balance, created_at FROM users').fetchall()
    conn.close()

    return render_template('admin.html', users=users)

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        conn = get_db()
        cursor = conn.cursor()
        user = cursor.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

        if user:
            code = ''.join(random.choices(string.digits, k=6))
            cursor.execute('UPDATE users SET verification_code = ? WHERE email = ?', (code, email))
            conn.commit()
            send_verification_email(email, code)

        conn.close()
        session['pending_email'] = email
        flash('If your email is registered, password reset code has been sent.', 'info')
        return redirect(url_for('reset_password'))

    return render_template('forgot_password.html')

@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    email = session.get('pending_email')
    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        new_password = request.form.get('password', '')

        conn = get_db()
        cursor = conn.cursor()
        user = cursor.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

        if user and user['verification_code'] == code:
            hashed_password = generate_password_hash(new_password)
            cursor.execute('UPDATE users SET password = ?, verification_code = NULL WHERE email = ?', (hashed_password, email))
            conn.commit()
            conn.close()
            session.pop('pending_email', None)
            flash('Password reset successfully. Please login.', 'success')
            return redirect(url_for('login'))
        else:
            conn.close()
            flash('Invalid code or email.', 'danger')

    return render_template('reset_password.html', email=email)

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
