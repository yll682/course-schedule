#!/usr/bin/env bash
# 由 deploy.sh 生成 deploy.cn.sh（国内版）：
# - clone/fetch 首选 CNB，失败回退 GitHub（与原脚本「先 GitHub 后加速」对称对调）
# - 提示文案与文件头注释同步调整
# 每次修改 deploy.sh 后重新运行本脚本，保持两版一致。
set -euo pipefail
cd "$(dirname "$0")"

python3 - <<'PYEOF'
import pathlib
s = pathlib.Path("deploy.sh").read_text(encoding="utf-8")

s = s.replace(
    '#   bash <(curl -fsSL https://raw.githubusercontent.com/yll682/course-schedule/master/deploy.sh)',
    '#   bash <(curl -fsSL https://cnb.cool/yll682/course-schedule/-/git/raw/master/deploy.cn.sh)', 1)
s = s.replace(
    '#   bash <(curl -fsSL https://cnb.cool/yll682/course-schedule/-/git/raw/master/deploy.sh)',
    '#   bash <(curl -fsSL https://raw.githubusercontent.com/yll682/course-schedule/master/deploy.sh)', 1)
s = s.replace(
    'REPO_URL="https://github.com/yll682/course-schedule.git"',
    'REPO_URL="https://cnb.cool/yll682/course-schedule.git"', 1)
s = s.replace(
    'REPO_URL_CN="https://cnb.cool/yll682/course-schedule.git"',
    'REPO_URL_CN="https://github.com/yll682/course-schedule.git"', 1)
s = s.replace('尝试 EdgeOne 加速', '尝试国际线路（GitHub）')
s = s.replace('#   国内加速：', '#   国际线路：', 1)

pathlib.Path("deploy.cn.sh").write_text(s, encoding="utf-8")
print("generated deploy.cn.sh")
PYEOF

chmod +x deploy.cn.sh
bash -n deploy.cn.sh && echo "syntax OK"