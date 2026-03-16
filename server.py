import os
import json
import logging
import secrets
import sqlite3
import threading
import time as time_module
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS

import jw_client

# ── 全局锁 ────────────────────────────────────────────────────────────────────
_token_refresh_locks = {}  # username -> Lock
_token_locks_lock = threading.Lock()

def _get_user_lock(username: str) -> threading.Lock:
    """获取用户专属的 token 刷新锁"""
    with _token_locks_lock:
        if username not in _token_refresh_locks:
            _token_refresh_locks[username] = threading.Lock()
        return _token_refresh_locks[username]

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
    SESSION_COOKIE_PERMANENT=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # 限制请求大小为 16MB
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
        for col_def in [
            'jw_token TEXT', 'token_time TEXT', 'jw_name TEXT',
            'jw_class TEXT', 'jw_kbjcmsid TEXT', 'password_enc TEXT',
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


# ── 后台定时抓取 ──────────────────────────────────────────────────────────────
# 用户失败计数，连续失败 3 次后跳过
_user_fail_counts = defaultdict(int)

def background_fetch():
    while True:
        try:
            with _db() as conn:
                c = conn.cursor()
                c.execute('SELECT username FROM users WHERE password_enc IS NOT NULL')
                users = [row[0] for row in c.fetchall()]

            for username in users:
                # 连续失败 3 次后跳过该用户
                if _user_fail_counts[username] >= 3:
                    logger.info('跳过连续失败用户 user=%s', username)
                    continue

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

                    # 成功后重置失败计数
                    _user_fail_counts[username] = 0

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

    session.permanent = True
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
    with _db() as conn:
        c = conn.cursor()
        c.execute('SELECT jw_name FROM users WHERE username=?', (username,))
        row = c.fetchone()
    name = (row[0] or username) if row else username
    return jsonify({
        'logged_in':              True,
        'username':               username,
        'name':                   name,
        'is_admin':               session.get('is_admin', False),
        'slot34_special_pattern': slot34_pattern,
    })


def _async_refresh_course(username: str, week: int):
    """后台异步刷新课表数据"""
    try:
        course_data = fetch_from_jw(username, week)
        actual_week = course_data['metadata']['current_week'] if week == 0 else week

        with _db() as conn:
            conn.execute('INSERT OR REPLACE INTO courses VALUES (?, ?, ?, ?)',
                         (username, actual_week,
                          json.dumps(course_data, ensure_ascii=False),
                          datetime.now().isoformat()))
            conn.commit()
        logger.info('后台刷新成功 user=%s week=%s', username, week)
    except Exception as e:
        logger.info('后台刷新失败 user=%s week=%s: %s', username, week, e)


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

    # ── 读取缓存 ──────────────────────────────────────────────────────────────────
    force = request.args.get('force') == '1'
    cache_row = None
    actual_week_for_zero = week

    with _db() as conn:
        c = conn.cursor()
        if week == 0:
            # 先从最近一次缓存中读取 current_week，再精确取那一周的缓存
            c.execute('SELECT data FROM courses WHERE username=? '
                      'ORDER BY cached_at DESC LIMIT 1', (username,))
            row = c.fetchone()
            if row:
                cur_w = json.loads(row[0]).get('metadata', {}).get('current_week')
                if cur_w:
                    actual_week_for_zero = cur_w
                    c.execute('SELECT data, cached_at FROM courses WHERE username=? AND week=?',
                              (username, cur_w))
                else:
                    c.execute('SELECT data, cached_at FROM courses WHERE username=? '
                              'ORDER BY cached_at DESC LIMIT 1', (username,))
                cache_row = c.fetchone()
        else:
            c.execute('SELECT data, cached_at FROM courses WHERE username=? AND week=?',
                      (username, week))
            cache_row = c.fetchone()

    # ── 策略：有缓存就先返回，同时后台异步刷新 ──────────────────────────────────
    if cache_row and not force:
        # 检查缓存是否过期
        should_refresh = False
        try:
            age = (datetime.now() - datetime.fromisoformat(cache_row[1])).total_seconds()
            # 缓存超过抓取间隔的一半就触发后台刷新（fetch_interval单位是分钟，需转换成秒）
            threshold = get_setting('fetch_interval', 60) * 60 / 2
            if age > threshold:
                should_refresh = True
                logger.info('缓存过期触发刷新 user=%s week=%s age=%.1fs threshold=%.1fs',
                           username, actual_week_for_zero if week == 0 else week, age, threshold)
        except (ValueError, TypeError):
            should_refresh = True

        # 触发后台异步刷新（不阻塞用户请求）
        if should_refresh and not share_mode:
            threading.Thread(
                target=_async_refresh_course,
                args=(username, actual_week_for_zero if week == 0 else week),
                daemon=True
            ).start()

        # 立即返回缓存数据
        return jsonify({
            **json.loads(cache_row[0]),
            'from_cache': True,
            'cache_time': cache_row[1],
            'refreshing': should_refresh,
        })

    # ── 无缓存或强制刷新：同步获取 ──────────────────────────────────────────────
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
        # 即使是旧缓存也返回，避免完全无法使用
        if cache_row:
            return jsonify({
                **json.loads(cache_row[0]),
                'from_cache': True,
                'fetch_failed': True,
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
def share_create():
    """创建分享码（仅限已登录的正式用户）"""
    if 'username' not in session:
        return jsonify({'error': '未登录'}), 401

    data     = request.json or {}
    username = session['username']

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
        c.execute('SELECT username, jw_name, jw_class, last_login FROM users ORDER BY last_login DESC')
        users = c.fetchall()
        result = []
        for u in users:
            c.execute('SELECT week FROM courses WHERE username=? ORDER BY week', (u[0],))
            cached_weeks = [r[0] for r in c.fetchall()]
            result.append({
                'username':     u[0],
                'name':         u[1] or u[0],
                'class_name':   u[2] or '',
                'last_login':   u[3] or '',
                'cached_weeks': cached_weeks,
            })
    return jsonify({'users': result})


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


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    port  = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=debug)
