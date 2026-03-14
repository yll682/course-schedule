#!/usr/bin/env bash
# =============================================================
# 课程表 · 一键部署 (Debian / Ubuntu)
#
# 一行命令完成所有操作：
#   bash <(curl -fsSL https://raw.githubusercontent.com/yll682/course-schedule/master/deploy.sh)
#
# 更新已部署的实例：
#   bash <(curl -fsSL https://raw.githubusercontent.com/yll682/course-schedule/master/deploy.sh) update
# =============================================================
set -euo pipefail

REPO_URL="https://github.com/yll682/course-schedule.git"
APP_DIR="/opt/course-schedule"
APP_USER="courseapp"
SERVICE_NAME="course-schedule"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${CYAN}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}!${NC} $*"; }
die()   { echo -e "${RED}✗ $*${NC}"; exit 1; }
ask()   { echo -en "${BOLD}$*${NC} "; }

[[ $EUID -ne 0 ]] && die "请以 root 运行，例如：sudo bash <(curl ...)"
MODE="${1:-install}"

# ─────────────────────────────────────────────────────────────
# 交互式配置（仅在首次安装时询问）
# ─────────────────────────────────────────────────────────────
interactive_config() {
    echo ""
    echo -e "${BOLD}══════════════════════════════════════${NC}"
    echo -e "${BOLD}  课程表 · 一键部署${NC}"
    echo -e "${BOLD}══════════════════════════════════════${NC}"
    echo ""

    # 端口
    ask "监听端口 [5000]："
    read -r input_port
    PORT="${input_port:-5000}"
    if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
        die "无效端口：$PORT"
    fi

    # 管理员学号
    ask "管理员学号（留空则之后手动改 server.py）："
    read -r input_admin
    ADMIN_ID="${input_admin:-}"

    echo ""
}

# ─────────────────────────────────────────────────────────────
# 系统依赖
# ─────────────────────────────────────────────────────────────
install_deps() {
    info "更新软件包列表..."
    apt-get update -qq
    info "安装系统依赖（git python3 python3-venv）..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3 python3-venv
    ok "依赖就绪"
}

# ─────────────────────────────────────────────────────────────
# 系统用户
# ─────────────────────────────────────────────────────────────
create_user() {
    if id "$APP_USER" &>/dev/null; then
        info "用户 $APP_USER 已存在"
    else
        info "创建系统用户 $APP_USER..."
        useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
        ok "用户 $APP_USER 创建完成"
    fi
}

# ─────────────────────────────────────────────────────────────
# 克隆代码
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
# 虚拟环境 + 依赖
# ─────────────────────────────────────────────────────────────
setup_venv() {
    info "配置 Python 虚拟环境..."
    python3 -m venv "$APP_DIR/.venv"
    "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
    "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
    ok "Python 依赖安装完成"
}

# ─────────────────────────────────────────────────────────────
# .env
# ─────────────────────────────────────────────────────────────
setup_env() {
    ENV_FILE="$APP_DIR/.env"
    if [[ -f "$ENV_FILE" ]]; then
        info ".env 已存在，保留原有配置"
        # 如果端口有变化则更新
        if ! grep -q "^PORT=${PORT}$" "$ENV_FILE"; then
            sed -i "s/^PORT=.*/PORT=${PORT}/" "$ENV_FILE"
            info ".env 中 PORT 已更新为 ${PORT}"
        fi
    else
        info "生成 .env..."
        SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        cat > "$ENV_FILE" <<EOF
SECRET_KEY=${SECRET_KEY}
PORT=${PORT}
FLASK_DEBUG=false
DB_FILE=${APP_DIR}/courses.db
EOF
        chmod 600 "$ENV_FILE"
        ok ".env 已生成（SECRET_KEY 随机生成）"
    fi
}

# ─────────────────────────────────────────────────────────────
# 修改管理员学号
# ─────────────────────────────────────────────────────────────
patch_admin() {
    [[ -z "${ADMIN_ID:-}" ]] && return
    info "设置管理员学号：$ADMIN_ID"
    sed -i "s/ADMIN_USERS = \[.*\]/ADMIN_USERS = ['${ADMIN_ID}']/" "$APP_DIR/server.py"
    ok "管理员学号已设为 $ADMIN_ID"
}

# ─────────────────────────────────────────────────────────────
# systemd 服务
# ─────────────────────────────────────────────────────────────
setup_systemd() {
    info "配置 systemd 服务..."
    cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
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
    --bind 127.0.0.1:\${PORT:-5000} \\
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
    systemctl enable --quiet "${SERVICE_NAME}.service"
    systemctl restart "${SERVICE_NAME}.service"

    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        ok "服务已启动并设为开机自启"
    else
        journalctl -u "$SERVICE_NAME" -n 20 --no-pager
        die "服务启动失败，日志见上"
    fi
}

# ─────────────────────────────────────────────────────────────
# 完成提示
# ─────────────────────────────────────────────────────────────
print_done() {
    local ip
    ip=$(hostname -I | awk '{print $1}')
    echo ""
    echo -e "${GREEN}══════════════════════════════════════${NC}"
    echo -e "${GREEN}  部署完成！${NC}"
    echo -e "  访问地址：${CYAN}http://${ip}:${PORT}${NC}"
    echo ""
    echo -e "  查看日志：${YELLOW}journalctl -u ${SERVICE_NAME} -f${NC}"
    echo -e "  重启服务：${YELLOW}systemctl restart ${SERVICE_NAME}${NC}"
    echo -e "  停止服务：${YELLOW}systemctl stop ${SERVICE_NAME}${NC}"
    echo -e "  更新版本：${YELLOW}bash <(curl -fsSL ${REPO_URL/\.git/}/raw/master/deploy.sh) update${NC}"
    echo -e "${GREEN}══════════════════════════════════════${NC}"
    echo ""
}

# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
case "$MODE" in
    install)
        interactive_config
        install_deps
        create_user
        setup_code
        setup_venv
        setup_env
        patch_admin
        setup_systemd
        print_done
        ;;
    update)
        info "更新模式..."
        setup_code
        setup_venv
        systemctl restart "${SERVICE_NAME}.service"
        sleep 2
        systemctl is-active --quiet "$SERVICE_NAME" \
            && ok "更新完成，服务已重启" \
            || die "服务重启失败：journalctl -u $SERVICE_NAME -n 30"
        ;;
    *)
        echo "用法："
        echo "  首次安装：bash <(curl -fsSL <url>)"
        echo "  更新代码：bash <(curl -fsSL <url>) update"
        exit 1
        ;;
esac
