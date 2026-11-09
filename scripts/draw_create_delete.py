# -*- coding: utf-8 -*-
"""录入与删除流程图"""

from PIL import Image, ImageDraw, ImageFont

W, H = 1800, 1000
BG = "#FFFFFF"
ARROW = "#94A3B8"
INK = "#0F172A"
MUTED = "#64748B"

FONT = "C:/Windows/Fonts/msyh.ttc"
FONT_BD = "C:/Windows/Fonts/msyhbd.ttc"


def f(size, bold=False):
    return ImageFont.truetype(FONT_BD if bold else FONT, size)


F_TITLE = f(32, True)
F_SUB = f(15)
F_TAG = f(16, True)
F_BOX = f(15, True)
F_BODY = f(12)
F_SMALL = f(12)

LANE = {
    "blue": ("#EFF6FF", "#3B82F6", "#1E3A8A"),
    "green": ("#ECFDF5", "#10B981", "#065F46"),
    "purple": ("#F5F3FF", "#8B5CF6", "#4C1D95"),
    "orange": ("#FFF7ED", "#F97316", "#9A3412"),
    "slate": ("#F8FAFC", "#94A3B8", "#334155"),
    "red": ("#FEF2F2", "#F87171", "#991B1B"),
}

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)


def text_size(t, font):
    box = d.textbbox((0, 0), t, font=font)
    return box[2] - box[0], box[3] - box[1]


def wrap(t, font, max_w):
    lines, cur = [], ""
    for ch in t:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if d.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def box(x, y, w, h, title, body="", lane="blue"):
    fill, border, ink = LANE[lane]
    d.rounded_rectangle([x, y, x + w, y + h], radius=11, fill=fill, outline=border, width=2)
    inner = w - 20
    tl = wrap(title, F_BOX, inner)
    bl = wrap(body, F_BODY, inner) if body else []
    total = len(tl) * 21 + len(bl) * 16
    cy = y + (h - total) / 2
    for ln in tl:
        tw, _ = text_size(ln, F_BOX)
        d.text((x + (w - tw) / 2, cy), ln, font=F_BOX, fill=ink)
        cy += 21
    for ln in bl:
        tw, _ = text_size(ln, F_BODY)
        d.text((x + (w - tw) / 2, cy), ln, font=F_BODY, fill=MUTED)
        cy += 16


def diamond(cx, cy, w, h, text):
    fill, border, ink = LANE["orange"]
    pts = [(cx, cy - h / 2), (cx + w / 2, cy), (cx, cy + h / 2), (cx - w / 2, cy)]
    d.polygon(pts, fill=fill, outline=border)
    d.line(pts + [pts[0]], fill=border, width=2)
    tw, th = text_size(text, F_BOX)
    d.text((cx - tw / 2, cy - th / 2), text, font=F_BOX, fill=ink)


def head_down(x, y):
    d.polygon([(x - 5, y - 9), (x + 5, y - 9), (x, y)], fill=ARROW)


def head_right(x, y):
    d.polygon([(x - 9, y - 5), (x - 9, y + 5), (x, y)], fill=ARROW)


def vline(x, y1, y2):
    d.line([(x, y1), (x, y2)], fill=ARROW, width=2)
    head_down(x, y2)


def hline(x1, x2, y):
    d.line([(x1, y), (x2, y)], fill=ARROW, width=2)


def elbow_down(x1, x2, y1, y2):
    d.line([(x1, y1), (x2, y1)], fill=ARROW, width=2)
    vline(x2, y1, y2)


d.text((50, 28), "录入与删除 · 两条主链路", font=F_TITLE, fill=INK)
d.text((52, 72), "每轮先跑全局快照（过去与未来各 7 天），再按意图分流", font=F_SUB, fill=MUTED)

# ================= 左：录入 =================
d.text((60, 108), "① 录入（intent = add）", font=F_TAG, fill="#2563EB")

