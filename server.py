from flask import Flask, request, jsonify, session, send_from_directory, redirect
from flask_cors import CORS
import requests
import json
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import threading
import time as time_module

app = Flask(__name__, static_folder='.')
app.secret_key = 'your-secret-key-change-this'
CORS(app, supports_credentials=True)

ADMIN_USERS = ['YOUR_STUDENT_ID']  # 修改为你的学号
JW_BASE_URL = 'http://59.57.242.167:81/njwsjd'
DB_FILE = 'courses.db'

def get_setting(key, default):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT value FROM settings WHERE key=?', (key,))
    row = c.fetchone()
    conn.close()
    return int(row[0]) if row else default

def background_fetch():
    """后台定时抓取任务"""
    while True:
        interval = get_setting('fetch_interval', 60) * 60
        time_module.sleep(interval)

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT DISTINCT username FROM users')
        users = [row[0] for row in c.fetchall()]
        conn.close()

        for username in users:
            # 获取当前周次（简化：假设第2周）
            current_week = 2
            for week in [current_week - 1, current_week, current_week + 1]:
                try:
                    data = fetch_from_jw(username, week)
                    conn = sqlite3.connect(DB_FILE)
                    c = conn.cursor()
                    c.execute('INSERT OR REPLACE INTO courses VALUES (?, ?, ?, ?)',
                             (username, week, json.dumps(data, ensure_ascii=False),
                              datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                except:
                    pass

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (username TEXT PRIMARY KEY, password_hash TEXT, last_login TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS courses
                 (username TEXT, week INTEGER, data TEXT, cached_at TEXT,
                  PRIMARY KEY (username, week))''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings
                 (key TEXT PRIMARY KEY, value TEXT)''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    if 'username' not in session:
        return send_from_directory('.', 'login.html')
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('.', path)

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    # 暂时简化：只要有用户名密码就通过
    # TODO: 实际对接教务系统验证
    if username and password:
        session['username'] = username
        session['is_admin'] = username in ADMIN_USERS

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO users VALUES (?, ?, ?)',
                 (username, '', datetime.now().isoformat()))
        conn.commit()
        conn.close()

        return jsonify({'success': True, 'is_admin': username in ADMIN_USERS})

    return jsonify({'success': False, 'message': '请输入用户名和密码'}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/user', methods=['GET'])
def get_user():
    if 'username' in session:
        return jsonify({
            'logged_in': True,
            'username': session['username'],
            'is_admin': session.get('is_admin', False)
        })
    return jsonify({'logged_in': False})

@app.route('/api/courses/<int:week>', methods=['GET'])
def get_courses(week):
    if 'username' not in session:
        return jsonify({'error': '未登录'}), 401

    username = session['username']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute('SELECT data, cached_at FROM courses WHERE username=? AND week=?',
             (username, week))
    row = c.fetchone()

    # 总是尝试更新
    try:
        course_data = fetch_from_jw(username, week)
        c.execute('INSERT OR REPLACE INTO courses VALUES (?, ?, ?, ?)',
                 (username, week, json.dumps(course_data, ensure_ascii=False),
                  datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return jsonify({**course_data, 'from_cache': False})
    except:
        # 失败则使用缓存
        if row:
            conn.close()
            return jsonify({**json.loads(row[0]), 'from_cache': True,
                          'cache_time': row[1]})
        conn.close()
        return jsonify({'error': '无法获取课表'}), 500

def fetch_from_jw(username, week):
    # TODO: 实际从教务系统获取
    # 暂时返回示例数据
    with open('course_data.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    data['metadata']['周次'] = f'第{week}周'
    return data

@app.route('/api/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    if not session.get('is_admin'):
        return jsonify({'error': '无权限'}), 403

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    if request.method == 'POST':
        data = request.json
        for key, value in data.items():
            c.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)',
                     (key, str(value)))
        conn.commit()
        conn.close()
        return jsonify({'success': True})

    c.execute('SELECT key, value FROM settings')
    settings = dict(c.fetchall())
    conn.close()
    return jsonify(settings)

if __name__ == '__main__':
    threading.Thread(target=background_fetch, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=True)
