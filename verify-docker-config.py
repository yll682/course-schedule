#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Docker 配置验证脚本
检查所有必需文件和配置是否正确
"""

import os
import sys
from pathlib import Path

# 颜色输出
class Colors:
    GREEN = '\033[0;32m'
    RED = '\033[0;31m'
    YELLOW = '\033[1;33m'
    NC = '\033[0m'  # No Color

def print_ok(msg):
    print(f"{Colors.GREEN}[OK]{Colors.NC} {msg}")

def print_err(msg):
    print(f"{Colors.RED}[ERROR]{Colors.NC} {msg}")

def print_warn(msg):
    print(f"{Colors.YELLOW}[WARN]{Colors.NC} {msg}")

def check_file(filepath, required=True):
    """检查文件是否存在"""
    path = Path(filepath)
    if path.exists():
        print_ok(f"文件存在: {filepath}")
        return True
    else:
        if required:
            print_err(f"文件缺失: {filepath}")
        else:
            print_warn(f"可选文件不存在: {filepath}")
        return False

def check_env_file():
    """检查 .env 文件配置"""
    if not Path('.env').exists():
        print_err(".env 文件不存在")
        print("  请运行: cp .env.example .env")
        return False

    print_ok(".env 文件存在")

    # 检查必需的环境变量
    with open('.env', 'r', encoding='utf-8') as f:
        content = f.read()

    required_vars = {
        'STORAGE_AES_KEY': '密码加密密钥',
    }

    optional_vars = {
        'SECRET_KEY': 'Session 密钥',
        'ADMIN_USERS': '管理员账号',
        'PORT': '监听端口',
    }

    all_ok = True

    for var, desc in required_vars.items():
        if f'{var}=' in content and 'change-this' not in content:
            print_ok(f"环境变量已设置: {var} ({desc})")
        else:
            print_err(f"环境变量未设置或使用默认值: {var} ({desc})")
            all_ok = False

    for var, desc in optional_vars.items():
        if f'{var}=' in content:
            print_ok(f"可选环境变量已设置: {var} ({desc})")

    return all_ok

def check_dockerfile():
    """检查 Dockerfile 内容"""
    with open('Dockerfile', 'r', encoding='utf-8') as f:
        content = f.read()

    checks = [
        ('FROM python:3.11', '基础镜像'),
        ('EXPOSE 5000', '端口暴露'),
        ('gunicorn', 'Gunicorn 启动命令'),
        ('HEALTHCHECK', '健康检查'),
    ]

    all_ok = True
    for pattern, desc in checks:
        if pattern in content:
            print_ok(f"Dockerfile 包含: {desc}")
        else:
            print_err(f"Dockerfile 缺少: {desc}")
            all_ok = False

    return all_ok

def check_docker_compose():
    """检查 docker-compose.yml 内容"""
    with open('docker-compose.yml', 'r', encoding='utf-8') as f:
        content = f.read()

    checks = [
        ('version:', '版本声明'),
        ('course-schedule:', '服务定义'),
        ('volumes:', '数据持久化'),
        ('restart:', '重启策略'),
        ('STORAGE_AES_KEY', '环境变量引用'),
    ]

    all_ok = True
    for pattern, desc in checks:
        if pattern in content:
            print_ok(f"docker-compose.yml 包含: {desc}")
        else:
            print_err(f"docker-compose.yml 缺少: {desc}")
            all_ok = False

    return all_ok

def main():
    print("=" * 40)
    print("  Docker Configuration Validator")
    print("=" * 40 + "\n")

    all_checks_passed = True

    # 检查必需文件
    print("\n[1/5] 检查必需文件...")
    required_files = [
        'Dockerfile',
        'docker-compose.yml',
        'requirements.txt',
        '.env.example',
        'server.py',
        'jw_client.py',
    ]

    for filepath in required_files:
        if not check_file(filepath):
            all_checks_passed = False

    # 检查可选文件
    print("\n[2/5] 检查可选文件...")
    optional_files = [
        '.dockerignore',
        'DOCKER.md',
        'docker-deploy.sh',
    ]

    for filepath in optional_files:
        check_file(filepath, required=False)

    # 检查 .env 文件
    print("\n[3/5] 检查环境变量配置...")
    if not check_env_file():
        all_checks_passed = False

    # 检查 Dockerfile
    print("\n[4/5] 检查 Dockerfile...")
    if not check_dockerfile():
        all_checks_passed = False

    # 检查 docker-compose.yml
    print("\n[5/5] 检查 docker-compose.yml...")
    if not check_docker_compose():
        all_checks_passed = False

    # 总结
    print("\n" + "=" * 40)
    if all_checks_passed:
        print(f"{Colors.GREEN}[SUCCESS] All checks passed!{Colors.NC}")
        print("\nReady to deploy with:")
        print("  docker-compose up -d --build")
        sys.exit(0)
    else:
        print(f"{Colors.RED}[FAILED] Configuration issues found{Colors.NC}")
        print("\nCommon fixes:")
        print("  1. Copy config: cp .env.example .env")
        print("  2. Edit config: nano .env")
        print("  3. Generate key: python -c \"import secrets; print(secrets.token_hex(16))\"")
        sys.exit(1)

if __name__ == '__main__':
    main()
