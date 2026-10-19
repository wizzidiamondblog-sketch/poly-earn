import os
import traceback
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'poly_earn_super_secret_2026')

DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

@app.route('/')
def home():
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return "Database Connection Successful! Render setup is working."
    except Exception as e:
        return f"<h1>Database Error:</h1><pre>{traceback.format_exc()}</pre>"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
