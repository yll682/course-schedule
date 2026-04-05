# Docker 部署文档

本文档提供课程表应用的 Docker 部署详细说明。

## 目录

- [快速开始](#快速开始)
- [环境变量配置](#环境变量配置)
- [数据持久化](#数据持久化)
- [反向代理配置](#反向代理配置)
- [故障排查](#故障排查)

## 快速开始

### 前置要求

- Docker 19.03+
- Docker Compose 1.27+

### 一键部署

```bash
# 克隆项目
git clone https://github.com/yll682/course-schedule.git
cd course-schedule

# 运行部署脚本
chmod +x docker-deploy.sh
./docker-deploy.sh
```

访问 `http://localhost:5000` 即可使用。

### 手动部署

```bash
# 1. 准备配置文件
cp .env.example .env

# 2. 编辑 .env，生成密钥
nano .env
```

生成密钥命令：
```bash
# STORAGE_AES_KEY (必需)
python -c "import secrets; print(secrets.token_hex(16))"

# SECRET_KEY (可选，不设置则自动生成)
python -c "import secrets; print(secrets.token_hex(32))"
```

```bash
# 3. 构建并启动
docker-compose up -d --build

# 4. 查看日志确认启动成功
docker-compose logs -f
```

## 环境变量配置

### 必需变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `STORAGE_AES_KEY` | 密码加密密钥（16字节hex） | `d050746cb3e7f445019a8a67ddf5a013` |

### 可选变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SECRET_KEY` | Flask Session 密钥 | 自动生成 |
| `JW_API_KEY` | 教务系统API密钥 | 使用默认值 |
| `ADMIN_USERS` | 管理员学号（逗号分隔） | 空 |
| `PORT` | 监听端口 | `5000` |
| `FLASK_DEBUG` | 调试模式 | `false` |

### 配置示例

```env
# 必需
STORAGE_AES_KEY=d050746cb3e7f445019a8a67ddf5a013

# 可选
ADMIN_USERS=2405309121,202012345
PORT=5000
FLASK_DEBUG=false
```

## 数据持久化

Docker 部署使用 Docker volume 持久化数据：

- **volume 名称**: `course-data`
- **挂载路径**: `/app/data`
- **包含内容**:
  - SQLite 数据库 (`courses.db`)
  - Session 密钥文件 (`.secret_key`)

### 备份数据

```bash
# 备份 volume 数据
docker run --rm -v course-data:/data -v $(pwd):/backup alpine tar czf /backup/course-backup.tar.gz /data

# 恢复数据
docker run --rm -v course-data:/data -v $(pwd):/backup alpine tar xzf /backup/course-backup.tar.gz -C /
```

### 查看 volume 位置

```bash
docker volume inspect course-data
```

## 反向代理配置

### Nginx 配置示例

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 强制 HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /path/to/ssl/cert.pem;
    ssl_certificate_key /path/to/ssl/key.pem;

    # 安全头
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket 支持（如果需要）
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

### Caddy 配置示例

```caddyfile
your-domain.com {
    reverse_proxy localhost:5000
}
```

## 常用操作

### 容器管理

```bash
# 查看容器状态
docker-compose ps

# 查看实时日志
docker-compose logs -f

# 查看最近 100 行日志
docker-compose logs --tail=100

# 重启服务
docker-compose restart

# 停止服务
docker-compose down

# 停止并删除 volume（危险操作！）
docker-compose down -v
```

### 更新部署

```bash
# 拉取最新代码
git pull origin master

# 重新构建并启动
docker-compose up -d --build

# 查看日志确认
docker-compose logs -f
```

### 进入容器调试

```bash
# 进入运行中的容器
docker-compose exec course-schedule /bin/bash

# 查看数据库
sqlite3 /app/data/courses.db

# 查看进程
ps aux | grep gunicorn
```

## 故障排查

### 1. 容器无法启动

**检查日志**:
```bash
docker-compose logs
```

**常见原因**:
- `STORAGE_AES_KEY` 未设置
- 端口被占用
- 依赖安装失败

**解决方法**:
```bash
# 检查环境变量
docker-compose config

# 检查端口占用
lsof -i :5000

# 重新构建
docker-compose build --no-cache
```

### 2. 无法访问应用

**检查容器状态**:
```bash
docker-compose ps
```

**检查端口映射**:
```bash
docker port course-schedule
```

**测试容器内连接**:
```bash
docker-compose exec course-schedule curl http://localhost:5000
```

### 3. 数据库错误

**检查数据库文件**:
```bash
docker-compose exec course-schedule ls -la /app/data/
```

**检查权限**:
```bash
docker-compose exec course-schedule stat /app/data/courses.db
```

**重建数据库（会丢失数据）**:
```bash
docker-compose down
docker volume rm course-data
docker-compose up -d
```

### 4. 性能问题

**查看资源使用**:
```bash
docker stats course-schedule
```

**调整 worker 数量**（仅适用于多进程场景，本项目限制为 1）:

编辑 `Dockerfile` 中的 CMD 命令，但注意后台线程要求单 worker。

### 5. Session 问题

**检查密钥文件**:
```bash
docker-compose exec course-schedule cat /app/data/.secret_key 2>/dev/null || echo "未生成"
```

**重新生成 Session 密钥**:
```bash
docker-compose exec course-schedule rm -f /app/data/.secret_key
docker-compose restart
```

## 生产环境建议

### 1. 安全配置

- ✅ 设置强随机 `STORAGE_AES_KEY`
- ✅ 配置 HTTPS（通过反向代理）
- ✅ 设置 `FLASK_DEBUG=false`
- ✅ 不要暴露 5000 端口到公网，使用反向代理

### 2. 备份策略

```bash
# 定期备份脚本 (crontab)
0 2 * * * docker run --rm -v course-data:/data -v /backup:/backup alpine tar czf /backup/course-$(date +\%Y\%m\%d).tar.gz /data
```

### 3. 监控

- 使用 Docker healthcheck（已配置）
- 监控容器资源使用
- 设置日志轮转（已配置：10MB × 3 文件）

### 4. 更新策略

```bash
# 安全更新流程
git pull
docker-compose pull  # 拉取基础镜像更新
docker-compose up -d --build
docker-compose logs -f
```

## 高级配置

### 自定义 Gunicorn 参数

编辑 `Dockerfile` 中的 CMD 命令：

```dockerfile
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--timeout", "120", \  # 增加超时时间
     "--keep-alive", "5", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "server:app"]
```

### 多实例部署（不推荐）

本项目使用后台线程定时抓取课表，不支持多 worker。如果需要负载均衡，建议：

1. 使用单个实例
2. 在上游使用缓存（如 Redis）
3. 优化数据库查询

## 技术支持

如遇问题，请提供以下信息：

```bash
# 系统信息
docker version
docker-compose version

# 容器日志
docker-compose logs --tail=100 > logs.txt

# 配置信息（隐藏敏感信息）
docker-compose config > config.txt
```

提交 Issue: https://github.com/yll682/course-schedule/issues
