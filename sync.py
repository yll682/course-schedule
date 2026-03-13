#!/usr/bin/env python3
import json
from datetime import datetime
from pathlib import Path

def sync():
    src = Path('参考文件/完整课表示例.json')
    dst = Path('course_data.json')

    data = json.loads(src.read_text(encoding='utf-8'))
    data['metadata']['提取时间'] = datetime.now().isoformat()

    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'同步成功: {data["统计信息"]["总课程数"]}门课程')

if __name__ == '__main__':
    sync()
