#!/bin/bash

# 初始化git仓库
if [ ! -d .git ]; then
    git init
    git add .
    git commit -m "Initial commit: 第三方课表客户端"
fi

# 运行课表同步
python course_sync.py

# 如果有变化则提交并推送
if [ -f course_data.json ]; then
    git add course_data.json course_sync.log
    if git diff --staged --quiet; then
        echo "没有变化"
    else
        git commit -m "更新课表数据 $(date '+%Y-%m-%d %H:%M:%S')"
        git push origin main
    fi
fi
