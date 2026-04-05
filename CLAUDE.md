# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

课程表 (Course Schedule) is a web application that syncs with an educational administration system (翔安教务系统). It supports multi-user course schedules, share codes for read-only access, ICS calendar subscription, and import/export functionality.

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Set required environment variables
export STORAGE_AES_KEY=$(python -c "import secrets; print(secrets.token_hex(16))")
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")

# Run development server
python server.py
# or with Flask debug mode:
FLASK_DEBUG=true python server.py

# Run with Gunicorn (production)
gunicorn --bind 0.0.0.0:5000 --workers 1 --timeout 60 server:app

# Docker deployment
docker-compose up --build
```

## Architecture

### Backend (server.py)
- **Flask** application with SQLite database (WAL mode)
- Background daemon thread (`background_fetch`) that periodically fetches course data for all users
- User-level locks (`_token_refresh_locks`) to prevent concurrent token refresh
- Rate limiting on login endpoints (5 attempts/minute/IP)

### Database Tables
- `users`: username, encrypted password, JW token, user info, group_id
- `courses`: cached course data by (username, week)
- `settings`: key-value settings (fetch_interval, slot34_special_pattern, etc.)
- `share_tokens`: share codes with week range and expiration
- `ics_tokens`: ICS calendar subscription tokens
- `user_groups`: permission groups (can_use_ics, can_create_share)

### API Client (jw_client.py)
- Handles authentication with the educational system
- AES-ECB encryption for password transmission to JW system
- AES-GCM encryption for local password storage
- Token management (~4 hour validity)
- Course data transformation from raw API to frontend format

### Frontend
- Single-page HTML files: `index.html` (main), `login.html`, `admin.html`
- No build step required - vanilla JS with service worker (`sw.js`) for offline caching
- Dark mode via CSS `prefers-color-scheme`

## Key API Routes

| Route | Description |
|-------|-------------|
| `POST /api/login` | Login with username/password |
| `POST /api/logout` | Clear session |
| `GET /api/user` | Get current user info |
| `GET /api/courses/<week>` | Get courses (0 = current week) |
| `GET/POST /api/settings` | Get/update settings |
| `POST /api/share/create` | Create share code |
| `GET /api/share/verify` | Verify share code |
| `POST /api/share/enter` | Enter share mode |
| `GET /calendar/<token>.ics` | ICS calendar export |
| `GET /api/admin/users` | Admin: list all users |
| `POST /api/admin/force_fetch` | Admin: trigger background fetch |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `STORAGE_AES_KEY` | Yes | 16-byte hex key for local password encryption |
| `SECRET_KEY` | No | Flask session key (auto-generated if missing) |
| `JW_API_KEY` | No | AES key for JW system API (default: built-in) |
| `ADMIN_USERS` | No | Comma-separated admin usernames |
| `PORT` | No | Server port (default: 5000) |
| `FLASK_DEBUG` | No | Enable debug mode |
| `DB_FILE` | No | SQLite database path |

## Deployment

### Production (Debian/Ubuntu)
```bash
# Automated deployment
bash <(curl -fsSL https://raw.githubusercontent.com/yll682/course-schedule/master/deploy.sh)
```

Creates:
- System user `courseapp`
- Systemd service at `/etc/systemd/system/course-schedule.service`
- Application at `/opt/course-schedule/`
- Data directory at `/opt/course-schedule/data/`

### Docker Deployment
```bash
# Quick deployment
chmod +x docker-deploy.sh
./docker-deploy.sh

# Manual deployment
cp .env.example .env
# Edit .env and set STORAGE_AES_KEY
docker-compose up -d --build
```

Docker-specific notes:
- Data persists in Docker volume `course-data`
- Database stored at `/app/data/courses.db` inside container
- Health check configured for automatic recovery
- See DOCKER.md for detailed documentation

### Important Notes

1. **Single Worker**: Gunicorn must run with `--workers 1` to ensure the background fetch thread is unique

2. **Token Locking**: When modifying `_ensure_token()` or related functions, maintain the user-level locking pattern to prevent race conditions

3. **Database Migrations**: The `init_db()` function handles schema migrations via `ALTER TABLE` with error suppression for existing columns

4. **Security Headers**: Static file routes block `.py`, `.db`, `.env` extensions via `_BLOCKED` and `_BLOCKED_EXTS`

5. **Share Mode**: Sessions with `share_token` have restricted week access - always check `week_min`/`week_max` bounds
