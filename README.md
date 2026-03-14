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

## ⚙️ 配置

**管理员学号**：安装时填写，或事后修改 `server.py`：

```python
ADMIN_USERS = ['你的学号']
```

修改后重启服务：`systemctl restart course-schedule`

**.env 文件**（位于 `/opt/course-schedule/.env`）：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SECRET_KEY` | Flask Session 密钥，自动生成 | — |
| `PORT` | 监听端口 | 安装时填写 |
| `FLASK_DEBUG` | 开发模式 | false |
| `DB_FILE` | 数据库路径 | `/opt/course-schedule/courses.db` |

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

- 用户密码 AES 加密后存入本地 SQLite，不以明文保存
- Session 使用 HttpOnly + SameSite=Lax Cookie
- 登录接口频率限制：5 次/分钟/IP
- 静态路由屏蔽 `.py`、`.db`、`.env` 等敏感扩展名
- Gunicorn 仅监听 `127.0.0.1`，不直接对外暴露
- `*.db`、`.env` 已加入 `.gitignore`
