import os
import json
import logging
import secrets
import sqlite3
import threading
import time as time_module
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime

from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS

import jw_client

# ── 日志 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger(__name__)

# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder='.')

_secret = os.environ.get('SECRET_KEY')
if not _secret:
    _secret = secrets.token_hex(32)
    logger.warning('SECRET_KEY 未设置，使用随机密钥（重启后 session 失效）')
app.secret_key = _secret

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)
CORS(app, supports_credentials=True)

ADMIN_USERS = ['2405309121']
DB_FILE = os.environ.get('DB_FILE', 'courses.db')

# ── 安全响应头 ────────────────────────────────────────────────────────────────
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

# ── 登录频率限制（5次/分钟/IP） ────────────────────────────────────────────────
_login_attempts: dict = defaultdict(list)

def _rate_limited(ip: str) -> bool:
    now = time_module.time()
    times = [t for t in _login_attempts[ip] if now - t < 60]
    _login_attempts[ip] = times
    if len(times) >= 5:
        return True
    _login_attempts[ip].append(now)
    return False

# ── 数据库 ────────────────────────────────────────────────────────────────────
# 禁止通过静态路由直接访问的文件
_BLOCKED = {
    'courses.db', '.gitignore', '.env', 'Dockerfile',
    'docker-compose.yml', 'deploy.sh', 'start.bat', 'serve.bat',
}
_BLOCKED_EXTS = {'.py', '.db', '.sh', '.bat', '.env', '.cfg', '.ini'}

