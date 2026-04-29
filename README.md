# 课程表

与翔安教务系统实时同步的个人 Web 课程表，支持多用户、分享码、导入/导出。

## ✨ 功能

| 功能 | 说明 |
|------|------|
| 实时同步 | 登录教务账号后自动拉取课表，断网时展示本地缓存 |
| 后台定时抓取 | 默认每 60 分钟为所有用户刷新前后三周，可调整 |
| 分享码 | 生成带有效期和周次范围的 8 位分享码，对方无需账号即可只读查看 |
| 导出整学期 | 设置页一键导出全学期 JSON，同时触发所有周缓存 |
| 导入查看 | 登录页选择 JSON 文件，纯前端预览，关闭标签页自动清除 |
| 多用户 | 每人独立课表，互不干扰 |
| 深色模式 | 跟随系统自动切换 |

### 管理员专属

- 调整全局后台抓取间隔
- 查看并撤销所有用户的分享码（含创建者、时长、周次等详情）
- 查看所有用户已缓存的课表（按周次点击，不触发实时请求）

## 🚀 部署

### 方式一：传统部署（推荐）

**要求：** Debian / Ubuntu，root 权限

国际线路：
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/yll682/course-schedule/master/deploy.sh)
```

国内线路（EdgeOne 加速）：
```bash
bash <(curl -fsSL https://edgeone.gh-proxy.org/https://raw.githubusercontent.com/yll682/course-schedule/master/deploy.sh)
```

运行后出现菜单，选择操作：

```
══════════════════════════════════════
      课程表 · 部署管理
══════════════════════════════════════

  当前状态：● 未安装

  1) 安装 / 更新
  2) 卸载
  3) 退出
```

- **安装**：首次询问监听端口（默认 `38521`）和管理员学号，之后全自动
- **更新**：再次运行选 1，直接拉取最新代码并重启，无需重新配置
- **卸载**：删除服务、应用目录、系统用户

安装完成后通过反向代理（Nginx / Caddy 等）将流量转发到 `127.0.0.1:<端口>` 对外暴露。

### 方式二：Docker 部署

**要求：** Docker 和 Docker Compose

#### 快速部署

```bash
# 1. 克隆项目
git clone https://github.com/yll682/course-schedule.git
cd course-schedule

# 2. 运行部署脚本
chmod +x docker-deploy.sh
./docker-deploy.sh
```

部署脚本会自动：
- 复制 `.env.example` 为 `.env`
- 生成随机密钥
- 构建并启动 Docker 容器

#### 手动部署

```bash
# 1. 克隆项目
git clone https://github.com/yll682/course-schedule.git
cd course-schedule

# 2. 创建配置文件
cp .env.example .env

# 3. 编辑 .env 文件，设置必需的环境变量
# 必须设置: STORAGE_AES_KEY
nano .env

# 4. 构建并启动
docker-compose up -d --build

# 5. 查看日志
docker-compose logs -f
```

#### Docker 常用命令

```bash
# 查看运行状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 重启服务
docker-compose restart

# 停止服务
docker-compose down

# 更新部署
git pull
docker-compose up -d --build

# 进入容器调试
docker-compose exec course-schedule /bin/bash
```

#### Docker 环境变量

Docker 部署时的环境变量通过 `.env` 文件配置，与传统部署相同。数据库会自动持久化到 Docker volume `course-data`。

## ⚙️ 配置

**管理员学号**：安装时填写，或事后修改 `server.py`：

```python
ADMIN_USERS = ['你的学号']
```

修改后重启服务：`systemctl restart course-schedule`

**.env 文件**（位于 `/opt/course-schedule/.env`）：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `STORAGE_AES_KEY` | **必需**，本地密码加密密钥（16字节hex） | — |
| `SECRET_KEY` | Flask Session 密钥，自动生成 | — |
| `JW_API_KEY` | 教务系统API加密密钥 | 默认值（无需修改） |
| `ADMIN_USERS` | 管理员学号（逗号分隔） | — |
| `PORT` | 监听端口 | 安装时填写 |
| `FLASK_DEBUG` | 开发模式 | false |
| `DB_FILE` | 数据库路径 | `/opt/course-schedule/courses.db` |

**重要**：`STORAGE_AES_KEY` 必须在首次运行前设置，可使用以下命令生成：
```bash
python -c "import secrets; print(secrets.token_hex(16))"
```

## 🗂️ 项目结构

```
├── server.py           # Flask 后端
├── jw_client.py        # 教务系统 API 客户端
├── index.html          # 课程表主页
├── login.html          # 登录页（含分享码 / 导入入口）
├── admin.html          # 设置页
├── deploy.sh           # 一键部署脚本
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## 🔒 安全说明

- **CSRF 保护**：所有 POST/PUT/DELETE 请求均需 CSRF token，防止跨站请求伪造攻击
- **Session 安全**：登录后重新生成 Session ID，防止 Session 固定攻击
- **密码加密**：用户密码使用 AES-GCM 加密后存入本地 SQLite，不以明文保存
- **Session Cookie**：使用 HttpOnly + SameSite=Lax + Secure（生产环境）
- **Content-Security-Policy**：严格的 CSP 策略，防止 XSS 攻击
- **频率限制**：登录接口 5 次/分钟/IP，防止暴力破解
- **静态文件保护**：屏蔽 `.py`、`.db`、`.env` 等敏感扩展名
- **仅本地监听**：Gunicorn 仅监听 `127.0.0.1`，不直接对外暴露
- **敏感文件**：`*.db`、`.env` 已加入 `.gitignore`

## 📡 API 接口

### 周次查询接口

**接口地址**：`GET /api/week_number`

**功能**：根据日期查询对应的学期周次

**参数**：
- `date`（可选）：YYYY-MM-DD 格式的日期字符串

**返回格式**：JSON

#### 使用示例

1. **获取当前周次**（不传参数）
```bash
curl http://localhost:5000/api/week_number
```
返回：
```json
{
  "success": true,
  "week_number": 7,
  "current_date": "2026-04-19"
}
```

2. **查询指定日期的周次**
```bash
curl "http://localhost:5000/api/week_number?date=2026-05-06"
```
返回：
```json
{
  "success": true,
  "week_number": 8,
  "target_date": "2026-05-06"
}
```

3. **超出学期范围的日期**
```bash
curl "http://localhost:5000/api/week_number?date=2026-09-01"
```
返回：
```json
{
  "success": true,
  "week_number": 26,
  "target_date": "2026-09-01",
  "warning": "计算出的周次 26 超出学期范围（最大周次：19）"
}
```

#### 计算原理

接口从数据库最近的课表缓存中获取参照数据：
- 参照周次（`current_week`）
- 参照日期（`today`）

然后计算目标日期与参照日期的天数差，推算出周次：
```
target_week = current_week + (days_diff / 7)
```

#### 错误处理

- **无缓存数据**：返回 404，提示系统中无数据
- **日期格式错误**：返回 400，提示使用正确格式（YYYY-MM-DD）
- **超出学期范围**：返回计算结果，并附带警告提示
