#!/usr/bin/env bash
# =============================================================
# 课程表 · 一键部署 (Debian / Ubuntu)
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/yll682/course-schedule/master/deploy.sh)
#
#   国内加速：
#   bash <(curl -fsSL https://edgeone.gh-proxy.org/https://raw.githubusercontent.com/yll682/course-schedule/master/deploy.sh)
# =============================================================
set -euo pipefail

REPO_URL="https://github.com/yll682/course-schedule.git"
REPO_URL_CN="https://edgeone.gh-proxy.org/https://github.com/yll682/course-schedule.git"
APP_DIR="/opt/course-schedule"
APP_USER="courseapp"
SERVICE_NAME="course-schedule"
DEFAULT_PORT="38521"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${CYAN}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}!${NC} $*"; }
die()   { echo -e "${RED}✗ $*${NC}"; exit 1; }

[[ $EUID -ne 0 ]] && die "请以 root 运行：sudo bash <(curl ...)"

# ─────────────────────────────────────────────────────────────
# 主菜单
# ─────────────────────────────────────────────────────────────
main_menu() {
    echo ""
    echo -e "${BOLD}══════════════════════════════════════${NC}"
    echo -e "${BOLD}      课程表 · 部署管理${NC}"
    echo -e "${BOLD}══════════════════════════════════════${NC}"
    echo ""

    # 检测当前状态
    local status_line=""
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        local port; port=$(grep "^PORT=" "$APP_DIR/.env" 2>/dev/null | cut -d= -f2 || echo "?")
        status_line="${GREEN}● 运行中${NC}（端口 ${port}）"
    elif systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        status_line="${YELLOW}● 已安装，未运行${NC}"
    else
        status_line="${CYAN}● 未安装${NC}"
    fi
    echo -e "  当前状态：$status_line"
    echo ""
    echo -e "  ${BOLD}1)${NC} 安装 / 更新"
    echo -e "  ${BOLD}2)${NC} 卸载"
    echo -e "  ${BOLD}3)${NC} 退出"
    echo ""
    echo -en "${BOLD}请选择 [1-3]：${NC} "
    read -r choice
    echo ""

    case "$choice" in
        1) do_install ;;
        2) do_uninstall ;;
        3) exit 0 ;;
        *) die "无效选项：$choice" ;;
    esac
}

# ─────────────────────────────────────────────────────────────
# 安装 / 更新流程
# ─────────────────────────────────────────────────────────────
do_install() {
    local is_update=false
    [[ -d "$APP_DIR/.git" ]] && is_update=true

    PORT="$DEFAULT_PORT"
    ADMIN_ID=""

    if [[ "$is_update" == false ]]; then
        # 首次安装才询问配置
        echo -en "${BOLD}监听端口 [${DEFAULT_PORT}]：${NC} "
        read -r input_port
        PORT="${input_port:-$DEFAULT_PORT}"
        if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); then
            die "无效端口：$PORT（范围 1024-65535）"
        fi

        echo -en "${BOLD}管理员学号（留空则之后手动改 server.py）：${NC} "
        read -r ADMIN_ID
        echo ""
    fi

    install_deps
    create_user
    setup_code
    setup_venv
    if [[ "$is_update" == false ]]; then
        setup_env
    fi
    patch_env
    setup_systemd
    print_done
}

# ─────────────────────────────────────────────────────────────
# 卸载流程
# ─────────────────────────────────────────────────────────────
do_uninstall() {
    if ! systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null && [[ ! -d "$APP_DIR" ]]; then
        die "未检测到已安装的实例"
    fi

    echo -e "${RED}此操作将删除服务和所有数据（包括课表数据库）！${NC}"
    echo -en "${BOLD}确认卸载？输入 yes 继续：${NC} "
    read -r confirm
    [[ "$confirm" != "yes" ]] && { info "已取消"; exit 0; }

    echo ""
    info "停止并删除 systemd 服务..."
    systemctl stop  "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    rm -f "/etc/sudoers.d/${SERVICE_NAME}-restart"
    systemctl daemon-reload

    info "删除应用目录 $APP_DIR..."
    rm -rf "$APP_DIR"

    info "删除系统用户 $APP_USER..."
    userdel "$APP_USER" 2>/dev/null || true

    echo ""
    ok "卸载完成"
}

# ─────────────────────────────────────────────────────────────
# 各步骤函数
# ─────────────────────────────────────────────────────────────
install_deps() {
    info "更新软件包列表..."
    apt-get update -qq
    info "安装系统依赖..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3 python3-venv
    ok "依赖就绪"
}

create_user() {
    if id "$APP_USER" &>/dev/null; then
        info "用户 $APP_USER 已存在"
    else
        useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
        ok "用户 $APP_USER 创建完成"
    fi
}

setup_code() {
    # Git 2.35+ 安全检查
    git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
    # 国内网络 HTTP/2 不稳定，强制用 HTTP/1.1
    git config --global http.version HTTP/1.1 2>/dev/null || true

    if [[ -d "$APP_DIR/.git" ]]; then
        info "拉取最新代码..."
        if ! git -C "$APP_DIR" fetch origin 2>/dev/null; then
            info "直连失败，尝试 EdgeOne 加速..."
            git -C "$APP_DIR" remote set-url origin "$REPO_URL_CN"
            git -C "$APP_DIR" fetch origin
            git -C "$APP_DIR" remote set-url origin "$REPO_URL"
        fi
        git -C "$APP_DIR" reset --hard origin/master
    else
        info "克隆仓库..."
        if ! git clone "$REPO_URL" "$APP_DIR" 2>/dev/null; then
            info "直连失败，尝试 EdgeOne 加速..."
            git clone "$REPO_URL_CN" "$APP_DIR"
        fi
    fi
    ok "代码就绪"
}

