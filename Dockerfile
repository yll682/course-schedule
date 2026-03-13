FROM python:3.11-slim

# 非 root 用户运行，降低容器风险
RUN adduser --disabled-password --gecos '' appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 5000

# 生产环境用 Gunicorn（单 worker，保证后台线程唯一）
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "60", "--access-logfile", "-", "server:app"]
