# -*- coding: utf-8 -*-
"""本地回归测试：不依赖 Dify，直接跑两个代码节点的逻辑。"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "code"))

import conflict  # noqa: E402
import validate_event  # noqa: E402

NOW = "2026-10-29T14:45:00+08:00"  # 2026-10-29 是周四
PASS = 0


def check(name, condition, detail=""):
    global PASS
    if not condition:
        raise AssertionError("%s 失败 %s" % (name, detail))
    PASS += 1
    print("  ok  %s" % name)


def run_validate(payload):
    return validate_event.main(json.dumps(payload, ensure_ascii=False), NOW)


print("== validate_event ==")

# 1. 信息完整
full = run_validate({
    "title": "和导师开会", "date": "2026-10-30", "start_time": "15:00",
    "end_time": "16:00", "location": "教三201", "category": "会议",
})
check("信息完整时 ok=True", full["ok"] is True, full)
check("时长计算正确", full["event"]["duration_min"] == 60, full["event"])

# 2. 缺结束时间和地点 -> 追问，且不补全
partial = run_validate({
    "title": "和导师开会", "date": "明天", "start_time": "下午三点",
    "end_time": None, "location": None, "category": "会议",
})
check("缺失字段被识别", set(partial["missing"]) == {"end_time", "location"}, partial)
check("缺失时 ok=False", partial["ok"] is False)
check("未擅自补全结束时间", "end_time" not in partial["event"], partial["event"])
check("明天换算为 2026-10-30", partial["event"]["date"] == "2026-10-30", partial["event"])
check("下午三点换算为 15:00", partial["event"]["start_time"] == "15:00", partial["event"])

# 3. 下周三
next_wed = run_validate({
    "title": "讲座", "date": "下周三", "start_time": "19:00",
    "end_time": "20:30", "location": "图书馆报告厅", "category": "讲座",
})
check("下周三 = 2026-11-04", next_wed["event"]["date"] == "2026-11-04", next_wed["event"])

# 4. 只说“周三” -> 取最近的周三
wed = run_validate({
    "title": "社团", "date": "周三", "start_time": "19:00",
    "end_time": "20:00", "location": "活动中心", "category": "社团",
})
check("周三 = 2026-11-04", wed["event"]["date"] == "2026-11-04", wed["event"])

# 5. 时间有歧义 -> 报错，不猜
ambiguous = run_validate({
    "title": "开会", "date": "2026-10-30", "start_time": "三点",
    "end_time": "四点", "location": "教三", "category": "会议",
})
check("3 点被判定为歧义", any("歧义" in e for e in ambiguous["errors"]), ambiguous)

# 6. 结束早于开始
reversed_time = run_validate({
    "title": "开会", "date": "2026-10-30", "start_time": "16:00",
    "end_time": "15:00", "location": "教三", "category": "会议",
})
check("结束早于开始被拦截", any("晚于" in e for e in reversed_time["errors"]), reversed_time)

# 7. 地点写“待定”视为已提供
tbd = run_validate({
    "title": "线上分享", "date": "2026-10-31", "start_time": "20:00",
    "end_time": "21:00", "location": "待定", "category": "讲座",
})
check("地点待定不算缺失", "location" not in tbd["missing"], tbd)


# 8. 模型把 JSON 包在 ```json 代码块里
fenced = validate_event.main(
    '```json\n{"title":"讲座","date":"2026-10-30","start_time":"19:00",'
    '"end_time":"20:00","location":"报告厅","category":"讲座"}\n```',
    NOW,
)
check("能剥掉 Markdown 代码块", fenced["ok"] is True, fenced)

# 9. 推理模型输出带 <think> 思考段
think = validate_event.main(
    "<think>用户说 {不是合法json} 明天开会</think>"
    '{"title":"讲座","date":"2026-10-30","start_time":"19:00",'
    '"end_time":"20:00","location":"报告厅","category":"讲座"}',
    NOW,
)
check("能剥掉 think 思考段", think["ok"] is True, think)

print("== conflict ==")
existing = [
    {"id": "1", "title": "高等数学", "event_date": "2026-10-30",
     "start_time": "14:00:00", "end_time": "15:30:00", "location": "教二101"},
    {"id": "2", "title": "社团例会", "event_date": "2026-10-30",
     "start_time": "19:00:00", "end_time": "20:00:00", "location": "活动中心"},
    {"id": "3", "title": "别的日子", "event_date": "2026-10-31",
     "start_time": "15:00:00", "end_time": "16:00:00"},
]

conflicted = conflict.main(
    json.dumps({"date": "2026-10-30", "start_time": "15:00", "end_time": "16:00"}),
    json.dumps(existing),
)
check("检出与高数的冲突", conflicted["has_conflict"] is True, conflicted)
check("只报冲突的那一条", [c["id"] for c in conflicted["conflicts"]] == ["1"], conflicted["conflicts"])
check("给出空闲时段建议", len(conflicted["suggestions"]) > 0, conflicted["suggestions"])
check("最近建议是 15:30", conflicted["suggestions"][0]["start"] == "15:30", conflicted["suggestions"])

no_conflict = conflict.main(
    json.dumps({"date": "2026-10-30", "start_time": "16:00", "end_time": "17:00"}),
    json.dumps(existing),
)
check("16:00 不冲突", no_conflict["has_conflict"] is False, no_conflict)

edge = conflict.main(
    json.dumps({"date": "2026-10-30", "start_time": "15:30", "end_time": "16:30"}),
    json.dumps(existing),
)
check("首尾相接不算冲突", edge["has_conflict"] is False, edge)

other_day = conflict.main(
    json.dumps({"date": "2026-10-31", "start_time": "15:00", "end_time": "16:00"}),
    json.dumps(existing),
)
check("跨日不误判", other_day["has_conflict"] is True, other_day)  # 10-31 有 id=3 的重叠

print("\n全部通过：%d 项" % PASS)
