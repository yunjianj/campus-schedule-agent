# -*- coding: utf-8 -*-
"""
Dify 代码节点 · 冲突检测与空闲时段推荐

为什么不交给大模型：
区间重叠判断是确定性计算，用代码做结果可复现、可解释，答辩时能直接跑用例演示。
模型只负责把结果说成人话。

输入：
    new_event_json       : validate_event 节点输出的 event，或直接传抽取 JSON
    existing_events_json : 当日已有日程数组，字段 event_date / start_time / end_time / title
    window_start/end     : 可安排时段，默认 08:00–22:00

输出：
    has_conflict : 是否冲突
    conflicts    : 冲突的已有日程（含 id / title / 起止）
    suggestions  : 当天空闲且足够长的时段，按接近原定时间排序
    message      : 供 LLM 组织语言的结构化描述
"""

from __future__ import annotations

import json

DEFAULT_WINDOW = ("08:00", "22:00")


def _to_minutes(value) -> int:
    """'15:00' / '15:00:00' -> 900"""
    parts = str(value).strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return hour * 60 + minute


def _to_hhmm(minutes: int) -> str:
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def _norm_date(value) -> str:
    return str(value).strip()[:10]


def _get(event: dict, *keys, default=None):
    for key in keys:
        if key in event and event[key] not in (None, ""):
            return event[key]
    return default


def find_conflicts(new_event: dict, existing: list) -> list:
    """返回与 new_event 时间重叠的已有日程。"""
    target_date = _norm_date(_get(new_event, "date", "event_date"))
    new_start = _to_minutes(_get(new_event, "start_time", "start"))
    new_end = _to_minutes(_get(new_event, "end_time", "end"))

    conflicts = []
    for item in existing or []:
        if _norm_date(_get(item, "date", "event_date")) != target_date:
            continue
        start = _to_minutes(_get(item, "start_time", "start"))
        end = _to_minutes(_get(item, "end_time", "end"))
        # 半开区间重叠：start < other_end 且 other_start < end
        if new_start < end and start < new_end:
            conflicts.append({
                "id": _get(item, "id"),
                "title": _get(item, "title", default="未命名日程"),
                "start": _to_hhmm(start),
                "end": _to_hhmm(end),
                "location": _get(item, "location", default=""),
            })
    return conflicts


def free_slots(new_event: dict, existing: list, window_start="08:00", window_end="22:00",
               max_suggestions=3) -> list:
    """在 window 内找出长度 >= 新日程时长的空闲段。"""
    target_date = _norm_date(_get(new_event, "date", "event_date"))
    new_start = _to_minutes(_get(new_event, "start_time", "start"))
    new_end = _to_minutes(_get(new_event, "end_time", "end"))
    duration = new_end - new_start
    if duration <= 0:
        return []

    win_start = _to_minutes(window_start)
    win_end = _to_minutes(window_end)

    busy = []
    for item in existing or []:
        if _norm_date(_get(item, "date", "event_date")) != target_date:
            continue
        start = max(win_start, _to_minutes(_get(item, "start_time", "start")))
        end = min(win_end, _to_minutes(_get(item, "end_time", "end")))
        if start < end:
            busy.append((start, end))
    busy.sort()

    merged = []
    for start, end in busy:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    gaps = []
    cursor = win_start
    for start, end in merged:
        if start - cursor >= duration:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if win_end - cursor >= duration:
        gaps.append((cursor, win_end))

    # 按“离原定开始时间最近”排序，同等距离优先靠后（给用户缓冲）
    gaps.sort(key=lambda g: (abs(g[0] - new_start), -g[0]))
    return [
        {"date": target_date, "start": _to_hhmm(s), "end": _to_hhmm(s + duration)}
        for s, _ in gaps[:max_suggestions]
    ]


def main(new_event_json: str, existing_events_json: str,
         window_start: str = DEFAULT_WINDOW[0], window_end: str = DEFAULT_WINDOW[1],
         max_suggestions: int = 3) -> dict:
    try:
        new_event = json.loads(new_event_json) if isinstance(new_event_json, str) else dict(new_event_json)
        existing = json.loads(existing_events_json) if isinstance(existing_events_json, str) else list(existing_events_json)
    except (TypeError, ValueError) as exc:
        return {
            "has_conflict": False,
            "conflicts": [],
            "suggestions": [],
            "message": "冲突检测输入解析失败：%s" % exc,
        }

    conflicts = find_conflicts(new_event, existing)
    suggestions = free_slots(new_event, existing, window_start, window_end, max_suggestions)

    if conflicts:
        parts = ["与「%s」冲突（%s–%s）" % (c["title"], c["start"], c["end"]) for c in conflicts]
        message = "；".join(parts)
        if suggestions:
            message += "。当日可调整时段：" + "、".join(
                "%s–%s" % (s["start"], s["end"]) for s in suggestions
            )
        else:
            message += "。当日已无足够空档，建议改期。"
    else:
        message = "与当日已有日程没有时间冲突。"

    return {
        "has_conflict": bool(conflicts),
        "conflicts": conflicts,
        "suggestions": suggestions,
        "message": message,
    }


if __name__ == "__main__":
    new = {"date": "2026-10-30", "start_time": "15:00", "end_time": "16:00"}
    existing = [
        {"id": "1", "title": "高等数学", "event_date": "2026-10-30",
         "start_time": "14:00:00", "end_time": "15:30:00"},
        {"id": "2", "title": "社团例会", "event_date": "2026-10-30",
         "start_time": "19:00:00", "end_time": "20:00:00"},
    ]
    print(json.dumps(main(json.dumps(new), json.dumps(existing)), ensure_ascii=False, indent=2))
