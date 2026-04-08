import os
import json
import logging
import secrets
import sqlite3
import subprocess
import threading
import time as time_module
import re
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, session, send_from_directory, g
from flask_cors import CORS
from werkzeug.exceptions import BadRequest
from werkzeug.security import safe_join

import jw_client

# ── CSRF 保护 ──────────────────────────────────────────────────────────────────
def generate_csrf_token():
    """生成 CSRF token 并存储在 session 中"""
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

def validate_csrf_token(token):
    """验证 CSRF token"""
    return token and token == session.get('_csrf_token')

def csrf_protected(f):
    """CSRF 保护装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # GET, HEAD, OPTIONS 不需要 CSRF 保护
        if request.method in ['GET', 'HEAD', 'OPTIONS']:
            return f(*args, **kwargs)

        # 从请求头或请求体获取 CSRF token
        token = request.headers.get('X-CSRF-Token') or (request.json or {}).get('_csrf_token')

        if not validate_csrf_token(token):
            logger.warning(f'CSRF 验证失败: {request.remote_addr} {request.path}')
            return jsonify({'success': False, 'message': '安全验证失败,请刷新页面重试'}), 403

        return f(*args, **kwargs)
    return decorated_function

# ── 全局锁 ────────────────────────────────────────────────────────────────────
_token_refresh_locks = {}  # username -> Lock
_token_locks_lock = threading.Lock()

def _get_user_lock(username: str) -> threading.Lock:
    """获取用户专属的 token 刷新锁"""
    with _token_locks_lock:
        if username not in _token_refresh_locks:
            _token_refresh_locks[username] = threading.Lock()
        return _token_refresh_locks[username]

# ── 输入验证 ──────────────────────────────────────────────────────────────────
def validate_week(week: int) -> bool:
    """验证周次"""
    return isinstance(week, int) and 0 <= week <= 30

def sanitize_string(s: str, max_length: int = 1000) -> str:
    """清理字符串输入"""
    if not isinstance(s, str):
        return ''
    # 移除控制字符
    s = ''.join(char for char in s if ord(char) >= 32 or char in '\n\r\t')
    return s[:max_length].strip()

def validate_share_token(token: str) -> bool:
    """验证分享码格式"""
    if not token or not isinstance(token, str):
        return False
    # 分享码应该是大写字母和数字的组合，长度 8-20
    return bool(re.match(r'^[A-Z0-9]{8,20}$', token.upper()))

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
    _key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.secret_key')
    if os.path.exists(_key_file):
        with open(_key_file, 'r') as f:
            _secret = f.read().strip()
    else:
        _secret = secrets.token_hex(32)
        with open(_key_file, 'w') as f:
            f.write(_secret)
        logger.info('已生成并保存持久化 secret_key 到 .secret_key')
app.secret_key = _secret

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    # 默认要求 HTTPS，除非明确设置 ALLOW_HTTP=true（仅用于本地开发）
    SESSION_COOKIE_SECURE=os.environ.get('ALLOW_HTTP', 'false').lower() != 'true',
    SESSION_COOKIE_PERMANENT=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # 限制请求大小为 16MB
)
CORS(app, supports_credentials=True)

ADMIN_USERS = [u.strip() for u in os.environ.get('ADMIN_USERS', '2405309121').split(',') if u.strip()]
DB_FILE = os.environ.get('DB_FILE', 'courses.db')

# ── 安全响应头 ────────────────────────────────────────────────────────────────
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'

    # 添加 Content-Security-Policy
    # 注意：这是一个严格的 CSP 策略，只允许同源资源
    # 如果需要加载外部资源，需要根据实际情况调整
    csp_policy = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "  # unsafe-inline 用于内联脚本，如果可能应该使用 nonce
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    response.headers['Content-Security-Policy'] = csp_policy

    # 添加 Referrer-Policy
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

    # 添加 Permissions-Policy
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'

    return response

# ── 登录频率限制（5次/分钟/IP） ────────────────────────────────────────────────
_login_attempts: dict = defaultdict(list)
_login_attempts_lock = threading.Lock()
_last_cleanup = time_module.time()

def _rate_limited(ip: str) -> bool:
    global _last_cleanup
    now = time_module.time()

    with _login_attempts_lock:
        # 每 5 分钟清理一次过期的 IP 记录
        if now - _last_cleanup > 300:
            expired_ips = [k for k, v in _login_attempts.items()
                          if not v or now - v[-1] > 300]
            for k in expired_ips:
                del _login_attempts[k]
            _last_cleanup = now

        times = [t for t in _login_attempts[ip] if now - t < 60]
        _login_attempts[ip] = times
        if len(times) >= 5:
            return True
        _login_attempts[ip].append(now)
        return False

# ── 数据库 ────────────────────────────────────────────────────────────────────
# 禁止通过静态路由直接访问的文件
_BLOCKED = {
    'courses.db', '.gitignore', '.env', '.secret_key', 'Dockerfile',
    'docker-compose.yml', 'deploy.sh', 'start.bat', 'serve.bat',
}
_BLOCKED_EXTS = {'.py', '.db', '.sh', '.bat', '.env', '.cfg', '.ini'}

@contextmanager
def _db():
    conn = sqlite3.connect(DB_FILE, timeout=30)  # 增加超时时间到 30 秒
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with _db() as conn:
        # WAL 模式只需设置一次，之后持久生效
        conn.execute('PRAGMA journal_mode=WAL')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (username TEXT PRIMARY KEY, password_hash TEXT, last_login TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS courses
                     (username TEXT, week INTEGER, data TEXT, cached_at TEXT,
                      PRIMARY KEY (username, week))''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings
                     (key TEXT PRIMARY KEY, value TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS share_tokens
                     (token TEXT PRIMARY KEY, owner TEXT NOT NULL,
                      week_from INTEGER NOT NULL, week_to INTEGER NOT NULL,
                      expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
                      revoked INTEGER NOT NULL DEFAULT 0)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_share_owner ON share_tokens(owner)')
        # ICS订阅令牌表
        c.execute('''CREATE TABLE IF NOT EXISTS ics_tokens
                     (token TEXT PRIMARY KEY, username TEXT NOT NULL,
                      created_at TEXT NOT NULL, revoked INTEGER DEFAULT 0)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_ics_username ON ics_tokens(username)')
        # 用户组表
        c.execute('''CREATE TABLE IF NOT EXISTS user_groups
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                      can_use_ics INTEGER DEFAULT 1, can_create_share INTEGER DEFAULT 1,
                      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        # 用户表中添加组ID列
        try:
            c.execute('ALTER TABLE users ADD COLUMN group_id INTEGER')
        except sqlite3.OperationalError as e:
            if 'duplicate column name' not in str(e).lower():
                logger.warning('数据库迁移警告: %s', e)
        # 创建默认用户组（如果不存在）
        c.execute('SELECT COUNT(*) FROM user_groups')
        if c.fetchone()[0] == 0:
            c.execute('''INSERT INTO user_groups (name, can_use_ics, can_create_share)
                         VALUES ('默认组', 1, 1)''')
            c.execute('''INSERT INTO user_groups (name, can_use_ics, can_create_share)
                         VALUES ('受限组', 0, 0)''')
            # 将所有现有用户设置为默认组
            c.execute('UPDATE users SET group_id = 1 WHERE group_id IS NULL')
        for col_def in [
            'jw_token TEXT', 'token_time TEXT', 'jw_name TEXT',
            'jw_class TEXT', 'jw_kbjcmsid TEXT', 'password_enc TEXT', 'last_active TEXT',
        ]:
            try:
                c.execute(f'ALTER TABLE users ADD COLUMN {col_def}')
            except sqlite3.OperationalError as e:
                # 忽略"列已存在"错误，其他错误记录日志
                if 'duplicate column name' not in str(e).lower():
                    logger.warning('数据库迁移警告: %s', e)
        conn.commit()


init_db()


def get_setting(key, default):
    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT value FROM settings WHERE key=?', (key,))
        row = c.fetchone()
    if not row:
        return default
    if isinstance(default, int):
        try:
            return int(row[0])
        except (ValueError, TypeError):
            return default
    return row[0]


# ── Token 管理 ────────────────────────────────────────────────────────────────
def _ensure_token(username: str):
    # 使用用户级别的锁防止并发刷新
    with _get_user_lock(username):
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


def _background_refresh_user(username: str, week: int):
    """缓存过期时后台异步刷新，不阻塞用户请求"""
    try:
        data = fetch_from_jw(username, week)
        actual_week = data['metadata']['current_week'] if week == 0 else week
        with _db() as conn:
            conn.execute('INSERT OR REPLACE INTO courses VALUES (?, ?, ?, ?)',
                         (username, actual_week,
                          json.dumps(data, ensure_ascii=False),
                          datetime.now().isoformat()))
            conn.commit()
        logger.info('后台刷新完成 user=%s week=%s', username, week)
    except Exception as e:
        logger.info('后台刷新失败 user=%s week=%s: %s', username, week, e)


# ── 后台定时抓取 ──────────────────────────────────────────────────────────────
# 用户失败计数，连续失败 3 次后降频重试（每 10 轮重试一次）
_user_fail_counts = defaultdict(int)
_user_skip_counts = defaultdict(int)  # 当前已跳过轮数

def background_fetch():
    while True:
        try:
            with _db() as conn:
                c = conn.cursor()
                c.execute('SELECT username FROM users WHERE password_enc IS NOT NULL')
                users = [row[0] for row in c.fetchall()]

            for username in users:
                # 连续失败 3 次后降频：每 10 轮才重试一次
                if _user_fail_counts[username] >= 3:
                    _user_skip_counts[username] += 1
                    if _user_skip_counts[username] < 10:
                        logger.info('降频跳过用户 user=%s skip=%d/10',
                                    username, _user_skip_counts[username])
                        continue
                    else:
                        _user_skip_counts[username] = 0  # 重置，允许重试

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
                                logger.info('后台抓取邻近周失败 user=%s week=%d: %s', username, w, e)

                    # 成功后重置失败计数和跳过计数
                    _user_fail_counts[username] = 0
                    _user_skip_counts[username] = 0

                except Exception as e:
                    _user_fail_counts[username] += 1
                    if _user_fail_counts[username] >= 3:
                        logger.warning('后台抓取连续失败 user=%s 失败次数=%d: %s',
                                      username, _user_fail_counts[username], e)
                    else:
                        logger.info('后台抓取失败 user=%s: %s', username, e)

        except Exception as e:
            logger.error('后台定时任务异常: %s', e)

        interval = get_setting('fetch_interval', 60) * 60
        time_module.sleep(interval)


threading.Thread(target=background_fetch, daemon=True).start()

# ── 路由 ──────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    # 始终返回 index.html；认证检查由前端 JS 处理（支持导入预览模式）
    return send_from_directory('.', 'index.html')


@app.route('/<path:path>')
def static_files(path):
    # 防止路径遍历攻击：验证路径不会跳出根目录
    safe_path = safe_join('.', path)
    if not safe_path:
        logger.warning(f'路径遍历尝试被拦截: {request.remote_addr} {path}')
        return jsonify({'error': 'Not found'}), 404

    # 额外的黑名单检查（深度防御）
    filename = path.rsplit('/', 1)[-1]
    ext = os.path.splitext(filename)[1].lower()
    if filename in _BLOCKED or ext in _BLOCKED_EXTS:
        return jsonify({'error': 'Not found'}), 404

    return send_from_directory('.', safe_path)


# ── CSRF Token API ─────────────────────────────────────────────────────────────
@app.route('/api/csrf-token', methods=['GET'])
def get_csrf_token():
    """获取 CSRF token"""
    return jsonify({'csrf_token': generate_csrf_token()})


@app.route('/api/login', methods=['POST'])
def login():
    ip = request.remote_addr
    if _rate_limited(ip):
        return jsonify({'success': False, 'message': '登录尝试过于频繁，请稍后再试'}), 429

    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    # 基础验证
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
            default_group_id = _get_default_group_id()
            c.execute(
                'INSERT INTO users (username, password_hash, last_login, jw_token, token_time, '
                'jw_name, jw_class, password_enc, group_id) VALUES (?, "", ?, ?, ?, ?, ?, ?, ?)',
                (username, now, token, now,
                 user_info.get('name', ''), user_info.get('clsName', ''),
                 password_enc, default_group_id),
            )
        conn.commit()

    # 安全关键：登录成功后重新生成 session ID，防止 session 固定攻击
    session.clear()
    session.permanent = True
    session['username'] = username
    session['is_admin']  = username in ADMIN_USERS

    # 生成新的 CSRF token
    csrf_token = generate_csrf_token()

    return jsonify({
        'success':     True,
        'is_admin':    username in ADMIN_USERS,
        'name':        user_info.get('name', username),
        'csrf_token':  csrf_token,
    })


@app.route('/api/logout', methods=['POST'])
@csrf_protected
def logout():
    session.clear()
    return jsonify({'success': True})


@app.route('/api/user', methods=['GET'])
def get_user():
    slot34_pattern = get_setting('slot34_special_pattern', '')

    # 分享浏览模式
    if 'share_token' in session:
        token = session['share_token']
        with _db() as conn:
            c = conn.cursor()
            c.execute('SELECT owner, expires_at, week_from, week_to, revoked '
                      'FROM share_tokens WHERE token=?', (token,))
            row = c.fetchone()
        if not row or row[4]:
            session.clear()
            return jsonify({'logged_in': False})
        owner, expires_at, week_from, week_to, _ = row
        try:
            if datetime.fromisoformat(expires_at) < datetime.now():
                session.clear()
                return jsonify({'logged_in': False})
        except ValueError:
            session.clear()
            return jsonify({'logged_in': False})
        with _db() as conn:
            c = conn.cursor()
            c.execute('SELECT jw_name FROM users WHERE username=?', (owner,))
            r = c.fetchone()
        owner_name = (r[0] or owner) if r else owner
        return jsonify({
            'logged_in':              True,
            'is_share_mode':          True,
            'owner_name':             owner_name,
            'share_week_from':        week_from,
            'share_week_to':          week_to,
            'share_expires_at':       expires_at,
            'slot34_special_pattern': slot34_pattern,
        })

    if 'username' not in session:
        return jsonify({'logged_in': False})
    username = session['username']
    now = datetime.now().isoformat()
    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT jw_name FROM users WHERE username=?', (username,))
        row = c.fetchone()
        # 更新最近活跃时间
        c.execute('UPDATE users SET last_active=? WHERE username=?', (now, username))
        conn.commit()
    name = (row[0] or username) if row else username
    return jsonify({
        'logged_in':              True,
        'username':               username,
        'name':                   name,
        'is_admin':               session.get('is_admin', False),
        'slot34_special_pattern': slot34_pattern,
    })


@app.route('/api/courses/<int:week>', methods=['GET'])
def get_courses(week):
    # ── 确定访问身份与周次限制 ──────────────────────────────────────────────────
    share_mode = False
    week_min, week_max = 1, 99

    if 'share_token' in session:
        token = session['share_token']
        with _db() as conn:
            c = conn.cursor()
            c.execute('SELECT owner, expires_at, week_from, week_to, revoked '
                      'FROM share_tokens WHERE token=?', (token,))
            row = c.fetchone()
        if not row or row[4]:
            session.clear()
            return jsonify({'error': '分享码已失效，请重新获取'}), 401
        owner, expires_at, week_from, week_to, _ = row
        try:
            if datetime.fromisoformat(expires_at) < datetime.now():
                session.clear()
                return jsonify({'error': '分享码已过期'}), 401
        except ValueError:
            session.clear()
            return jsonify({'error': '分享码无效'}), 401
        username   = owner
        share_mode = True
        week_min, week_max = week_from, week_to
    elif 'username' in session:
        username = session['username']
    else:
        return jsonify({'error': '未登录'}), 401

    # 分享模式：week=0 默认从允许的最小周开始
    if share_mode and week == 0:
        week = week_min

    # 分享模式：强制校验周次范围
    if share_mode and not (week_min <= week <= week_max):
        return jsonify({'error': f'分享码仅允许查看第 {week_min}~{week_max} 周'}), 403

    # ── 优先读缓存，无缓存时才实时抓取 ──────────────────────────────────────────
    force = request.args.get('force') == '1'

    if not force:
        with _db() as conn:
            c = conn.cursor()
            if week == 0:
                # 先从最近一次缓存中读取 current_week，再精确取那一周的缓存
                # 避免返回邻近周数据导致前端周次显示与课表内容不一致
                c.execute('SELECT data FROM courses WHERE username=? '
                          'ORDER BY cached_at DESC LIMIT 1', (username,))
                row = c.fetchone()
                if row:
                    cur_w = json.loads(row[0]).get('metadata', {}).get('current_week')
                    if cur_w:
                        c.execute('SELECT data, cached_at FROM courses WHERE username=? AND week=?',
                                  (username, cur_w))
                    else:
                        c.execute('SELECT data, cached_at FROM courses WHERE username=? '
                                  'ORDER BY cached_at DESC LIMIT 1', (username,))
                    cache_row = c.fetchone()
                else:
                    cache_row = None
            else:
                c.execute('SELECT data, cached_at FROM courses WHERE username=? AND week=?',
                          (username, week))
                cache_row = c.fetchone()
        if cache_row:
            stale = False
            try:
                age = (datetime.now() - datetime.fromisoformat(cache_row[1])).total_seconds()
                if age > get_setting('fetch_interval', 60) * 60:
                    stale = True
            except (ValueError, TypeError):
                stale = True

            # 有缓存就立即返回，过期则后台异步刷新
            if stale:
                threading.Thread(
                    target=_background_refresh_user,
                    args=(username, week),
                    daemon=True
                ).start()
            return jsonify({
                **json.loads(cache_row[0]),
                'from_cache': True,
                'cache_time': cache_row[1],
                'refreshing': stale,
            })

    # 完全无缓存 → 实时抓取
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
        return jsonify({'error': '无法获取课表，请稍后重试'}), 500


@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    if 'username' not in session:
        return jsonify({'error': '未登录'}), 401

    is_admin = session.get('is_admin', False)

    if request.method == 'POST':
        # CSRF 保护
        token = request.headers.get('X-CSRF-Token') or (request.json or {}).get('_csrf_token')
        if not validate_csrf_token(token):
            return jsonify({'success': False, 'message': '安全验证失败'}), 403

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
        # slot34_special_pattern：管理员专属
        if 'slot34_special_pattern' in data and is_admin:
            pattern = str(data['slot34_special_pattern']).strip()
            with _db() as conn:
                conn.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)',
                             ('slot34_special_pattern', pattern))
                conn.commit()
        return jsonify({'success': True})

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT key, value FROM settings')
        result = dict(c.fetchall())
    result['is_admin'] = is_admin

    # 附带用户最新缓存的 max_week，供前端导出使用
    username = session['username']
    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT data FROM courses WHERE username=? ORDER BY cached_at DESC LIMIT 1',
                  (username,))
        row = c.fetchone()
    max_week = 20
    if row:
        try:
            max_week = json.loads(row[0]).get('metadata', {}).get('max_week', 20)
        except Exception:
            pass
    result['max_week'] = max_week
    return jsonify(result)


# ── 分享码 ────────────────────────────────────────────────────────────────────

@app.route('/api/share/verify', methods=['GET'])
def share_verify():
    """验证分享码（不设置session，仅返回信息供前端使用）"""
    token = request.args.get('token', '').strip().upper()
    if not token:
        return jsonify({'valid': False, 'message': '请输入分享码'}), 400

    # 验证分享码格式
    if not validate_share_token(token):
        return jsonify({'valid': False, 'message': '分享码格式无效'}), 400

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT owner, expires_at, week_from, week_to, revoked '
                  'FROM share_tokens WHERE token=?', (token,))
        row = c.fetchone()

    if not row or row[4]:
        return jsonify({'valid': False, 'message': '分享码无效或已撤销'}), 400
    owner, expires_at, week_from, week_to, _ = row

    try:
        if datetime.fromisoformat(expires_at) < datetime.now():
            return jsonify({'valid': False, 'message': '分享码已过期'}), 400
    except ValueError:
        return jsonify({'valid': False, 'message': '分享码无效'}), 400

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT jw_name FROM users WHERE username=?', (owner,))
        r = c.fetchone()
    owner_name = (r[0] or owner) if r else owner

    return jsonify({
        'valid': True,
        'token': token,
        'owner_name': owner_name,
        'week_from': week_from,
        'week_to': week_to,
        'expires_at': expires_at,
    })


@app.route('/api/share/enter', methods=['POST'])
@csrf_protected
def share_enter():
    """用分享码进入只读浏览模式（无需账号密码）"""
    ip = request.remote_addr
    if _rate_limited(ip):
        return jsonify({'success': False, 'message': '请求过于频繁，请稍后再试'}), 429

    token = (request.json or {}).get('token', '').strip().upper()
    if not token:
        return jsonify({'success': False, 'message': '请输入分享码'}), 400

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT owner, expires_at, week_from, week_to, revoked '
                  'FROM share_tokens WHERE token=?', (token,))
        row = c.fetchone()

    if not row or row[4]:
        return jsonify({'success': False, 'message': '分享码无效或已撤销'}), 400
    owner, expires_at, week_from, week_to, _ = row

    try:
        if datetime.fromisoformat(expires_at) < datetime.now():
            return jsonify({'success': False, 'message': '分享码已过期'}), 400
    except ValueError:
        return jsonify({'success': False, 'message': '分享码无效'}), 400

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT jw_name FROM users WHERE username=?', (owner,))
        r = c.fetchone()
    owner_name = (r[0] or owner) if r else owner

    session.clear()
    session['share_token'] = token
    session['share_owner'] = owner
    return jsonify({
        'success':    True,
        'owner_name': owner_name,
        'week_from':  week_from,
        'week_to':    week_to,
        'expires_at': expires_at,
    })


@app.route('/api/share/create', methods=['POST'])
@csrf_protected
def share_create():
    """创建分享码（仅限已登录的正式用户）"""
    if 'username' not in session:
        return jsonify({'error': '未登录'}), 401

    username = session['username']
    is_admin = session.get('is_admin', False)

    # 检查是否有权限创建分享码
    if not _can_create_share(username, is_admin):
        return jsonify({'error': '您没有创建分享码的权限'}), 403

    data     = request.json or {}

    days = {'1d': 1, '7d': 7, '30d': 30, '180d': 180}.get(
        data.get('expires_in', '7d'), 7)
    expires_at = (datetime.now() + timedelta(days=days)).isoformat()

    try:
        week_from = max(1, int(data.get('week_from', 1)))
        week_to   = min(30, int(data.get('week_to', 19)))
        if week_from > week_to:
            return jsonify({'error': '起始周不能大于结束周'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': '无效的周次'}), 400

    # 重试机制：最多尝试 5 次生成不冲突的 token
    for attempt in range(5):
        token = secrets.token_hex(4).upper()
        try:
            with _db() as conn:
                conn.execute(
                    'INSERT INTO share_tokens '
                    '(token, owner, week_from, week_to, expires_at, created_at, revoked) '
                    'VALUES (?, ?, ?, ?, ?, ?, 0)',
                    (token, username, week_from, week_to, expires_at, datetime.now().isoformat()),
                )
                conn.commit()
            break
        except sqlite3.IntegrityError:
            if attempt == 4:
                logger.error('分享码生成失败：5 次尝试均冲突 user=%s', username)
                return jsonify({'error': '生成分享码失败，请重试'}), 500
            continue

    return jsonify({
        'success':    True,
        'token':      token,
        'expires_at': expires_at,
        'week_from':  week_from,
        'week_to':    week_to,
    })


@app.route('/api/share/list', methods=['GET'])
def share_list():
    """列出分享码；管理员加 ?all=1 可查看所有用户"""
    if 'username' not in session:
        return jsonify({'error': '未登录'}), 401

    username = session['username']
    is_admin = session.get('is_admin', False)
    show_all = is_admin and request.args.get('all') == '1'
    now      = datetime.now().isoformat()

    with _db() as conn:
        c = conn.cursor()
        if show_all:
            c.execute(
                'SELECT st.token, st.week_from, st.week_to, st.expires_at, st.created_at, '
                '       st.owner, u.jw_name '
                'FROM share_tokens st LEFT JOIN users u ON st.owner = u.username '
                'WHERE st.revoked=0 ORDER BY st.created_at DESC',
            )
        else:
            c.execute(
                'SELECT token, week_from, week_to, expires_at, created_at, owner, NULL '
                'FROM share_tokens WHERE owner=? AND revoked=0 ORDER BY created_at DESC',
                (username,),
            )
        rows = c.fetchall()

    return jsonify({'tokens': [
        {
            'token':      r[0],
            'week_from':  r[1],
            'week_to':    r[2],
            'expires_at': r[3],
            'created_at': r[4],
            'owner':      r[5],
            'owner_name': r[6] or r[5],
            'expired':    r[3] < now,
        }
        for r in rows
    ]})


@app.route('/api/share/revoke', methods=['POST'])
@csrf_protected
def share_revoke():
    """撤销分享码；管理员可撤销任意码"""
    if 'username' not in session:
        return jsonify({'error': '未登录'}), 401

    username = session['username']
    is_admin = session.get('is_admin', False)
    token    = (request.json or {}).get('token', '').strip().upper()

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT owner FROM share_tokens WHERE token=?', (token,))
        row = c.fetchone()
        if not row:
            return jsonify({'error': '分享码不存在'}), 404
        if not is_admin and row[0] != username:
            return jsonify({'error': '无权撤销此分享码'}), 403
        conn.execute('UPDATE share_tokens SET revoked=1 WHERE token=?', (token,))
        conn.commit()

    return jsonify({'success': True})


@app.route('/api/admin/users', methods=['GET'])
def admin_list_users():
    """管理员获取所有用户及其缓存周次"""
    if 'username' not in session or not session.get('is_admin'):
        return jsonify({'error': '无权限'}), 403

    with _db() as conn:
        c = conn.cursor()
        c.execute('''SELECT u.username, u.jw_name, u.jw_class, u.last_active, u.last_login, u.group_id, g.name
                     FROM users u LEFT JOIN user_groups g ON u.group_id = g.id
                     ORDER BY u.last_active DESC, u.last_login DESC''')
        users = c.fetchall()
        result = []
        for u in users:
            c.execute('SELECT week FROM courses WHERE username=? ORDER BY week', (u[0],))
            cached_weeks = [r[0] for r in c.fetchall()]
            result.append({
                'username':     u[0],
                'name':         u[1] or u[0],
                'class_name':   u[2] or '',
                'last_active':  u[3] or u[4] or '',  # 优先使用 last_active
                'cached_weeks': cached_weeks,
                'group_id':     u[5],
                'group_name':   u[6] or '',
                'is_admin':     u[0] in ADMIN_USERS,  # 标记是否为管理员
            })
    return jsonify({'users': result})


# ── 用户组管理 ────────────────────────────────────────────────────────────────

@app.route('/api/admin/groups', methods=['GET'])
def admin_list_groups():
    """获取所有用户组"""
    if 'username' not in session or not session.get('is_admin'):
        return jsonify({'error': '无权限'}), 403

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT id, name, can_use_ics, can_create_share FROM user_groups ORDER BY id')
        groups = c.fetchall()
        result = [{
            'id': g[0],
            'name': g[1],
            'can_use_ics': bool(g[2]),
            'can_create_share': bool(g[3]),
        } for g in groups]
    return jsonify({'groups': result})


@app.route('/api/admin/groups', methods=['POST'])
@csrf_protected
def admin_create_group():
    """创建用户组"""
    if 'username' not in session or not session.get('is_admin'):
        return jsonify({'error': '无权限'}), 403

    data = request.get_json() or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': '组名不能为空'}), 400

    can_use_ics = 1 if data.get('can_use_ics', True) else 0
    can_create_share = 1 if data.get('can_create_share', True) else 0

    try:
        with _db() as conn:
            c = conn.cursor()
            c.execute('INSERT INTO user_groups (name, can_use_ics, can_create_share) VALUES (?, ?, ?)',
                      (name, can_use_ics, can_create_share))
            conn.commit()
            group_id = c.lastrowid
        return jsonify({'success': True, 'id': group_id, 'name': name,
                        'can_use_ics': bool(can_use_ics), 'can_create_share': bool(can_create_share)})
    except sqlite3.IntegrityError:
        return jsonify({'error': '组名已存在'}), 400


@app.route('/api/admin/groups/<int:group_id>', methods=['PUT'])
@csrf_protected
def admin_update_group(group_id):
    """修改用户组"""
    if 'username' not in session or not session.get('is_admin'):
        return jsonify({'error': '无权限'}), 403

    data = request.get_json() or {}
    updates = []
    params = []

    if 'name' in data:
        updates.append('name = ?')
        params.append(data['name'].strip())
    if 'can_use_ics' in data:
        updates.append('can_use_ics = ?')
        params.append(1 if data['can_use_ics'] else 0)
    if 'can_create_share' in data:
        updates.append('can_create_share = ?')
        params.append(1 if data['can_create_share'] else 0)

    if not updates:
        return jsonify({'error': '无更新内容'}), 400

    params.append(group_id)
    try:
        with _db() as conn:
            c = conn.cursor()
            c.execute(f'UPDATE user_groups SET {", ".join(updates)} WHERE id = ?', params)
            conn.commit()
            if c.rowcount == 0:
                return jsonify({'error': '用户组不存在'}), 404
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        return jsonify({'error': '组名已存在'}), 400


@app.route('/api/admin/groups/<int:group_id>', methods=['DELETE'])
@csrf_protected
def admin_delete_group(group_id):
    """删除用户组"""
    if 'username' not in session or not session.get('is_admin'):
        return jsonify({'error': '无权限'}), 403

    # 不允许删除默认组（ID 1或2）
    if group_id <= 2:
        return jsonify({'error': '不能删除系统默认组'}), 400

    with _db() as conn:
        c = conn.cursor()
        # 将该组的用户移至默认组
        default_id = _get_default_group_id()
        c.execute('UPDATE users SET group_id = ? WHERE group_id = ?', (default_id, group_id))
        c.execute('DELETE FROM user_groups WHERE id = ?', (group_id,))
        conn.commit()
        if c.rowcount == 0:
            return jsonify({'error': '用户组不存在'}), 404
    return jsonify({'success': True})


@app.route('/api/admin/users/<string:username>/group', methods=['PUT'])
@csrf_protected
def admin_set_user_group(username):
    """设置用户所属组"""
    if 'username' not in session or not session.get('is_admin'):
        return jsonify({'error': '无权限'}), 403

    data = request.get_json() or {}
    group_id = data.get('group_id')

    # 如果 group_id 为 None，则设置为默认组
    if group_id is None:
        group_id = _get_default_group_id()

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT id FROM user_groups WHERE id = ?', (group_id,))
        if not c.fetchone():
            return jsonify({'error': '用户组不存在'}), 404
        c.execute('UPDATE users SET group_id = ? WHERE username = ?', (group_id, username))
        conn.commit()
    return jsonify({'success': True})


@app.route('/api/admin/settings/default_group', methods=['GET', 'PUT'])
@csrf_protected
def admin_default_group():
    """获取或设置默认用户组"""
    if 'username' not in session or not session.get('is_admin'):
        return jsonify({'error': '无权限'}), 403

    if request.method == 'GET':
        default_id = _get_default_group_id()
        return jsonify({'default_group_id': default_id})

    data = request.get_json() or {}
    group_id = data.get('default_group_id')
    if group_id is None:
        return jsonify({'error': '缺少 default_group_id'}), 400

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT id FROM user_groups WHERE id = ?', (group_id,))
        if not c.fetchone():
            return jsonify({'error': '用户组不存在'}), 404
        c.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                  ('default_group_id', str(group_id)))
        conn.commit()
    return jsonify({'success': True})



@app.route('/api/admin/view/<string:target_user>/<int:week>', methods=['GET'])
def admin_view(target_user, week):
    """管理员查看指定用户的缓存课表（不触发实时抓取）"""
    if 'username' not in session or not session.get('is_admin'):
        return jsonify({'error': '无权限'}), 403

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT data, cached_at FROM courses WHERE username=? AND week=?',
                  (target_user, week))
        row = c.fetchone()

    if not row:
        return jsonify({'error': f'第 {week} 周暂无缓存数据'}), 404

    return jsonify({**json.loads(row[0]), 'from_cache': True, 'cache_time': row[1]})


@app.route('/api/admin/force_fetch', methods=['POST'])
@csrf_protected
def admin_force_fetch():
    """管理员触发立即为所有用户抓取课表（后台执行，失败保留缓存）"""
    if 'username' not in session or not session.get('is_admin'):
        return jsonify({'error': '无权限'}), 403

    def _run():
        with _db() as conn:
            c = conn.cursor()
            c.execute('SELECT username FROM users WHERE password_enc IS NOT NULL')
            users = [row[0] for row in c.fetchall()]

        ok_count = 0
        fail_count = 0
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
                            logger.warning('强制抓取失败 user=%s week=%d: %s', username, w, e)
                ok_count += 1
            except Exception as e:
                fail_count += 1
                logger.error('强制抓取失败 user=%s: %s', username, e)
        logger.info('强制抓取完成：成功 %d 人，失败 %d 人', ok_count, fail_count)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'success': True, 'message': '已在后台开始抓取，请稍后刷新查看'})


@app.route('/api/admin/restart', methods=['POST'])
@csrf_protected
def admin_restart():
    """管理员重启服务"""
    if 'username' not in session or not session.get('is_admin'):
        return jsonify({'error': '无权限'}), 403

    try:
        # 使用 systemctl restart 重启服务
        # 注意：需要配置 sudoers 允许 courseapp 用户无密码执行此命令
        result = subprocess.run(
            ['sudo', 'systemctl', 'restart', 'course-schedule'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            logger.error('重启服务失败: %s', result.stderr)
            return jsonify({'success': False, 'message': f'重启失败: {result.stderr}'}), 500

        # 如果能执行到这里，说明重启命令已发送（但服务可能已在重启中）
        return jsonify({'success': True, 'message': '重启命令已发送，服务将重新启动'})
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'message': '重启命令超时'}), 500
    except Exception as e:
        logger.error('重启服务异常: %s', e)
        return jsonify({'success': False, 'message': f'重启异常: {e}'}), 500


# ── ICS 日历订阅 ────────────────────────────────────────────────────────────────

def _get_user_group(username: str) -> dict | None:
    """获取用户所属的用户组信息"""
    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT group_id FROM users WHERE username=?', (username,))
        row = c.fetchone()
        if not row or not row[0]:
            return None
        c.execute('SELECT id, name, can_use_ics, can_create_share FROM user_groups WHERE id=?', (row[0],))
        group = c.fetchone()
        if not group:
            return None
        return {
            'id': group[0],
            'name': group[1],
            'can_use_ics': bool(group[2]),
            'can_create_share': bool(group[3]),
        }

def _get_default_group_id() -> int:
    """获取默认用户组ID"""
    # 优先从设置中获取
    default_id = get_setting('default_group_id', None)
    if default_id:
        try:
            return int(default_id)
        except (ValueError, TypeError):
            pass
    # 否则返回第一个组
    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT id FROM user_groups ORDER BY id LIMIT 1')
        row = c.fetchone()
        return row[0] if row else 1

def _can_use_ics(username: str, is_admin: bool) -> bool:
    """检查用户是否可以使用ICS订阅"""
    if is_admin:
        return True
    # 先检查用户所属组
    group = _get_user_group(username)
    if group:
        return group['can_use_ics']
    # 未分组的用户使用默认组权限
    default_group_id = _get_default_group_id()
    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT can_use_ics FROM user_groups WHERE id = ?', (default_group_id,))
        row = c.fetchone()
        return bool(row[0]) if row else False

def _can_create_share(username: str, is_admin: bool) -> bool:
    """检查用户是否可以创建分享码"""
    if is_admin:
        return True
    # 先检查用户所属组
    group = _get_user_group(username)
    if group:
        return group['can_create_share']
    # 未分组的用户使用默认组权限
    default_group_id = _get_default_group_id()
    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT can_create_share FROM user_groups WHERE id = ?', (default_group_id,))
        row = c.fetchone()
        return bool(row[0]) if row else False


def _get_slot_time_range(start_slot: int, duration: int, location: str = '') -> tuple:
    """根据节次和上课地点获取时间范围 (start_time, end_time)

    地点参数用于支持部分教学楼第3-4节的特殊时间（由管理员配置）。
    """
    # 标准节次时间（与前端 getCourseTime 保持一致）
    slot_starts = {
        1: '08:30', 2: '09:20', 3: '10:25', 4: '11:15',
        5: '14:30', 6: '15:25', 7: '16:20', 8: '17:15',
        9: '19:00', 10: '19:55',
    }
    slot_ends = {
        1: '09:15', 2: '10:05', 3: '11:10', 4: '12:00',
        5: '15:15', 6: '16:10', 7: '17:05', 8: '18:00',
        9: '19:45', 10: '20:40',
    }
    # 特殊教学楼第3-4节时间（翔安校区等）
    slot_starts_sp = {3: '10:15', 4: '11:05'}
    slot_ends_sp   = {3: '11:00', 4: '11:50'}

    # 读取管理员配置的特殊教学楼模式
    try:
        pattern = get_setting('slot34_special_pattern', '')
        special = bool(pattern) and any(p.strip() in location for p in pattern.split(',') if p.strip())
    except Exception:
        special = False

    starts = {**slot_starts, **slot_starts_sp} if special else slot_starts
    ends   = {**slot_ends,   **slot_ends_sp}   if special else slot_ends

    if start_slot not in slot_starts:
        return ('08:00', '09:00')
    start_time = starts.get(start_slot, '08:00')
    end_slot   = start_slot + duration - 1
    end_time   = ends.get(end_slot, ends.get(start_slot, '09:00'))
    return (start_time, end_time)


def _parse_weeks(weeks_str: str) -> list:
    """解析周次字符串，返回周次列表

    支持格式：
    - "1,3,5"       -> [1, 3, 5]
    - "1-10"        -> [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    - "1,3,5,7,9"   -> [1, 3, 5, 7, 9]
    - "1-5,7,9"     -> [1, 2, 3, 4, 5, 7, 9]
    """
    if not weeks_str:
        return []
    weeks = set()
    for part in weeks_str.replace('，', ',').split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            try:
                start, end = part.split('-', 1)
                weeks.update(range(int(start), int(end) + 1))
            except (ValueError, TypeError):
                continue
        else:
            try:
                weeks.add(int(part))
            except ValueError:
                continue
    return sorted(weeks)


def generate_ics(username: str) -> str:
    """生成ICS格式的日历数据

    每节课单独生成一个VEVENT，不使用RRULE重复规则。
    直接使用数据库中已有的日期信息，不进行估算。
    """
    with _db() as conn:
        c = conn.cursor()
        # 按周次排序读取所有缓存数据
        c.execute('SELECT week, data FROM courses WHERE username=? ORDER BY week', (username,))
        rows = c.fetchall()

    if not rows:
        return _generate_empty_ics()

    # 收集所有课程实例（每节课一个事件）
    events = []

    for week, data_json in rows:
        try:
            data = json.loads(data_json)

            for day_data in data.get('完整课表', []):
                date_str = day_data.get('date', '')
                if not date_str:
                    continue

                # 验证日期格式
                try:
                    datetime.strptime(date_str[:10], '%Y-%m-%d')
                except ValueError:
                    logger.warning('日期格式无效，跳过: %s', date_str)
                    continue

                for course in day_data.get('courses', []):
                    # 获取上课地点（用于特殊节次时间判断）
                    location = course.get('location', '')
                    # 根据节次和地点计算时间
                    start_slot = course.get('time_slots', {}).get('start_slot', 1)
                    duration = course.get('time_slots', {}).get('duration', 1)
                    start_time_str, end_time_str = _get_slot_time_range(start_slot, duration, location)

                    # 构建完整的DTSTART和DTEND
                    dtstart = f'{date_str[:10].replace("-", "")}T{start_time_str.replace(":", "")}00'
                    dtend = f'{date_str[:10].replace("-", "")}T{end_time_str.replace(":", "")}00'

                    # 获取周次信息
                    weeks_str = course.get('weeks', '')
                    week_display = f'第{week}周' if weeks_str else f'第{week}周'
                    if weeks_str:
                        week_display = f'第{week}周({weeks_str})'

                    # 构建描述信息
                    teacher = course.get('teacher', '')
                    description_parts = []
                    if teacher:
                        description_parts.append(f'教师: {teacher}')
                    if weeks_str:
                        description_parts.append(f'周次: {weeks_str}')
                    if location:
                        description_parts.append(f'地点: {location}')
                    description = ' | '.join(description_parts) if description_parts else ''

                    # 唯一标识：课程名_日期_节次
                    uid = f'{username}-{date_str}-{start_slot}@{course.get("course_name", "course")}'

                    events.append({
                        'uid': uid,
                        'summary': course.get('course_name', '未知课程'),
                        'description': description,
                        'location': location,
                        'dtstart': dtstart,
                        'dtend': dtend,
                        'week_display': week_display,
                    })

        except json.JSONDecodeError as e:
            logger.warning('JSON解析失败 week=%s: %s', week, e)
            continue
        except Exception as e:
            logger.warning('处理课表数据异常 week=%s: %s', week, e)
            continue

    # 生成ICS文件
    if not events:
        return _generate_empty_ics()

    # 按日期和时间排序
    events.sort(key=lambda x: (x['dtstart'], x['uid']))

    ics_lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//Course Schedule//CN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'X-WR-CALNAME:课程表',
        'X-WR-TIMEZONE:Asia/Shanghai',
    ]

    for event in events:
        ics_lines.extend([
            'BEGIN:VEVENT',
            f'UID:{event["uid"]}',
            f'SUMMARY:{event["summary"]}',
            f'DTSTART;TZID=Asia/Shanghai:{event["dtstart"]}',
            f'DTEND;TZID=Asia/Shanghai:{event["dtend"]}',
        ])
        if event['description']:
            ics_lines.append(f'DESCRIPTION:{event["description"]}')
        if event['location']:
            ics_lines.append(f'LOCATION:{event["location"]}')
        # 添加创建时间（ICS规范推荐）
        ics_lines.append(f'DTSTAMP:{datetime.now().strftime("%Y%m%dT%H%M%S")}')
        ics_lines.append('END:VEVENT')

    ics_lines.append('END:VCALENDAR')

    return '\r\n'.join(ics_lines)


def _generate_empty_ics() -> str:
    """生成空的ICS文件"""
    return '\r\n'.join([
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//Course Schedule//CN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'X-WR-CALNAME:课程表',
        'X-WR-TIMEZONE:Asia/Shanghai',
        'END:VCALENDAR',
    ])


@app.route('/api/ics/create', methods=['POST'])
@csrf_protected
def ics_create():
    """创建ICS订阅"""
    if 'username' not in session:
        return jsonify({'error': '未登录'}), 401

    username = session['username']
    is_admin = session.get('is_admin', False)

    if not _can_use_ics(username, is_admin):
        return jsonify({'error': '管理员未开放日历订阅功能'}), 403

    # 检查是否已有有效订阅
    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT token FROM ics_tokens WHERE username=? AND revoked=0', (username,))
        row = c.fetchone()
        if row:
            return jsonify({'success': True, 'token': row[0], 'url': f'/calendar/{row[0]}.ics'})

    # 全量同步所有周的课表（后台执行，不阻塞返回）
    threading.Thread(
        target=_sync_all_weeks_for_ics,
        args=(username,),
        daemon=True
    ).start()

    # 生成新token
    for attempt in range(5):
        token = secrets.token_hex(8).upper()
        try:
            with _db() as conn:
                conn.execute(
                    'INSERT INTO ics_tokens (token, username, created_at, revoked) VALUES (?, ?, ?, 0)',
                    (token, username, datetime.now().isoformat())
                )
                conn.commit()
            return jsonify({'success': True, 'token': token, 'url': f'/calendar/{token}.ics', 'syncing': True, 'message': '正在后台同步课表数据'})
        except sqlite3.IntegrityError:
            if attempt == 4:
                logger.error('ICS token生成失败：5次尝试均冲突 user=%s', username)
                return jsonify({'error': '生成订阅失败，请重试'}), 500
            continue


def _sync_all_weeks_for_ics(username: str):
    """全量同步用户所有周的课表数据，用于ICS日历生成"""
    try:
        # 获取已缓存的周次列表
        with _db() as conn:
            c = conn.cursor()
            c.execute('SELECT week, data FROM courses WHERE username=? ORDER BY week', (username,))
            cached = {row[0]: json.loads(row[1]) for row in c.fetchall()}

        if not cached:
            # 没有任何缓存，先抓取当前周获取max_week
            try:
                data = fetch_from_jw(username, 0)
                max_week = data['metadata'].get('max_week', 20)
                current_week = data['metadata'].get('current_week', 1)
                with _db() as conn:
                    conn.execute('INSERT OR REPLACE INTO courses VALUES (?, ?, ?, ?)',
                                 (username, current_week,
                                  json.dumps(data, ensure_ascii=False),
                                  datetime.now().isoformat()))
                    conn.commit()
                cached[current_week] = data
            except Exception as e:
                logger.error('ICS全量同步失败，无法获取初始数据 user=%s: %s', username, e)
                return

        # 从缓存数据中获取max_week
        max_week = max(
            (d.get('metadata', {}).get('max_week', 20) for d in cached.values()),
            default=20
        )

        # 找出缺失的周次
        cached_weeks = set(cached.keys())
        missing_weeks = set(range(1, max_week + 1)) - cached_weeks

        if not missing_weeks:
            logger.info('ICS全量同步完成，数据已完整 user=%s weeks=%s', username, sorted(cached_weeks))
            return

        # 逐周抓取缺失的数据
        for week in sorted(missing_weeks):
            try:
                data = fetch_from_jw(username, week)
                with _db() as conn:
                    conn.execute('INSERT OR REPLACE INTO courses VALUES (?, ?, ?, ?)',
                                 (username, week,
                                  json.dumps(data, ensure_ascii=False),
                                  datetime.now().isoformat()))
                    conn.commit()
                logger.info('ICS全量同步已抓取 user=%s week=%d', username, week)
            except Exception as e:
                logger.warning('ICS全量同步抓取失败 user=%s week=%d: %s', username, week, e)
                # 继续抓取其他周

        logger.info('ICS全量同步完成 user=%s total=%d weeks', username, len(cached) + len(missing_weeks))

    except Exception as e:
        logger.error('ICS全量同步异常 user=%s: %s', username, e)


@app.route('/api/ics/status', methods=['GET'])
def ics_status():
    """获取ICS订阅状态"""
    if 'username' not in session:
        return jsonify({'error': '未登录'}), 401

    username = session['username']
    is_admin = session.get('is_admin', False)

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT token, created_at FROM ics_tokens WHERE username=? AND revoked=0', (username,))
        row = c.fetchone()

    return jsonify({
        'enabled': _can_use_ics(username, is_admin),
        'has_subscription': row is not None,
        'token': row[0] if row else None,
        'url': f'/calendar/{row[0]}.ics' if row else None,
        'created_at': row[1] if row else None,
    })


@app.route('/api/ics/revoke', methods=['POST'])
@csrf_protected
def ics_revoke():
    """撤销ICS订阅"""
    if 'username' not in session:
        return jsonify({'error': '未登录'}), 401

    username = session['username']

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT token FROM ics_tokens WHERE username=? AND revoked=0', (username,))
        row = c.fetchone()
        if not row:
            return jsonify({'error': '没有有效的订阅'}), 404

        c.execute('UPDATE ics_tokens SET revoked=1 WHERE token=?', (row[0],))
        conn.commit()

    return jsonify({'success': True})


@app.route('/calendar/<token>.ics')
def ics_export(token):
    """导出ICS文件"""
    token = token.upper()

    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT username, revoked FROM ics_tokens WHERE token=?', (token,))
        row = c.fetchone()

    if not row or row[1]:
        return '订阅已失效或不存在', 404

    username = row[0]

    # 生成ICS内容
    try:
        ics_content = generate_ics(username)
        return ics_content, 200, {
            'Content-Type': 'text/calendar; charset=utf-8',
            'Content-Disposition': f'attachment; filename="calendar_{username}.ics"',
        }
    except Exception as e:
        logger.error('生成ICS文件失败 token=%s: %s', token, e)
        return '生成日历文件失败', 500


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    port  = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=debug)
