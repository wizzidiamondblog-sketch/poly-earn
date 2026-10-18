import os
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3

app = Flask(__name__)
app.secret_key = os.urandom(24)

DB_NAME = 'database.sqlite'

# Email configuration (You can set these as Environment Variables on Render later)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "your_email@gmail.com") # Put your Gmail here or in Render env vars
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "your_gmail_app_password") # Put your Gmail App Password here

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            password TEXT NOT NULL,
            is_verified INTEGER DEFAULT 0,
            verification_code TEXT,
            reset_code TEXT,
            points INTEGER DEFAULT 0,
            used_points INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def send_email(to_email, subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        first_name = request.form['first_name'].strip()
        last_name = request.form['last_name'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password'].strip()
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, is_verified FROM users WHERE email = ?", (email,))
        existing = cursor.fetchone()
        
        if existing and existing[1] == 1:
            flash("Email already registered. Please login.", "error")
            conn.close()
            return redirect(url_for('login'))
            
        code = str(random.randint(100000, 999999))
        
        if existing:
            cursor.execute("UPDATE users SET first_name = ?, last_name = ?, password = ?, verification_code = ? WHERE email = ?", 
                           (first_name, last_name, password, code, email))
        else:
            cursor.execute("INSERT INTO users (email, first_name, last_name, password, verification_code, is_verified) VALUES (?, ?, ?, ?, ?, 0)", 
                           (email, first_name, last_name, password, code))
        conn.commit()
        conn.close()
        
        # Send verification code email
        subject = "Poly Earn - Email Verification Code"
        body = f"Hello {first_name},\n\nYour Poly Earn verification code is: {code}\n\nEnter this code on the website to activate your account."
        send_email(email, subject, body)
        
        session['temp_email'] = email
        return redirect(url_for('verify'))
        
    return render_template('register.html')

@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if 'temp_email' not in session:
        return redirect(url_for('register'))
        
    if request.method == 'POST':
        entered_code = request.form['code'].strip()
        email = session['temp_email']
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, verification_code FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        
        if user and user[1] == entered_code:
            cursor.execute("UPDATE users SET is_verified = 1, verification_code = NULL WHERE email = ?", (email,))
            conn.commit()
            session['user_id'] = user[0]
            session['email'] = email
            conn.close()
            session.pop('temp_email', None)
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid verification code. Try again.", "error")
            conn.close()
            
    return render_template('verify.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password'].strip()
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, password, is_verified, first_name FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        conn.close()
        
        if user:
            if user[2] == 0:
                session['temp_email'] = email
                return redirect(url_for('verify'))
            if user[1] == password:
                session['user_id'] = user[0]
                session['email'] = email
                session['first_name'] = user[3]
                return redirect(url_for('dashboard'))
                
        flash("Invalid email or password.", "error")
        return redirect(url_for('login'))
            
    return render_template('login.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, first_name FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        
        if user:
            reset_code = str(random.randint(100000, 999999))
            cursor.execute("UPDATE users SET reset_code = ? WHERE email = ?", (reset_code, email))
            conn.commit()
            conn.close()
            
            subject = "Poly Earn - Password Reset Code"
            body = f"Hello {user[1]},\n\nYour password reset code is: {reset_code}\n\nUse this code to set a new password."
            send_email(email, subject, body)
            
            session['reset_email'] = email
            return redirect(url_for('reset_password'))
        else:
            flash("Email not found in our system.", "error")
            conn.close()
            
    return render_template('forgot_password.html')

@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if 'reset_email' not in session:
        return redirect(url_for('forgot_password'))
        
    if request.method == 'POST':
        code = request.form['code'].strip()
        new_password = request.form['password'].strip()
        email = session['reset_email']
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, reset_code FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        
        if user and user[1] == code:
            cursor.execute("UPDATE users SET password = ?, reset_code = NULL WHERE email = ?", (new_password, email))
            conn.commit()
            conn.close()
            session.pop('reset_email', None)
            flash("Password updated successfully! Please login.", "success")
            return redirect(url_for('login'))
        else:
            flash("Invalid reset code.", "error")
            conn.close()
            
    return render_template('reset_password.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT points, used_points, first_name FROM users WHERE id = ?", (session['user_id'],))
    user_data = cursor.fetchone()
    conn.close()
    
    if not user_data:
        return redirect(url_for('logout'))
        
    points = user_data[0]
    used_points = user_data[1]
    first_name = user_data[2]
    usdt_balance = points / 200.0  # 4000 points = $20
    
    return render_template('dashboard.html', 
                           username=first_name, 
                           available_points=points, 
                           usdt_balance=usdt_balance, 
                           used_points=used_points)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
            
