# 课程表

与翔安教务系统（JW）实时同步的 Web 课程表，支持多用户、分享、导入/导出。

## ✨ 功能

### 核心
- 登录教务系统账号，自动获取并展示课程表（周视图）
- 后台定时抓取（默认 60 分钟），断网时展示本地缓存
- 多节连上跨格显示，深色/浅色主题自动跟随系统

### 分享
- 生成 8 位分享码，设定有效期（1 天 / 7 天 / 30 天 / 半年）和可查看周次范围
- 持码者在登录页输入分享码即可只读查看，无需账号密码

### 导入 / 导出
- **导出整学期**：在设置页一键导出全学期 JSON 文件（同时触发缓存所有周）
- **导入查看**：在登录页选择 JSON 文件，纯前端预览，关闭标签页后自动清除

### 管理员
- 调整全局后台抓取间隔
- 查看并撤销所有用户的分享码
- 查看所有用户已缓存的课表（按周次点击查看，不会主动请求教务系统）

## 🚀 部署

### Docker（推荐）

```bash
cp .env.example .env
# 编辑 .env，至少填写 SECRET_KEY
docker-compose up -d
```

### 本地直接运行

```bash
pip install -r requirements.txt
python server.py
```

访问 http://localhost:5000

## ⚙️ 配置

`.env` 文件（参考 `.env.example`）：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SECRET_KEY` | Flask Session 密钥（必填，否则重启后需重新登录） | 随机生成 |
| `PORT` | 监听端口 | 5000 |
| `FLASK_DEBUG` | 开发模式 | false |
| `DB_FILE` | 数据库路径 | courses.db |

**管理员账号**：修改 `server.py` 中的 `ADMIN_USERS` 列表，填入学号。

## 🗂️ 项目结构

```
├── server.py           # Flask 后端
├── jw_client.py        # 教务系统 API 客户端
├── index.html          # 课程表主页
├── login.html          # 登录页（含分享码 / 导入入口）
├── admin.html          # 设置页
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## 🔒 安全说明

- 用户密码经 AES-ECB 加密后存入本地 SQLite，不以明文保存
- Session 使用 HttpOnly + SameSite=Lax Cookie
- 登录接口有频率限制（5 次/分钟/IP）
- 静态文件路由屏蔽 `.py`、`.db`、`.env` 等敏感扩展名
- `*.db`、`.env` 已加入 `.gitignore`，不会提交到版本库

## 📦 卸载

**Docker**：`docker-compose down -v`
**本地**：直接删除文件夹
