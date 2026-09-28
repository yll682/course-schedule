import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

project_dir = Path(__file__).resolve().parent.parent
database_file = project_dir / 'tests' / 'verify-grades.db'
if database_file.exists():
    database_file.unlink()

with sqlite3.connect(database_file) as conn:
    conn.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute(
        '''CREATE TABLE grades
           (username TEXT, semester TEXT, data TEXT NOT NULL, cached_at TEXT NOT NULL,
            query_mode TEXT NOT NULL DEFAULT 'best',
            PRIMARY KEY (username, semester))'''
    )
    conn.execute(
        'INSERT INTO settings (key, value) VALUES (?, ?)',
        ('grade_cache_version', 'best-only-v1'),
    )
    conn.execute(
        '''INSERT INTO grades
           (username, semester, data, cached_at, query_mode)
           VALUES (?, ?, ?, ?, ?)''',
        ('migration-user', '2024-2025-1', '{}', datetime.now().isoformat(), 'best'),
    )

os.environ['STORAGE_AES_KEY'] = '0123456789abcdef0123456789abcdef'
os.environ['SECRET_KEY'] = 'grade-feature-verification'
os.environ['DB_FILE'] = str(database_file)
os.environ['ALLOW_HTTP'] = 'true'
sys.path.insert(0, str(project_dir))

import server

semester = '2024-2025-1'
uncached_semester = '2023-2024-2'
cached_at = datetime.now().isoformat()
semesters = {
    'current_semester': uncached_semester,
    'semesters': [
        {'semesterId': semester, 'semesterName': '2024-2025学年第1学期'},
        {'semesterId': uncached_semester, 'semesterName': '2023-2024学年第2学期'},
    ],
}
grades = {
    'name': '测试用户',
    'studentID': 'grade-test-user',
    'achievement': [
        {'cj0708id': '1', 'courseName': '实用药学基础', 'kcbh': 'K001', 'fraction': '50'},
        {'cj0708id': '2', 'courseName': '实用药学基础', 'kcbh': 'K001', 'fraction': '不及格'},
        {'cj0708id': '3', 'courseName': '实用药学基础', 'kcbh': 'K001', 'fraction': '61'},
    ],
}

with server._db() as conn:
    cache_version = conn.execute(
        'SELECT value FROM settings WHERE key=?',
        (server.GRADE_CACHE_VERSION_SETTING,),
    ).fetchone()
    migrated_grade = conn.execute(
        'SELECT 1 FROM grades WHERE username=?',
        ('migration-user',),
    ).fetchone()
    conn.execute(
        'INSERT OR REPLACE INTO users (username, jw_name, jw_class, last_login, group_id) VALUES (?, ?, ?, ?, ?)',
        ('grade-test-user', '测试用户', '测试班级', cached_at, 1),
    )
    conn.execute(
        'INSERT OR REPLACE INTO grade_semesters VALUES (?, ?, ?)',
        ('grade-test-user', json.dumps(semesters, ensure_ascii=False), cached_at),
    )
    conn.execute(
        '''INSERT OR REPLACE INTO grades
           (username, semester, data, cached_at)
           VALUES (?, ?, ?, ?)''',
        ('grade-test-user', semester, json.dumps(grades, ensure_ascii=False), cached_at),
    )
    conn.commit()

assert cache_version == (server.GRADE_CACHE_VERSION,)
assert migrated_grade is None

job_key = ('grade-test-user', 'deduplication-check')
refresh_job, claimed = server._claim_grade_refresh(job_key)
same_job, duplicate_claimed = server._claim_grade_refresh(job_key)
assert claimed is True
assert duplicate_claimed is False
assert same_job is refresh_job
assert server._execute_grade_refresh(job_key, refresh_job, lambda: 'completed') == 'completed'

client = server.app.test_client()
with client.session_transaction() as user_session:
    user_session['username'] = 'grade-test-user'

semester_response = client.get('/api/grades/semesters')
assert semester_response.status_code == 200
assert semester_response.json['from_cache'] is True

grade_response = client.get(f'/api/grades?semester={semester}&force=1')
assert grade_response.status_code == 200
assert grade_response.json['record_scope'] == 'all'
assert len(grade_response.json['data']['achievement']) == 3

refresh_without_csrf = client.post('/api/grades/refresh', json={'semester': semester})
assert refresh_without_csrf.status_code == 403

csrf_response = client.get('/api/csrf-token')
csrf_token = csrf_response.json['csrf_token']
invalid_refresh = client.post(
    '/api/grades/refresh',
    json={'semester': 'invalid'},
    headers={'X-CSRF-Token': csrf_token},
)
assert invalid_refresh.status_code == 400

admin_client = server.app.test_client()
with admin_client.session_transaction() as admin_session:
    admin_session['username'] = server.ADMIN_USERS[0]

admin_semester_response = admin_client.get('/api/admin/grades/grade-test-user/semesters')
assert admin_semester_response.status_code == 200
assert admin_semester_response.json['current_semester'] == semester
assert [item['semesterId'] for item in admin_semester_response.json['semesters']] == [semester]

admin_grade_response = admin_client.get(f'/api/admin/grades/grade-test-user?semester={semester}')
assert admin_grade_response.status_code == 200
assert admin_grade_response.json['record_scope'] == 'all'
assert len(admin_grade_response.json['data']['achievement']) == 3

share_client = server.app.test_client()
with share_client.session_transaction() as share_session:
    share_session['share_token'] = 'SHARE123'

assert share_client.get('/api/grades/semesters').status_code == 403

print('grade integration checks passed')
