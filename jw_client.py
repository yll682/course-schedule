"""
教务系统 API 客户端
逆向分析详情见《教务系统完整逆向分析报告.md》

依赖：pycryptodome（pip install pycryptodome）
"""
import base64
import json
import os
import requests
from datetime import datetime
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

BASE_URL = "http://59.57.242.167:81/njwhd"

# 外部系统加密密钥（通过环境变量配置，如果未设置则使用默认值）
# 注意：此密钥用于与教务系统 API 交互，非本地加密使用
# 生产环境强烈建议设置环境变量 JW_API_KEY
_jw_api_key_raw = os.environ.get('JW_API_KEY', 'qzkj1kjghd=876&*')
AES_KEY = _jw_api_key_raw.encode()[:16]

# 本地存储加密使用独立密钥（必须通过环境变量设置，未设置则启动失败）
_storage_key_raw = os.environ.get('STORAGE_AES_KEY')
if not _storage_key_raw:
    raise RuntimeError(
        "环境变量 STORAGE_AES_KEY 未设置。"
        "请生成一个随机密钥并设置该变量，例如：\n"
        "  python -c \"import secrets; print(secrets.token_hex(16))\""
    )
STORAGE_KEY = _storage_key_raw.encode()[:16]

WEEKDAY_MAP = {
    "1": "周一", "2": "周二", "3": "周三", "4": "周四",
    "5": "周五", "6": "周六", "0": "周日",
}


# ── 密码加密（发送给 JW 系统） ─────────────────────────────────────────────────

def encrypt_password(password: str) -> str:
    """复现 window.btoa(Uw.encrypt(password))：AES-ECB + 双层 Base64"""
    plaintext  = json.dumps(password).encode("utf-8")   # JSON.stringify 带引号
    ciphertext = AES.new(AES_KEY, AES.MODE_ECB).encrypt(pad(plaintext, 16))
    aes_b64    = base64.b64encode(ciphertext).decode()  # CryptoJS .toString()
    return base64.b64encode(aes_b64.encode()).decode()   # window.btoa()


# ── 本地存储加密（DB 中不明文存密码，AES-GCM） ────────────────────────────────

def encrypt_for_storage(password: str) -> str:
    """AES-GCM 加密，格式：'gcm:' + base64(iv + ciphertext + tag)"""
    iv = os.urandom(16)
    cipher = AES.new(STORAGE_KEY, AES.MODE_GCM, nonce=iv)
    ciphertext, tag = cipher.encrypt_and_digest(password.encode("utf-8"))
    return "gcm:" + base64.b64encode(iv + ciphertext + tag).decode()


def decrypt_from_storage(enc: str) -> str:
    """自动兼容旧 ECB 格式和新 GCM 格式"""
    if enc.startswith("gcm:"):
        raw = base64.b64decode(enc[4:])
        iv, ciphertext, tag = raw[:16], raw[16:-16], raw[-16:]
        cipher = AES.new(STORAGE_KEY, AES.MODE_GCM, nonce=iv)
        return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
    # 兼容旧 ECB 格式
    ciphertext = base64.b64decode(enc)
    return unpad(AES.new(STORAGE_KEY, AES.MODE_ECB).decrypt(ciphertext), 16).decode("utf-8")


# ── 登录 ──────────────────────────────────────────────────────────────────────

