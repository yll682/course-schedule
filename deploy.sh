#!/usr/bin/env bash
# =============================================================
# 课程表 · 一键部署脚本 (Debian / Ubuntu)
# 用法：
#   首次部署：bash deploy.sh
#   后续更新：bash deploy.sh update
#   只配 Nginx/HTTPS：bash deploy.sh nginx
# =============================================================
set -euo pipefail

# ── 配置项（按需修改）─────────────────────────────────────────
APP_USER="courseapp"
APP_DIR="/opt/course-schedule"
REPO_URL="https://github.com/yll682/course-schedule.git"
SERVICE_NAME="course-schedule"
PORT="5000"
# Nginx + HTTPS 配置（留空则跳过）
DOMAIN=""            # 例如 schedule.example.com，留空跳过 Nginx 配置
ENABLE_HTTPS="yes"   # 有 DOMAIN 时是否自动申请 Let's Encrypt 证书
ACME_EMAIL=""        # certbot 邮箱，留空则用 --register-unsafely-without-email
# =============================================================

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${CYAN}[info]${NC} $*"; }
ok()      { echo -e "${GREEN}[ ok ]${NC} $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC} $*"; }
die()     { echo -e "${RED}[err ]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && die "请以 root 运行：sudo bash deploy.sh"
MODE="${1:-install}"

# ─────────────────────────────────────────────────────────────
# 1. 系统依赖
# ─────────────────────────────────────────────────────────────
install_deps() {
    info "更新软件包列表..."
    apt-get update -qq
    info "安装系统依赖..."
    apt-get install -y -qq git python3 python3-pip python3-venv curl
    if [[ -n "$DOMAIN" ]]; then
        apt-get install -y -qq nginx certbot python3-certbot-nginx
    fi
    ok "依赖安装完成"
}

# ─────────────────────────────────────────────────────────────
# 2. 创建系统用户
# ─────────────────────────────────────────────────────────────
create_user() {
    if id "$APP_USER" &>/dev/null; then
        info "用户 $APP_USER 已存在，跳过"
    else
        info "创建系统用户 $APP_USER..."
        useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
        ok "用户 $APP_USER 创建完成"
    fi
}

# ─────────────────────────────────────────────────────────────
# 3. 克隆或更新代码
# ─────────────────────────────────────────────────────────────
setup_code() {
    if [[ -d "$APP_DIR/.git" ]]; then
        info "拉取最新代码..."
        git -C "$APP_DIR" pull --ff-only
    else
        info "克隆仓库..."
        git clone "$REPO_URL" "$APP_DIR"
    fi
    ok "代码就绪：$APP_DIR"
}

# ─────────────────────────────────────────────────────────────
# 4. Python 虚拟环境 + 依赖
# ─────────────────────────────────────────────────────────────
setup_venv() {
    info "配置 Python 虚拟环境..."
    python3 -m venv "$APP_DIR/.venv"
    "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
    "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
    ok "Python 依赖安装完成"
}

# ─────────────────────────────────────────────────────────────
# 5. 创建 .env（首次自动生成 SECRET_KEY）
# ─────────────────────────────────────────────────────────────
setup_env() {
    ENV_FILE="$APP_DIR/.env"
    if [[ -f "$ENV_FILE" ]]; then
        info ".env 已存在，跳过生成"
    else
        info "生成 .env 文件..."
        SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        cat > "$ENV_FILE" <<EOF
# 自动生成于 $(date '+%Y-%m-%d %H:%M:%S')
SECRET_KEY=${SECRET_KEY}
PORT=${PORT}
FLASK_DEBUG=false
DB_FILE=${APP_DIR}/courses.db
EOF
        chmod 600 "$ENV_FILE"
        ok ".env 已生成：$ENV_FILE"
        warn "请检查 .env 并根据需要调整配置"
    fi
}

# ─────────────────────────────────────────────────────────────
# 6. systemd 服务
# ─────────────────────────────────────────────────────────────
setup_systemd() {
    info "配置 systemd 服务..."
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=课程表 Web 应用
After=network.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/gunicorn \\
    --workers 1 \\
    --bind 127.0.0.1:${PORT} \\
    --timeout 60 \\
    --access-logfile - \\
    --error-logfile - \\
    server:app
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
    chmod 755 "$APP_DIR"

    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}.service"
    systemctl restart "${SERVICE_NAME}.service"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        ok "服务已启动并设为开机自启"
    else
        die "服务启动失败，查看日志：journalctl -u $SERVICE_NAME -n 30"
    fi
}

