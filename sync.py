#!/usr/bin/env python3
import json
import sys
from datetime import datetime
from pathlib import Path

def sync():
    src = Path('参考文件/完整课表示例.json')
    dst = Path('course_data.json')

    try:
        if not src.exists():
            print(f'错误: 源文件不存在 {src}', file=sys.stderr)
            return 1

        data = json.loads(src.read_text(encoding='utf-8'))
        data['metadata']['提取时间'] = datetime.now().isoformat()

        dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'同步成功: {data.get("统计信息", {}).get("总课程数", "未知")}门课程')
        return 0

    except json.JSONDecodeError as e:
        print(f'错误: JSON 解析失败 {e}', file=sys.stderr)
        return 1
    except Exception as e:
        print(f'错误: {e}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(sync())