setup_venv() {
    info "配置 Python 虚拟环境..."
    python3 -m venv "$APP_DIR/.venv"
    "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
    "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
    ok "Python 依赖安装完成"
}

patch_env() {
    local env_file="$APP_DIR/.env"
    if ! grep -q "^STORAGE_AES_KEY=" "$env_file" 2>/dev/null; then
        local storage_key
        storage_key=$(python3 -c "import secrets; print(secrets.token_hex(16))")
        echo "STORAGE_AES_KEY=${storage_key}" >> "$env_file"
        ok "已自动生成 STORAGE_AES_KEY 并写入 .env"
    fi
}

setup_env() {
    local env_file="$APP_DIR/.env"
    info "生成 .env..."
    local secret_key storage_key
    secret_key=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    storage_key=$(python3 -c "import secrets; print(secrets.token_hex(16))")
    cat > "$env_file" <<EOF
SECRET_KEY=${secret_key}
STORAGE_AES_KEY=${storage_key}
ADMIN_USERS=${ADMIN_ID}
PORT=${PORT}
FLASK_DEBUG=false
DB_FILE=${APP_DIR}/data/courses.db
EOF
    chmod 600 "$env_file"
    ok ".env 已生成（SECRET_KEY 和 STORAGE_AES_KEY 随机生成）"
}


setup_systemd() {
    # ── 数据目录迁移（兼容旧版本）──────────────────────────────
    local old_db="$APP_DIR/courses.db"
    local new_db="$APP_DIR/data/courses.db"
    mkdir -p "$APP_DIR/data"
    # 如果旧数据库存在且新位置还没有，则迁移
    if [[ -f "$old_db" && ! -f "$new_db" ]]; then
        info "迁移数据库到 data/ 目录..."
        mv "$old_db" "$new_db"
        # 顺带移走可能存在的 WAL 旁文件
        mv "${old_db}-wal" "$APP_DIR/data/" 2>/dev/null || true
        mv "${old_db}-shm" "$APP_DIR/data/" 2>/dev/null || true
        ok "数据库已迁移"
    fi
    chown "$APP_USER:$APP_USER" "$APP_DIR/data"
    chmod 750 "$APP_DIR/data"
    [[ -f "$new_db" ]] && chown "$APP_USER:$APP_USER" "$new_db"
    # 更新 .env 中的 DB_FILE（无论新旧安装）
    if grep -q "^DB_FILE=" "$APP_DIR/.env" 2>/dev/null; then
        sed -i "s|^DB_FILE=.*|DB_FILE=${APP_DIR}/data/courses.db|" "$APP_DIR/.env"
    else
        echo "DB_FILE=${APP_DIR}/data/courses.db" >> "$APP_DIR/.env"
    fi
    # ── 以下原有逻辑 ───────────────────────────────────────────
    info "配置 systemd 服务..."
    local svc_file="/etc/systemd/system/${SERVICE_NAME}.service"
    printf '[Unit]\nDescription=课程表 Web 应用\nAfter=network.target\n\n[Service]\nType=simple\nUser=%s\nWorkingDirectory=%s\nEnvironmentFile=%s/.env\nExecStart=%s/.venv/bin/gunicorn \\\n    --workers 1 \\\n    --bind 127.0.0.1:${PORT} \\\n    --timeout 60 \\\n    --access-logfile - \\\n    --error-logfile - \\\n    server:app\nRestart=on-failure\nRestartSec=5\nStandardOutput=journal\nStandardError=journal\n\n[Install]\nWantedBy=multi-user.target\n' \
        "$APP_USER" "$APP_DIR" "$APP_DIR" "$APP_DIR" > "$svc_file"
    chown root:root "$APP_DIR"
    chmod 755 "$APP_DIR"
    # .env 也由 courseapp 读取
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"

    # 配置 sudoers 允许 courseapp 重启服务
    local sudoers_file="/etc/sudoers.d/${SERVICE_NAME}-restart"
    echo "${APP_USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart ${SERVICE_NAME}" > "$sudoers_file"
    chmod 440 "$sudoers_file"

    systemctl daemon-reload
    systemctl enable --quiet "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        ok "服务已启动并设为开机自启"
    else
        journalctl -u "$SERVICE_NAME" -n 20 --no-pager
        die "服务启动失败，日志见上"
    fi
}

print_done() {
    local port; port=$(grep "^PORT=" "$APP_DIR/.env" | cut -d= -f2)
    local ip;   ip=$(hostname -I | awk '{print $1}')
    echo ""
    echo -e "${GREEN}══════════════════════════════════════${NC}"
    echo -e "${GREEN}  完成！${NC}"
    echo -e "  本机地址：${CYAN}http://127.0.0.1:${port}${NC}"
    echo -e "  内网地址：${CYAN}http://${ip}:${port}${NC}"
    echo ""
    echo -e "  查看日志：${YELLOW}journalctl -u ${SERVICE_NAME} -f${NC}"
    echo -e "${GREEN}══════════════════════════════════════${NC}"
    echo ""
}

# ─────────────────────────────────────────────────────────────
main_menu
