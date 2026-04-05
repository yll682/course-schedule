# Docker 部署测试检查清单

## 部署前检查

### 1. 检查 Docker 安装

```bash
# 检查 Docker 版本
docker --version

# 检查 Docker Compose 版本
docker-compose --version
# 或
docker compose version
```

预期输出：
```
Docker version 20.10+
Docker Compose version v2.0+
```

### 2. 检查 Docker 服务状态

```bash
# Linux
systemctl status docker

# Windows/Mac
docker info
```

### 3. 检查必需文件

```bash
# 确认这些文件存在
ls -la Dockerfile docker-compose.yml .env.example requirements.txt
```

## 部署测试

### 测试 1: 配置文件准备

```bash
# 复制配置文件
cp .env.example .env

# 编辑配置文件
nano .env
```

**验证点**:
- [ ] `STORAGE_AES_KEY` 已设置为 32 字符 hex 字符串
- [ ] `ADMIN_USERS` 已设置（可选）
- [ ] `FLASK_DEBUG=false`

生成密钥：
```bash
python -c "import secrets; print(secrets.token_hex(16))"
```

### 测试 2: 镜像构建

```bash
# 构建镜像
docker-compose build
```

**预期输出**:
```
Successfully built <image-id>
Successfully tagged course-schedule:latest
```

**验证点**:
- [ ] 无错误信息
- [ ] 镜像构建成功
- [ ] 所有依赖安装完成

### 测试 3: 容器启动

```bash
# 启动容器
docker-compose up -d

# 查看状态
docker-compose ps
```

**预期输出**:
```
NAME                STATUS              PORTS
course-schedule     Up (healthy)        0.0.0.0:5000->5000/tcp
```

**验证点**:
- [ ] 容器状态为 `Up`
- [ ] 健康检查通过 `(healthy)`
- [ ] 端口正确映射

### 测试 4: 日志检查

```bash
# 查看日志
docker-compose logs --tail=50
```

**预期看到**:
```
INFO: Waiting for application startup.
INFO: Application startup complete.
```

**不应看到**:
- `ERROR` 级别日志
- `STORAGE_AES_KEY` 未设置的错误
- 端口占用错误

### 测试 5: HTTP 访问测试

```bash
# 本地测试
curl http://localhost:5000

# 或使用浏览器访问
# http://localhost:5000
```

**验证点**:
- [ ] 返回 HTML 页面
- [ ] 无 500 错误
- [ ] 登录页面可访问

### 测试 6: 登录功能测试

1. 访问 `http://localhost:5000/login.html`
2. 输入学号和密码
3. 点击登录

**验证点**:
- [ ] 能成功跳转到主页
- [ ] 显示课程表信息
- [ ] 无错误提示

### 测试 7: 数据持久化测试

```bash
# 检查数据目录
docker-compose exec course-schedule ls -la /app/data/

# 检查数据库文件
docker-compose exec course-schedule test -f /app/data/courses.db && echo "数据库存在"
```

**验证点**:
- [ ] `/app/data/` 目录存在
- [ ] `courses.db` 文件已创建（登录后）

### 测试 8: 重启测试

```bash
# 重启容器
docker-compose restart

# 等待启动
sleep 5

# 检查状态
docker-compose ps
```

**验证点**:
- [ ] 容器正常重启
- [ ] 数据仍然存在
- [ ] 无需重新登录（Session 保持）

## 常见问题排查

### 问题 1: 镜像构建失败

**症状**:
```
ERROR: Could not install packages
```

**解决**:
```bash
# 清理 Docker 缓存
docker-compose build --no-cache

# 检查网络连接
ping pypi.org
```

### 问题 2: 容器无法启动

**症状**:
```
ERROR: for course-schedule  Cannot start service
```

**排查步骤**:
```bash
# 查看详细日志
docker-compose logs

# 检查端口占用
lsof -i :5000  # Linux/Mac
netstat -ano | findstr :5000  # Windows

# 检查环境变量
docker-compose config
```

### 问题 3: 健康检查失败

**症状**:
```
health: starting → unhealthy
```

**排查步骤**:
```bash
# 进入容器
docker-compose exec course-schedule /bin/bash

# 测试应用
curl http://localhost:5000

# 检查进程
ps aux | grep gunicorn
```

### 问题 4: 无法访问应用

**症状**:
- 浏览器显示 "Connection refused"
- curl 超时

**排查步骤**:
```bash
# 检查端口映射
docker port course-schedule

# 检查防火墙（Linux）
sudo ufw status

# 测试容器内连接
docker-compose exec course-schedule curl http://localhost:5000
```

## 生产环境验证

### 1. 安全检查

```bash
# 检查环境变量是否泄露
docker-compose exec course-schedule env | grep -E "KEY|SECRET"

# 确认非 root 用户运行
docker-compose exec course-schedule whoami
```

**预期**:
- 使用 `appuser` 用户
- 敏感信息不显示在日志中

### 2. 性能测试

```bash
# 查看资源使用
docker stats course-schedule --no-stream

# 查看容器大小
docker images course-schedule
```

### 3. 备份测试

```bash
# 创建备份
docker run --rm -v course-data:/data -v $(pwd):/backup alpine tar czf /backup/test-backup.tar.gz /data

# 验证备份
tar tzf test-backup.tar.gz
```

## 更新测试

```bash
# 1. 备份数据
docker run --rm -v course-data:/data -v $(pwd):/backup alpine tar czf /backup/pre-update.tar.gz /data

# 2. 拉取更新
git pull

# 3. 重新构建
docker-compose up -d --build

# 4. 检查状态
docker-compose ps
docker-compose logs --tail=20

# 5. 功能测试
curl http://localhost:5000
```

## 清理测试

```bash
# 停止并删除容器
docker-compose down

# 删除 volume（危险操作！）
docker-compose down -v

# 删除镜像
docker rmi course-schedule:latest

# 清理悬空镜像和容器
docker system prune -f
```

## 检查清单总结

部署成功标志：
- [ ] Docker 和 Docker Compose 已安装
- [ ] 配置文件正确（.env 存在且有效）
- [ ] 镜像构建成功
- [ ] 容器启动成功
- [ ] 健康检查通过
- [ ] HTTP 访问正常
- [ ] 登录功能正常
- [ ] 数据持久化正常
- [ ] 重启后数据保持

全部通过后，即可投入生产使用！
