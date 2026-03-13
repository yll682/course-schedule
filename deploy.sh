#!/bin/bash
python3 sync.py && git add -f course_data.json && git commit -m "更新 $(date +%Y-%m-%d)" && git push
