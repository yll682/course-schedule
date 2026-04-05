#!/bin/bash
set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}══════════════════════════════════════${NC}"
echo -e "${GREEN}    课程表 · Docker 部署脚本${NC}"
echo -e "${GREEN}══════════════════════════════════════${NC}"
echo

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}错误: 未安装 Docker${NC}"
    echo "请先安装 Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo -e "${RED}错误: 未安装 Docker Compose${NC}"
    echo "请先安装 Docker Compose: https://docs.docker.com/compose/install/"
    exit 1
fi

# 检查 .env 文件
if [ ! -f .env ]; then
    echo -e "${YELLOW}未找到 .env 文件，正在从 .env.example 创建...${NC}"
    cp .env.example .env

    # 生成随机密钥
    STORAGE_KEY=$(python3 -c "import secrets; print(secrets.token_hex(16))")
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

    # 更新 .env 文件
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s/change-this-to-a-random-16-char-key/$STORAGE_KEY/" .env
        sed -i '' "s/change-this-to-a-random-32-char-string/$SECRET_KEY/" .env
    else
        # Linux
        sed -i "s/change-this-to-a-random-16-char-key/$STORAGE_KEY/" .env
        sed -i "s/change-this-to-a-random-32-char-string/$SECRET_KEY/" .env
    fi

    echo -e "${GREEN}✓ 已生成随机密钥并写入 .env 文件${NC}"
    echo
fi

# 提示设置管理员
echo -e "${YELLOW}提示: 如需设置管理员账号，请编辑 .env 文件中的 ADMIN_USERS 变量${NC}"
echo

# 构建镜像
echo -e "${GREEN}正在构建 Docker 镜像...${NC}"
docker-compose build

# 启动容器
echo -e "${GREEN}正在启动服务...${NC}"
docker-compose up -d

# 等待服务启动
echo -e "${GREEN}等待服务启动...${NC}"
sleep 5

# 检查状态
if docker-compose ps | grep -q "Up"; then
    echo
    echo -e "${GREEN}══════════════════════════════════════${NC}"
    echo -e "${GREEN}✓ 部署成功！${NC}"
    echo -e "${GREEN}══════════════════════════════════════${NC}"
    echo
    echo -e "访问地址: ${YELLOW}http://localhost:${PORT:-5000}${NC}"
    echo
    echo "常用命令:"
    echo "  查看日志:   docker-compose logs -f"
    echo "  停止服务:   docker-compose down"
    echo "  重启服务:   docker-compose restart"
    echo "  更新部署:   git pull && docker-compose up -d --build"
    echo
else
    echo
    echo -e "${RED}══════════════════════════════════════${NC}"
    echo -e "${RED}✗ 部署失败${NC}"
    echo -e "${RED}══════════════════════════════════════${NC}"
    echo
    echo "请检查日志: docker-compose logs"
    exit 1
fi
