# -*- coding: utf-8 -*-
"""
Dify 代码节点 · 日程抽取结果的校验与归一化

职责边界（答辩时可以直接讲）：
1. 只做「校验 + 格式换算」，绝不补全用户没有提供的信息；
2. 相对时间（明天 / 下周三）在这里兜底换算，避免模型算错；
3. 缺失字段以 missing 列表返回，交给追问节点，不在代码里默认填值。

输入：
    extracted_json : LLM 节点输出的 JSON 字符串
    now_iso        : 当前时间，ISO 格式，例如 2026-10-29T14:45:00+08:00

输出：
    ok             : 字段齐全且无错误
    missing        : 缺失的必填字段名
    missing_labels : 缺失字段的中文名（直接拿去追问）
    errors         : 格式错误说明
    event          : 归一化后的日程（date=YYYY-MM-DD，时间=HH:MM）
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

# 必填字段：按赛题要求，地点也算信息，缺失要追问；
# 但「待定 / 线上 / 未定」视为用户已明确，不算缺失。
REQUIRED_FIELDS = ("title", "date", "start_time", "end_time", "location")

FIELD_LABELS = {
    "title": "活动名称",
    "date": "日期",
    "start_time": "开始时间",
    "end_time": "结束时间",
    "location": "地点",
    "category": "类别",
}

LOCATION_UNKNOWN = {"待定", "未定", "线上", "线上会议", "暂无", "tbd", "unknown"}

WEEKDAY_INDEX = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6,
}

DAY_WORDS = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2, "大后天": 3}

CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

CN_NUM_PATTERN = r"[0-9]{1,2}|[零一二两三四五六七八九十]{1,3}"


def _cn_to_int(text: str) -> int:
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = CN_DIGITS.get(left, 1) if left else 1
        ones = CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return CN_DIGITS[text]


# --------------------------------------------------------------------------- #
# 日期
# --------------------------------------------------------------------------- #
def _resolve_date(value, today: date):
    """返回 (date | None, error | None)。支持相对日期和常见数字格式。"""
    if value is None:
        return None, None
    s = str(value).strip()
    if not s:
        return None, None

    # 今天 / 明天 / 后天 / 大后天
    if s in DAY_WORDS:
        return today + timedelta(days=DAY_WORDS[s]), None

    # 周三 / 本周三 / 下周三 / 下下周三
    m = re.fullmatch(r"(这|本|下|下下)?(?:周|星期|礼拜)([一二三四五六日天1-7])", s)
    if m:
        prefix = m.group(1) or ""
        idx = WEEKDAY_INDEX[m.group(2)]
        week_start = today - timedelta(days=today.weekday())
        week_offset = {"": 0, "这": 0, "本": 0, "下": 1, "下下": 2}[prefix]
        target = week_start + timedelta(days=idx, weeks=week_offset)
        # 只说“周三”时，取下一个不早于今天的周三
        if not prefix and target < today:
            target += timedelta(days=7)
        return target, None

    # 2026-10-30 / 2026/10/30 / 2026年10月30日 / 10-30 / 10月30日
    normalized = (
        s.replace("年", "-").replace("月", "-").replace("日", "")
        .replace("/", "-").replace(".", "-").replace(" ", "")
    )
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", normalized)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), None
        except ValueError:
            return None, "日期不合法：%s" % value

    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", normalized)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            candidate = date(today.year, month, day)
        except ValueError:
            return None, "日期不合法：%s" % value
        if candidate < today:  # 只说月日且已过去，视为明年
            candidate = date(today.year + 1, month, day)
        return candidate, None

    return None, "无法识别的日期：%s" % value


# --------------------------------------------------------------------------- #
# 时间
# --------------------------------------------------------------------------- #
def _resolve_time(value):
    """返回 (HH:MM | None, error | None)。"""
    if value is None:
        return None, None
    s = str(value).strip().lower().replace("：", ":").replace(" ", "")
    if not s:
        return None, None

    # 先剥离“下午”这类时段前缀，剩下的部分统一解析
    period = ""
    m = re.match(r"^(上午|早上|早晨|中午|下午|晚上|傍晚)", s)
    if m:
        period = m.group(1)
        s = s[len(period):]

    hour = minute = None

    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?(am|pm)?", s)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        if m.group(3) == "am" and hour == 12:
            hour = 0
    else:
        m = re.fullmatch(
            r"(%s)(?:点|时)(半|\d{1,2}分?|[零一二两三四五六七八九十]{1,3}分?)?" % CN_NUM_PATTERN,
            s,
        )
        if not m:
            return None, "无法识别的时间：%s" % value
        try:
            hour = _cn_to_int(m.group(1))
        except KeyError:
            return None, "无法识别的时间：%s" % value
        tail = m.group(2) or ""
        if tail == "半":
            minute = 30
        elif tail:
            try:
                minute = _cn_to_int(tail.replace("分", ""))
            except KeyError:
                return None, "无法识别的时间：%s" % value
        else:
            minute = 0

    if period in ("下午", "晚上", "傍晚") and hour < 12:
        hour += 12
    elif period == "中午" and hour < 11:
        hour += 12
    elif not period and 1 <= hour <= 6:
        # 只说“3点”时，不替用户猜上午还是下午，交给追问
        return None, "时间有歧义（上午还是下午？）：%s" % value

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, "时间不合法：%s" % value
    return "%02d:%02d" % (hour, minute), None


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def _clean_json_text(text) -> str:
    """剥掉推理模型的思考段、Markdown 代码块和前后解释，只留 JSON。"""
    s = str(text or "").strip()
    # DeepSeek 等推理模型的思考标签
    s = re.sub(r"<think\b[^>]*>.*?</think>", "", s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r"</?think\b[^>]*>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.DOTALL)
    # Markdown 代码块
    s = re.sub(r"^```(?:json)?\s*", "", s.strip(), flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    # 去掉 JSON 前后的客套话，只取最后一个完整的 {...}
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        s = s[start:end + 1]
    return s.strip()


def _parse_json_text(text):
    """尽量从模型输出里解析出一个 JSON 对象，失败返回 None。"""
    cleaned = _clean_json_text(text)
    if not cleaned:
        return None
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except (TypeError, ValueError):
        pass
    # 兜底：从后往前找能单独解析的 {...}
    for index in range(len(cleaned) - 1, -1, -1):
        if cleaned[index] == "{":
            try:
                obj = json.loads(cleaned[index:])
            except (TypeError, ValueError):
                continue
            if isinstance(obj, dict):
                return obj
    return None


def main(extracted_json: str, now_iso: str = "", user_id: str = "demo") -> dict:
    raw_text = str(extracted_json or "")
    raw = _parse_json_text(raw_text)
    if raw is None:
        return {
            "ok": False,
            "missing": [],
            "missing_labels": [],
            "errors": ["抽取结果不是合法 JSON"],
            "event": {},
            "event_json": "{}",
            "row_json": "",
            "raw_text": raw_text[:500],
        }

    if now_iso:
        try:
            today = datetime.fromisoformat(now_iso).date()
        except ValueError:
            today = date.today()
    else:
        today = date.today()

    missing = []
    for field in REQUIRED_FIELDS:
        value = raw.get(field)
        if value is None or str(value).strip() == "" or str(value).strip() == "null":
            missing.append(field)
    location = str(raw.get("location") or "").strip()
    if location and location.lower() in LOCATION_UNKNOWN and "location" in missing:
        missing.remove("location")

    errors = []
    event = {}

    title = str(raw.get("title") or "").strip()
    if title:
        event["title"] = title

    parsed_date, err = _resolve_date(raw.get("date"), today)
    if err:
        errors.append(err)
    elif parsed_date:
        event["date"] = parsed_date.isoformat()

    start, err = _resolve_time(raw.get("start_time"))
    if err:
        errors.append(err)
    elif start:
        event["start_time"] = start

    end, err = _resolve_time(raw.get("end_time"))
    if err:
        errors.append(err)
    elif end:
        event["end_time"] = end

    if start and end and end <= start:
        errors.append("结束时间必须晚于开始时间（%s → %s）" % (start, end))

    if location:
        event["location"] = location

    category = str(raw.get("category") or "").strip()
    event["category"] = category or "其他"

    if "start_time" in event and "end_time" in event:
        h1, m1 = map(int, event["start_time"].split(":"))
        h2, m2 = map(int, event["end_time"].split(":"))
        event["duration_min"] = (h2 * 60 + m2) - (h1 * 60 + m1)

    row = {
        "user_id": user_id,
        "title": event.get("title", ""),
        "event_date": event.get("date"),
        "start_time": event.get("start_time"),
        "end_time": event.get("end_time"),
        "location": event.get("location", ""),
        "category": event.get("category", "其他"),
        "note": "",
        "source": "agent",
        "status": "planned",
    }

    return {
        "ok": not missing and not errors,
        "missing": missing,
        "missing_labels": [FIELD_LABELS.get(f, f) for f in missing],
        "errors": errors,
        "event": event,
        "event_json": json.dumps(event, ensure_ascii=False),
        "row_json": json.dumps(row, ensure_ascii=False),
        "raw_text": raw_text[:500],
    }


if __name__ == "__main__":
    demo = {
        "title": "和导师开会",
        "date": "明天",
        "start_time": "下午三点",
        "end_time": None,
        "location": "教三201",
        "category": "会议",
    }
    print(json.dumps(main(json.dumps(demo, ensure_ascii=False), "2026-10-29T14:45:00+08:00"),
                     ensure_ascii=False, indent=2))
