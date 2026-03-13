# 课程表系统

自动同步教务系统课表的Web应用

## ✨ 已实现功能

### 核心功能
- ✅ 用户登录系统（待对接教务系统API）
- ✅ 课程表展示（周视图，10节课时间轴）
- ✅ 周次切换（上周/下周按钮）
- ✅ 多节连上课程跨格子显示
- ✅ 响应式设计（适配手机/平板/电脑）
- ✅ 深色/浅色主题自动适配

### 缓存机制
- ✅ 永久缓存课表数据
- ✅ 每次访问尝试更新，失败则使用缓存
- ✅ 缓存失败时显示黄色提示条
- ✅ 后台定时抓取（默认60分钟）
- ✅ 自动抓取上周、当周、下周课表

### 管理功能
- ✅ 管理员后台（配置抓取频率）
- ✅ 多用户支持（每人独立课表）
- ✅ SQLite数据库存储

## 🚀 部署方式

### Docker部署（推荐）
```bash
docker-compose up -d
```

### Windows部署
```bash
pip install -r requirements.txt
start.bat
```

访问：http://localhost:5000

## ⚙️ 配置

1. 修改 `server.py` 中的管理员学号：
```python
ADMIN_USERS = ['YOUR_STUDENT_ID']
```

2. 登录后在管理后台设置抓取频率

## 📋 待实现功能

- [ ] 对接教务系统登录API
- [ ] 实现真实课表数据抓取
- [ ] 自动检测当前周次
- [ ] 密码修改检测与重新登录提示

## 🗂️ 项目结构

```
├── server.py           # Flask后端
├── index.html          # 课表主页
├── login.html          # 登录页
├── admin.html          # 管理后台
├── courses.db          # SQLite数据库
├── Dockerfile          # Docker配置
├── docker-compose.yml  # Docker Compose配置
└── requirements.txt    # Python依赖
```

## 🔒 安全说明

- 不要将包含真实账号密码的文件提交到Git
- `参考文件/` 目录已被 `.gitignore` 忽略
- 生产环境请修改 `app.secret_key`

## 📦 卸载

**Docker**: `docker-compose down`
**Windows**: 直接删除文件夹
