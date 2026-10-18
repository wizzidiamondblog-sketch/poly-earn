import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3

app = Flask(__name__)
app.secret_key = os.urandom(24)

DB_NAME = 'database.sqlite'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            points INTEGER DEFAULT 0,
            used_points INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        existing = cursor.fetchone()
        
        if existing:
            flash("Username already taken. Choose another one or login.", "error")
            conn.close()
            return redirect(url_for('register'))
            
        cursor.execute("INSERT INTO users (username, password, points, used_points) VALUES (?, ?, 0, 0)", (username, password))
        conn.commit()
        
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        new_user = cursor.fetchone()
        session['user_id'] = new_user[0]
        session['username'] = username
        conn.close()
        
        return redirect(url_for('dashboard'))
        
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, password FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()
        
        if user and user[1] == password:
            session['user_id'] = user[0]
            session['username'] = username
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid username or password.", "error")
            return redirect(url_for('login'))
            
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT points, used_points FROM users WHERE id = ?", (session['user_id'],))
    user_data = cursor.fetchone()
    conn.close()
    
    if not user_data:
        return redirect(url_for('logout'))
        
    points = user_data[0]
    used_points = user_data[1]
    usdt_balance = points / 200.0  # 4000 points = $20
    
    return render_template('dashboard.html', 
                           username=session['username'], 
                           available_points=points, 
                           usdt_balance=usdt_balance, 
                           used_points=used_points)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
