from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
import json
from datetime import datetime
from pathlib import Path
import sqlite3
import threading
import time as time_module

import jw_client

app = Flask(__name__, static_folder='.')
app.secret_key = 'your-secret-key-change-this'
CORS(app, supports_credentials=True)

ADMIN_USERS = ['2405309121']
DB_FILE = 'courses.db'


# ── 数据库 ────────────────────────────────────────────────────────────────────

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
    # 迁移：添加新列（已存在则忽略）
    for col_def in [
        'jw_token TEXT', 'token_time TEXT', 'jw_name TEXT',
        'jw_class TEXT', 'jw_kbjcmsid TEXT', 'password_enc TEXT',
    ]:
        try:
            c.execute(f'ALTER TABLE users ADD COLUMN {col_def}')
        except Exception:
            pass  # 列已存在
    conn.commit()
    conn.close()


init_db()


def get_setting(key, default):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT value FROM settings WHERE key=?', (key,))
    row = c.fetchone()
    conn.close()
    return int(row[0]) if row else default


# ── Token 管理 ────────────────────────────────────────────────────────────────

def _ensure_token(username: str):
    """
    返回 (token, kbjcmsid, user_info)。
    Token 过期（>3.5h）时自动用存储的密码重新登录；
    kbjcmsid 未缓存时自动获取并存入 DB。
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        'SELECT jw_token, token_time, password_enc, jw_name, jw_class, jw_kbjcmsid '
        'FROM users WHERE username=?',
        (username,),
    )
    row = c.fetchone()
    conn.close()

    if not row or not row[2]:
        raise RuntimeError('未找到用户凭据，请重新登录')

    token, token_time_str, password_enc, jw_name, jw_class, kbjcmsid = row
    user_info = {
        'name':    jw_name or username,
        'userNo':  username,
        'clsName': jw_class or '',
    }

    # 检查 Token 是否仍有效（距登录 < 3.5 小时）
    need_refresh = True
    if token and token_time_str:
        try:
            dt = datetime.fromisoformat(token_time_str)
            if (datetime.now() - dt).total_seconds() < 3.5 * 3600:
                need_refresh = False
        except Exception:
            pass

    if need_refresh:
        password = jw_client.decrypt_from_storage(password_enc)
        info = jw_client.login(username, password)
        token = info['token']
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET jw_token=?, token_time=? WHERE username=?',
                  (token, datetime.now().isoformat(), username))
        conn.commit()
        conn.close()

    # 获取并缓存 kbjcmsid（每学期只需查一次）
    if not kbjcmsid:
        kbjcmsid = jw_client.get_kbjcmsid(token)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET jw_kbjcmsid=? WHERE username=?',
                  (kbjcmsid, username))
        conn.commit()
        conn.close()

    return token, kbjcmsid, user_info


# ── 课表抓取 ──────────────────────────────────────────────────────────────────

def fetch_from_jw(username: str, week: int) -> dict:
    """
    从教务系统获取课表并转换为前端格式。
    week=0 表示当前周（由服务端决定）。
    """
    token, kbjcmsid, user_info = _ensure_token(username)
    actual_week = None if week == 0 else week
    raw = jw_client.get_timetable_raw(token, actual_week, kbjcmsid)
    # 若请求当前周，从响应中读取真实周次
    result_week = int(raw.get('week', week)) if week == 0 else week
    return jw_client.transform_timetable(raw, user_info, result_week)


# ── 后台定时抓取 ──────────────────────────────────────────────────────────────

def background_fetch():
    while True:
        interval = get_setting('fetch_interval', 60) * 60
        time_module.sleep(interval)

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT username FROM users WHERE password_enc IS NOT NULL')
        users = [row[0] for row in c.fetchall()]
        conn.close()

        for username in users:
            try:
                # 先获取当前周，从响应中读取真实周次和最大周次
                data = fetch_from_jw(username, 0)
                current_week = data['metadata']['current_week']
                max_week = data['metadata']['max_week']

                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute('INSERT OR REPLACE INTO courses VALUES (?, ?, ?, ?)',
                          (username, current_week,
                           json.dumps(data, ensure_ascii=False),
                           datetime.now().isoformat()))
                conn.commit()
                conn.close()

                # 额外抓取上周和下周
                for w in [current_week - 1, current_week + 1]:
                    if 1 <= w <= max_week:
                        try:
                            d = fetch_from_jw(username, w)
                            conn = sqlite3.connect(DB_FILE)
                            c = conn.cursor()
                            c.execute('INSERT OR REPLACE INTO courses VALUES (?, ?, ?, ?)',
                                      (username, w,
                                       json.dumps(d, ensure_ascii=False),
                                       datetime.now().isoformat()))
                            conn.commit()
                            conn.close()
                        except Exception:
                            pass
            except Exception:
                pass


# ── 路由 ──────────────────────────────────────────────────────────────────────

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
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'success': False, 'message': '请输入学号和密码'}), 401

    # 用真实教务账号验证
    try:
        user_info = jw_client.login(username, password)
    except RuntimeError as e:
        return jsonify({'success': False, 'message': str(e)}), 401
    except Exception as e:
        return jsonify({'success': False, 'message': f'连接教务系统失败，请稍后再试'}), 500

    token       = user_info['token']
    password_enc = jw_client.encrypt_for_storage(password)
    now         = datetime.now().isoformat()

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT username FROM users WHERE username=?', (username,))
    exists = c.fetchone()

    if exists:
        c.execute(
            'UPDATE users SET password_hash="", last_login=?, jw_token=?, token_time=?, '
            'jw_name=?, jw_class=?, password_enc=? WHERE username=?',
            (now, token, now,
             user_info.get('name', ''), user_info.get('clsName', ''),
             password_enc, username),
        )
    else:
        c.execute(
            'INSERT INTO users (username, password_hash, last_login, jw_token, token_time, '
            'jw_name, jw_class, password_enc) VALUES (?, "", ?, ?, ?, ?, ?, ?)',
            (username, now, token, now,
             user_info.get('name', ''), user_info.get('clsName', ''),
             password_enc),
        )
    conn.commit()
    conn.close()

    session['username'] = username
    session['is_admin']  = username in ADMIN_USERS

    return jsonify({
        'success':  True,
        'is_admin': username in ADMIN_USERS,
        'name':     user_info.get('name', username),
    })


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})


@app.route('/api/user', methods=['GET'])
def get_user():
    if 'username' not in session:
        return jsonify({'logged_in': False})
    username = session['username']
    # 读取真实姓名
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT jw_name FROM users WHERE username=?', (username,))
    row = c.fetchone()
    conn.close()
    name = (row[0] or username) if row else username
    return jsonify({
        'logged_in': True,
        'username':  username,
        'name':      name,
        'is_admin':  session.get('is_admin', False),
    })


@app.route('/api/courses/<int:week>', methods=['GET'])
def get_courses(week):
    if 'username' not in session:
        return jsonify({'error': '未登录'}), 401

    username = session['username']

    # 预先查缓存（week=0 时不预查，因为不知道是哪周）
    cache_row = None
    if week != 0:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT data, cached_at FROM courses WHERE username=? AND week=?',
                  (username, week))
        cache_row = c.fetchone()
        conn.close()

    # 总是先尝试从教务系统获取最新数据
    try:
        course_data  = fetch_from_jw(username, week)
        actual_week  = course_data['metadata']['current_week'] if week == 0 else week

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO courses VALUES (?, ?, ?, ?)',
                  (username, actual_week,
                   json.dumps(course_data, ensure_ascii=False),
                   datetime.now().isoformat()))
        conn.commit()
        conn.close()

        return jsonify({**course_data, 'from_cache': False})

    except Exception as e:
        if cache_row:
            return jsonify({
                **json.loads(cache_row[0]),
                'from_cache':  True,
                'cache_time':  cache_row[1],
            })
        return jsonify({'error': f'无法获取课表：{e}'}), 500


@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    if 'username' not in session:
        return jsonify({'error': '未登录'}), 401

    is_admin = session.get('is_admin', False)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    if request.method == 'POST':
        data = request.json or {}
        # 管理员专属字段
        admin_keys = {'fetch_interval'}
        for key, value in data.items():
            if key in admin_keys and not is_admin:
                continue  # 非管理员忽略管理员专属字段
            c.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)',
                      (key, str(value)))
        conn.commit()
        conn.close()
        return jsonify({'success': True})

    c.execute('SELECT key, value FROM settings')
    result = dict(c.fetchall())
    conn.close()
    result['is_admin'] = is_admin
    return jsonify(result)


if __name__ == '__main__':
    threading.Thread(target=background_fetch, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=True)
