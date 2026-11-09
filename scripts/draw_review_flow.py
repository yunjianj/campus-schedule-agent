# -*- coding: utf-8 -*-
"""复盘流程图"""

from PIL import Image, ImageDraw, ImageFont

W, H = 1700, 1250
BG = "#FFFFFF"
ARROW = "#94A3B8"
INK = "#0F172A"
MUTED = "#64748B"

FONT = "C:/Windows/Fonts/msyh.ttc"
FONT_BD = "C:/Windows/Fonts/msyhbd.ttc"


def f(size, bold=False):
    return ImageFont.truetype(FONT_BD if bold else FONT, size)


F_TITLE = f(34, True)
F_SUB = f(16)
F_TAG = f(15, True)
F_BOX = f(16, True)
F_BODY = f(12)
F_SMALL = f(12)

LANE = {
    "blue": ("#EFF6FF", "#3B82F6", "#1E3A8A"),
    "green": ("#ECFDF5", "#10B981", "#065F46"),
    "purple": ("#F5F3FF", "#8B5CF6", "#4C1D95"),
    "orange": ("#FFF7ED", "#F97316", "#9A3412"),
    "slate": ("#F8FAFC", "#94A3B8", "#334155"),
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
    d.rounded_rectangle([x, y, x + w, y + h], radius=12, fill=fill, outline=border, width=2)
    inner = w - 24
    tl = wrap(title, F_BOX, inner)
    bl = wrap(body, F_BODY, inner) if body else []
    total = len(tl) * 22 + len(bl) * 16
    cy = y + (h - total) / 2
    for ln in tl:
        tw, _ = text_size(ln, F_BOX)
        d.text((x + (w - tw) / 2, cy), ln, font=F_BOX, fill=ink)
        cy += 22
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


def hline(x1, x2, y, head=False):
    d.line([(x1, y), (x2, y)], fill=ARROW, width=2)
    if head:
        head_right(x2, y)


# 标题
d.text((50, 34), "复盘流程 · 日 / 周 / 月共用一套", font=F_TITLE, fill=INK)
d.text((52, 82), "范围跨度自动判定 period；一次复盘分两轮完成", font=F_SUB, fill=MUTED)

CX = 700
BW = 400

box(CX - BW / 2, 130, BW, 54, "意图分支", "review：开始复盘　review_feedback：回答复盘问题", lane="slate")
box(CX - BW / 2, 220, BW, 64, "复盘范围（代码）",
    "review_feedback 用存档范围；review 用意图日期，缺省今天", lane="blue")
box(CX - BW / 2, 320, BW, 58, "读取复盘范围（HTTP）", "该范围内的全部日程", lane="green")
box(CX - BW / 2, 414, BW, 58, "历史复盘（HTTP）", "同类型最近 3 条，limit=3", lane="green")
box(CX - BW / 2, 508, BW, 74, "复盘统计（代码）",
    "三档完成数 · 类别时长 · 按周聚合（月）", lane="blue")

diamond(CX, 630, 380, 76, "本轮意图是 review_feedback？")

vline(CX, 184, 220)
vline(CX, 284, 320)
vline(CX, 378, 414)
vline(CX, 472, 508)
vline(CX, 582, 592)

# 分支线
d.line([(CX, 668), (CX, 690)], fill=ARROW, width=2)
d.line([(300, 690), (1100, 690)], fill=ARROW, width=2)
vline(300, 690, 720)
vline(1100, 690, 720)
d.text((266, 694), "否", font=F_SMALL, fill="#DC2626")
d.text((1112, 694), "是", font=F_SMALL, fill="#059669")

# 第一轮
d.text((70, 700), "第 1 轮", font=F_TAG, fill="#2563EB")
box(130, 720, 340, 70, "暂存复盘状态", "range_json + review_stage=collect", lane="purple")
box(130, 820, 340, 64, "复盘提问", "统计摘要 + 三个问题（按周期变化）", lane="blue")
vline(300, 790, 820)
d.rounded_rectangle([120, 905, 480, 945], radius=10, fill="#EFF6FF", outline="#BFDBFE", width=1)
d.text((150, 916), "第 1 轮结束，等用户回答", font=F_SMALL, fill="#1D4ED8")

# 第二轮
d.text((1150, 700), "第 2 轮", font=F_TAG, fill="#7C3AED")
box(930, 720, 340, 64, "生成复盘报告（LLM）", "v4-pro · 数据 + 历史 + 用户补充", lane="purple")
box(930, 814, 340, 58, "构造复盘行（代码）", "period / range / report / stats", lane="blue")
box(930, 902, 340, 58, "保存复盘（HTTP POST）", "写入 public.reviews", lane="green")
box(930, 990, 340, 54, "清空复盘状态", "review_stage 置空", lane="purple")
box(930, 1074, 340, 54, "回复报告", "六段（日复盘为三段）", lane="blue")
vline(1100, 784, 814)
vline(1100, 872, 902)
vline(1100, 960, 990)
vline(1100, 1044, 1074)

# 数据来源说明
d.rounded_rectangle([70, 1000, 640, 1130], radius=14, fill="#F8FAFC", outline="#CBD5E1", width=2)
d.text((92, 1016), "数据来源", font=F_TAG, fill="#334155")
d.text((92, 1046), "schedules：status（planned / done）、manual_override、", font=F_SMALL, fill=MUTED)
d.text((92, 1068), "event_date、start_time、end_time、category", font=F_SMALL, fill=MUTED)
d.text((92, 1094), "reviews：period、range_start/end、report、stats", font=F_SMALL, fill=MUTED)

d.text((70, 1180), "三档统计：手动确认完成 · 按时间默认完成 · 未完成；月复盘额外输出按周完成率",
       font=F_SMALL, fill=MUTED)

img.save("campus-schedule-agent/diagrams/review-flow.png", "PNG")
print("saved")