@contextmanager
def _db():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.execute('PRAGMA journal_mode=WAL')
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with _db() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (username TEXT PRIMARY KEY, password_hash TEXT, last_login TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS courses
                     (username TEXT, week INTEGER, data TEXT, cached_at TEXT,
                      PRIMARY KEY (username, week))''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings
                     (key TEXT PRIMARY KEY, value TEXT)''')
        for col_def in [
            'jw_token TEXT', 'token_time TEXT', 'jw_name TEXT',
            'jw_class TEXT', 'jw_kbjcmsid TEXT', 'password_enc TEXT',
        ]:
            try:
                c.execute(f'ALTER TABLE users ADD COLUMN {col_def}')
            except Exception:
                pass
        conn.commit()


init_db()


def get_setting(key, default):
    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT value FROM settings WHERE key=?', (key,))
        row = c.fetchone()
    return int(row[0]) if row else default


# ── Token 管理 ────────────────────────────────────────────────────────────────
def _ensure_token(username: str):
    with _db() as conn:
        c = conn.cursor()
        c.execute(
            'SELECT jw_token, token_time, password_enc, jw_name, jw_class, jw_kbjcmsid '
            'FROM users WHERE username=?',
            (username,),
        )
        row = c.fetchone()

    if not row or not row[2]:
        raise RuntimeError('未找到用户凭据，请重新登录')

    token, token_time_str, password_enc, jw_name, jw_class, kbjcmsid = row
    user_info = {
        'name':    jw_name or username,
        'userNo':  username,
        'clsName': jw_class or '',
    }

    need_refresh = True
    if token and token_time_str:
        try:
            dt = datetime.fromisoformat(token_time_str)
            if (datetime.now() - dt).total_seconds() < 3.5 * 3600:
                need_refresh = False
        except ValueError:
            logger.warning('token_time 格式异常 user=%s value=%s', username, token_time_str)

    if need_refresh:
        password = jw_client.decrypt_from_storage(password_enc)
        info  = jw_client.login(username, password)
        token = info['token']
        with _db() as conn:
            conn.execute('UPDATE users SET jw_token=?, token_time=? WHERE username=?',
                         (token, datetime.now().isoformat(), username))
            conn.commit()

    if not kbjcmsid:
        kbjcmsid = jw_client.get_kbjcmsid(token)
        with _db() as conn:
            conn.execute('UPDATE users SET jw_kbjcmsid=? WHERE username=?',
                         (kbjcmsid, username))
            conn.commit()

    return token, kbjcmsid, user_info


# ── 课表抓取 ──────────────────────────────────────────────────────────────────
def fetch_from_jw(username: str, week: int) -> dict:
    token, kbjcmsid, user_info = _ensure_token(username)
    actual_week = None if week == 0 else week
    raw = jw_client.get_timetable_raw(token, actual_week, kbjcmsid)
    result_week = int(raw.get('week', week)) if week == 0 else week
    return jw_client.transform_timetable(raw, user_info, result_week)


# ── 后台定时抓取 ──────────────────────────────────────────────────────────────
def background_fetch():
    while True:
        interval = get_setting('fetch_interval', 60) * 60
        time_module.sleep(interval)

        with _db() as conn:
            c = conn.cursor()
            c.execute('SELECT username FROM users WHERE password_enc IS NOT NULL')
            users = [row[0] for row in c.fetchall()]

        for username in users:
            try:
                data = fetch_from_jw(username, 0)
                current_week = data['metadata']['current_week']
                max_week     = data['metadata']['max_week']

                with _db() as conn:
                    conn.execute('INSERT OR REPLACE INTO courses VALUES (?, ?, ?, ?)',
                                 (username, current_week,
                                  json.dumps(data, ensure_ascii=False),
                                  datetime.now().isoformat()))
                    conn.commit()

                for w in [current_week - 1, current_week + 1]:
                    if 1 <= w <= max_week:
                        try:
                            d = fetch_from_jw(username, w)
                            with _db() as conn:
                                conn.execute('INSERT OR REPLACE INTO courses VALUES (?, ?, ?, ?)',
                                             (username, w,
                                              json.dumps(d, ensure_ascii=False),
                                              datetime.now().isoformat()))
                                conn.commit()
                        except Exception as e:
                            logger.warning('后台抓取失败 user=%s week=%d: %s', username, w, e)
            except Exception as e:
                logger.error('后台抓取失败 user=%s: %s', username, e)


threading.Thread(target=background_fetch, daemon=True).start()

# ── 路由 ──────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    if 'username' not in session:
        return send_from_directory('.', 'login.html')
    return send_from_directory('.', 'index.html')


@app.route('/<path:path>')
def static_files(path):
    filename = path.rsplit('/', 1)[-1]
    ext = os.path.splitext(filename)[1].lower()
    if filename in _BLOCKED or ext in _BLOCKED_EXTS:
        return jsonify({'error': 'Not found'}), 404
    return send_from_directory('.', path)


@app.route('/api/login', methods=['POST'])
def login():
    ip = request.remote_addr
    if _rate_limited(ip):
        return jsonify({'success': False, 'message': '登录尝试过于频繁，请稍后再试'}), 429

    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'success': False, 'message': '请输入学号和密码'}), 401

    try:
        user_info = jw_client.login(username, password)
    except RuntimeError as e:
        return jsonify({'success': False, 'message': str(e)}), 401
    except Exception as e:
        logger.error('JW 登录失败 user=%s: %s', username, e)
        return jsonify({'success': False, 'message': '连接教务系统失败，请稍后再试'}), 500

    token        = user_info['token']
    password_enc = jw_client.encrypt_for_storage(password)
    now          = datetime.now().isoformat()

    with _db() as conn:
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
    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT jw_name FROM users WHERE username=?', (username,))
        row = c.fetchone()
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

    username  = session['username']
    cache_row = None
    if week != 0:
        with _db() as conn:
            c = conn.cursor()
            c.execute('SELECT data, cached_at FROM courses WHERE username=? AND week=?',
                      (username, week))
            cache_row = c.fetchone()

    try:
        course_data = fetch_from_jw(username, week)
        actual_week = course_data['metadata']['current_week'] if week == 0 else week

        with _db() as conn:
            conn.execute('INSERT OR REPLACE INTO courses VALUES (?, ?, ?, ?)',
                         (username, actual_week,
                          json.dumps(course_data, ensure_ascii=False),
                          datetime.now().isoformat()))
            conn.commit()

        return jsonify({**course_data, 'from_cache': False})

    except Exception as e:
        logger.error('获取课表失败 user=%s week=%s: %s', username, week, e)
        if cache_row:
            return jsonify({
                **json.loads(cache_row[0]),
                'from_cache': True,
                'cache_time': cache_row[1],
            })
        return jsonify({'error': '无法获取课表，请稍后重试'}), 500


@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    if 'username' not in session:
        return jsonify({'error': '未登录'}), 401

    is_admin = session.get('is_admin', False)

    if request.method == 'POST':
        data = request.json or {}
        # fetch_interval：管理员专属，需校验范围
        if 'fetch_interval' in data and is_admin:
            try:
                interval = int(data['fetch_interval'])
                if not (5 <= interval <= 1440):
                    return jsonify({'error': '抓取间隔需在 5-1440 分钟之间'}), 400
            except (ValueError, TypeError):
                return jsonify({'error': '无效的间隔值'}), 400
            with _db() as conn:
                conn.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)',
                             ('fetch_interval', str(interval)))
                conn.commit()
        return jsonify({'success': True})

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT key, value FROM settings')
        result = dict(c.fetchall())
    result['is_admin'] = is_admin
    return jsonify(result)


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    port  = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=debug)
