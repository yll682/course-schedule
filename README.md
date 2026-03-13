# 课表同步

## 部署
```bash
git clone https://github.com/yll682/course-schedule.git
cd course-schedule
```

## 运行
```bash
python3 sync.py
bash deploy.sh  # 自动推送
```

## 定时任务
```bash
crontab -e
# 每5分钟同步
*/5 * * * * cd /path/to/course-schedule && bash deploy.sh
```

## 移除
```bash
cd .. && rm -rf course-schedule
```
