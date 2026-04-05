FROM python:3.11-slim

# 设置标签
LABEL maintainer="course-schedule"
LABEL description="Personal course schedule synced with educational administration system"

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户
RUN adduser --disabled-password --gecos '' appuser

WORKDIR /app

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY --chown=appuser:appuser . .

# 创建数据目录
RUN mkdir -p /app/data && chown appuser:appuser /app/data

# 切换到非 root 用户
USER appuser

# 暴露端口
EXPOSE 5000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5000/', timeout=5)" || exit 1

# 生产环境用 Gunicorn（单 worker，保证后台线程唯一）
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "60", \
     "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info", \
     "server:app"]