box(170, 140, 280, 50, "意图识别 = add", lane="slate")
box(170, 220, 280, 52, "抽取日程（LLM）", "标题 / 日期 / 起止 / 地点 / 类别", lane="purple")
box(170, 302, 280, 52, "校验归一化（代码）", "结束时间缺省 +1h，地点可空", lane="blue")
diamond(310, 408, 280, 76, "信息齐全？")

vline(310, 190, 220)
vline(310, 272, 302)
vline(310, 354, 370)

# 否 -> 暂存 + 追问
elbow_down(170, 150, 408, 470)
d.text((116, 414), "否", font=F_SMALL, fill="#DC2626")
box(30, 470, 240, 54, "暂存草稿", "draft = row_json", lane="blue")
box(30, 548, 240, 52, "追问缺失项", "只问标题 / 日期 / 开始时间", lane="blue")
box(30, 624, 240, 42, "本轮结束", lane="slate")
vline(150, 524, 548)
vline(150, 600, 624)

# 是 -> 读当天 -> 冲突
elbow_down(450, 510, 408, 470)
d.text((470, 414), "是", font=F_SMALL, fill="#059669")
box(390, 470, 240, 52, "读取当天日程（HTTP）", lane="green")
box(390, 548, 240, 52, "冲突检测（代码）", "区间重叠判断", lane="blue")
diamond(510, 660, 240, 72, "有冲突？")
vline(510, 522, 548)
vline(510, 600, 624)

# 有冲突也保存
d.line([(390, 660), (410, 740)], fill=ARROW, width=2)
head_down(410, 740)
d.text((346, 692), "是", font=F_SMALL, fill="#DC2626")
d.line([(630, 660), (650, 740)], fill=ARROW, width=2)
head_down(650, 740)
d.text((662, 692), "否", font=F_SMALL, fill="#059669")
box(390, 740, 340, 54, "保存日程（HTTP POST）", "有冲突也保存，回复里带提示", lane="green")
box(390, 818, 340, 46, "清空草稿", lane="purple")
box(390, 888, 340, 46, "回复：摘要 + 冲突提示", lane="blue")
vline(560, 794, 818)
vline(560, 864, 888)

# ================= 右：删除 =================
d.text((960, 108), "② 删除（intent = delete）", font=F_TAG, fill="#7C3AED")

box(1030, 140, 280, 50, "意图识别 = delete", lane="slate")
box(1030, 220, 280, 54, "定位日程（代码）", "按标题 / 日期在快照里找", lane="blue")
diamond(1170, 336, 280, 76, "已确认删除？")

vline(1170, 190, 220)
vline(1170, 274, 298)

# 是 -> 删除
vline(1170, 374, 400)
d.text((1122, 380), "是", font=F_SMALL, fill="#DC2626")
box(1030, 400, 280, 54, "执行删除（HTTP DELETE）", "schedules?id=eq.xxx", lane="red")
box(1030, 478, 280, 50, "清空待删 ID", lane="purple")
box(1030, 552, 280, 42, "删除完成", lane="slate")
vline(1170, 454, 478)
vline(1170, 528, 552)

# 否 -> 回复匹配结果
elbow_down(1310, 1500, 336, 400)
d.text((1330, 340), "否", font=F_SMALL, fill="#059669")
box(1340, 400, 330, 92, "回复匹配结果",
    "1 条：请回“确认”\n多条：列出让用户说具体\n0 条：没找到", lane="blue")
box(1340, 512, 330, 42, "本轮结束", lane="slate")
vline(1505, 492, 512)

# 说明
d.text((60, 956), "录入：信息缺就追问并保存草稿；齐全就直接存，不确认；有冲突也保存，回复里带提示",
       font=F_SMALL, fill=MUTED)
d.text((960, 956), "删除：必须二次确认；确认后按 pending_delete 的 id 删除，并清空状态",
       font=F_SMALL, fill=MUTED)

img.save("campus-schedule-agent/diagrams/create-delete-flow.png", "PNG")
print("saved")