def login(username: str, password: str) -> dict:
    """
    登录教务系统，返回包含 token 的用户信息 dict。
    Token 有效期约 4 小时，过期后重新调用即可。
    """
    # 必须先查一次验证码配置，否则部分环境下登录失败
    requests.post(f"{BASE_URL}/retrievePwd", params={"type": "cx"}, timeout=(5, 10))

    resp = requests.post(
        f"{BASE_URL}/login",
        params={
            "userNo":      username,
            "pwd":         encrypt_password(password),
            "encode":      "1",
            "captchaData": "",
            "codeVal":     "",
        },
        timeout=(5, 10),
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != "1" or not data.get("data", {}).get("token"):
        raise RuntimeError(data.get("Msg") or "登录失败，请检查学号和密码")

    return data["data"]  # {token, name, userNo, clsName, academyName, ...}


# ── 课表相关接口 ───────────────────────────────────────────────────────────────

def _session(token: str) -> requests.Session:
    sess = requests.Session()
    sess.headers["token"] = token  # 非标准 header，不是 Authorization
    return sess


def get_kbjcmsid(token: str) -> str:
    """获取当前学期主校历 ID（取 mrms='1' 的那条）"""
    resp = _session(token).post(f"{BASE_URL}/Get_sjkbms", timeout=(5, 10))
    resp.raise_for_status()
    data = resp.json().get("data", [])
    for x in data:
        if x.get("mrms") == "1":
            return x["kbjcmsid"]
    raise RuntimeError("未找到默认学期校历（mrms='1'），请联系管理员")


def get_timetable_raw(token: str, week, kbjcmsid: str) -> dict:
    """
    获取指定周原始课表，返回 data[0]。
    week=None 时不传 week 参数，服务端返回当前周。
    注意：week 必须配合 kbjcmsid，否则 week 参数被服务端忽略。
    """
    params = {"kbjcmsid": kbjcmsid}
    if week is not None:
        params["week"] = week
    resp = _session(token).post(
        f"{BASE_URL}/student/curriculum",
        params=params,
        timeout=(5, 10),
    )
    resp.raise_for_status()
    return resp.json()["data"][0]


# ── 数据转换 ──────────────────────────────────────────────────────────────────

def _parse_class_time(class_time: str):
    """
    解析 classTime 编码字段。
    '10304' → (1, 3, 4)  周一第3-4节
    '2070809' → (2, 7, 9) 周二第7-9节
    返回：(weekday_id, start_node, end_node)
    """
    nodes = [int(class_time[i:i+2]) for i in range(1, len(class_time), 2)]
    return int(class_time[0]), nodes[0], nodes[-1]


def transform_timetable(raw: dict, user_info: dict, week: int) -> dict:
    """
    将 /njwhd/student/curriculum 的原始响应转换为前端可用格式。

    user_info: {name, userNo, clsName}（来自登录响应或 DB）
    week: 实际周次整数（用于 metadata）
    """
    top   = raw["topInfo"][0]
    dates = {d["xqid"]: d["mxrq"] for d in raw["date"]}

    # 按 weekDay 分组课程
    by_day: dict[str, list] = {}
    for c in raw.get("courses", []):
        wd = c["weekDay"]
        _, start, end = _parse_class_time(c["classTime"])
        by_day.setdefault(wd, []).append({
            "course_name": c["courseName"],
            "teacher":     c["teacherName"],
            "location":    c.get("location") or c.get("classroomName", ""),
            "weeks":       c.get("classWeek", ""),
            "time_slots": {
                "start_slot": start,
                "duration":   end - start + 1,
                "display":    f'{c["startTime"]}-{c["endTIme"]}',
            },
        })

    # 每天按节次升序
    for lst in by_day.values():
        lst.sort(key=lambda x: x["time_slots"]["start_slot"])

    # 周一(1)→周日(0) 顺序
    week_list = []
    for wid in ["1", "2", "3", "4", "5", "6", "0"]:
        week_list.append({
            "weekday":    wid,
            "weekday_cn": WEEKDAY_MAP[wid],
            "date":       dates.get(wid, ""),
            "courses":    by_day.get(wid, []),
        })

    return {
        "metadata": {
            "用户":         f'{user_info.get("name", "")} ({user_info.get("userNo", "")})',
            "班级":         user_info.get("clsName", ""),
            "周次":         f'第{week}周',
            "提取时间":     datetime.now().isoformat(),
            "current_week": int(top.get("week", week)),
            "max_week":     int(top.get("maxWeek", 19)),
            "today":        top.get("today", ""),
        },
        "完整课表": week_list,
    }