# ─────────────────────────────────────────────────────────────
# 7. Nginx 反代
# ─────────────────────────────────────────────────────────────
setup_nginx() {
    [[ -z "$DOMAIN" ]] && { info "未设置 DOMAIN，跳过 Nginx 配置"; return; }
    info "配置 Nginx..."
    NGINX_CONF="/etc/nginx/sites-available/${SERVICE_NAME}"
    cat > "$NGINX_CONF" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    # 上传限制（导入课表文件）
    client_max_body_size 10m;

    location / {
        proxy_pass         http://127.0.0.1:${PORT};
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
}
EOF
    ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/
    rm -f /etc/nginx/sites-enabled/default
    nginx -t && systemctl reload nginx
    ok "Nginx 配置完成：http://${DOMAIN}"

    if [[ "$ENABLE_HTTPS" == "yes" ]]; then
        info "申请 Let's Encrypt 证书..."
        CERTBOT_FLAGS="--nginx -d ${DOMAIN} --non-interactive --agree-tos --redirect"
        if [[ -n "$ACME_EMAIL" ]]; then
            CERTBOT_FLAGS="$CERTBOT_FLAGS --email ${ACME_EMAIL}"
        else
            CERTBOT_FLAGS="$CERTBOT_FLAGS --register-unsafely-without-email"
        fi
        # shellcheck disable=SC2086
        certbot $CERTBOT_FLAGS && ok "HTTPS 配置完成：https://${DOMAIN}" \
            || warn "证书申请失败（DNS 未生效？），稍后手动运行：certbot --nginx -d ${DOMAIN}"
    fi
}

# ─────────────────────────────────────────────────────────────
# 8. 自动续签证书（仅首次安装时写入 cron）
# ─────────────────────────────────────────────────────────────
setup_cert_renewal() {
    [[ -z "$DOMAIN" || "$ENABLE_HTTPS" != "yes" ]] && return
    CRON_LINE="0 3 * * * certbot renew --quiet --post-hook 'systemctl reload nginx'"
    if ! crontab -l 2>/dev/null | grep -qF "certbot renew"; then
        (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
        ok "已添加证书自动续签 cron 任务（每天凌晨 3 点）"
    fi
}

# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
case "$MODE" in
    install)
        install_deps
        create_user
        setup_code
        setup_venv
        setup_env
        setup_systemd
        setup_nginx
        setup_cert_renewal

        echo ""
        echo -e "${GREEN}══════════════════════════════════════════${NC}"
        echo -e "${GREEN}  部署完成！${NC}"
        if [[ -n "$DOMAIN" ]]; then
            echo -e "  访问地址：${CYAN}https://${DOMAIN}${NC}"
        else
            echo -e "  访问地址：${CYAN}http://<服务器IP>:${PORT}${NC}"
        fi
        echo -e "  查看日志：${YELLOW}journalctl -u ${SERVICE_NAME} -f${NC}"
        echo -e "  重启服务：${YELLOW}systemctl restart ${SERVICE_NAME}${NC}"
        echo -e "${GREEN}══════════════════════════════════════════${NC}"
        ;;
    update)
        info "更新模式..."
        setup_code
        setup_venv
        systemctl restart "${SERVICE_NAME}.service"
        sleep 2
        systemctl is-active --quiet "$SERVICE_NAME" && ok "更新完成，服务已重启" \
            || die "服务重启失败：journalctl -u $SERVICE_NAME -n 30"
        ;;
    nginx)
        install_deps
        setup_nginx
        setup_cert_renewal
        ;;
    *)
        echo "用法：bash deploy.sh [install|update|nginx]"
        exit 1
        ;;
esac
