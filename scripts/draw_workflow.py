# -*- coding: utf-8 -*-
"""生成校园日程智能体的工作流总览图。"""

from PIL import Image, ImageDraw, ImageFont

W, H = 1800, 1400
BG = "#FFFFFF"
ARROW = "#94A3B8"
INK = "#0F172A"
MUTED = "#64748B"

FONT = "C:/Windows/Fonts/msyh.ttc"
FONT_BD = "C:/Windows/Fonts/msyhbd.ttc"


def f(size, bold=False):
    return ImageFont.truetype(FONT_BD if bold else FONT, size)


F_TITLE = f(36, True)
F_SUB = f(17)
F_HEAD = f(20, True)
F_BOX = f(16, True)
F_BODY = f(13)
F_SMALL = f(12)
F_TAG = f(13, True)

LANE = {
    "blue": ("#EFF6FF", "#3B82F6", "#1E3A8A"),
    "green": ("#ECFDF5", "#10B981", "#065F46"),
    "amber": ("#FFFBEB", "#F59E0B", "#92400E"),
    "gray": ("#F8FAFC", "#94A3B8", "#334155"),
    "orange": ("#FFF7ED", "#F97316", "#9A3412"),
    "slate": ("#F1F5F9", "#CBD5E1", "#334155"),
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


def box(x, y, w, h, title, body="", lane="blue", body_size=13):
    fill, border, ink = LANE[lane]
    d.rounded_rectangle([x, y, x + w, y + h], radius=12, fill=fill, outline=border, width=2)
    inner_w = w - 24
    lines = wrap(title, F_BOX, inner_w)
    body_lines = wrap(body, f(body_size), inner_w) if body else []
    total = len(lines) * 22 + len(body_lines) * 17
    cy = y + (h - total) / 2
    for ln in lines:
        tw, _ = text_size(ln, F_BOX)
        d.text((x + (w - tw) / 2, cy), ln, font=F_BOX, fill=ink)
        cy += 22
    for ln in body_lines:
        tw, _ = text_size(ln, f(body_size))
        d.text((x + (w - tw) / 2, cy), ln, font=f(body_size), fill=MUTED)
        cy += 17


def diamond(cx, cy, w, h, text):
    fill, border, ink = LANE["orange"]
    pts = [(cx, cy - h / 2), (cx + w / 2, cy), (cx, cy + h / 2), (cx - w / 2, cy)]
    d.polygon(pts, fill=fill, outline=border)
    d.line(pts + [pts[0]], fill=border, width=2)
    tw, th = text_size(text, F_BOX)
    d.text((cx - tw / 2, cy - th / 2 - 2), text, font=F_BOX, fill=ink)


def head_arrow(x, y, color=ARROW):
    d.polygon([(x - 5, y - 9), (x + 5, y - 9), (x, y)], fill=color)


def head_right(x, y, color=ARROW):
    d.polygon([(x - 9, y - 5), (x - 9, y + 5), (x, y)], fill=color)


def vline(x, y1, y2, color=ARROW):
    d.line([(x, y1), (x, y2)], fill=color, width=2)
    head_arrow(x, y2, color)


def hline(x1, x2, y, color=ARROW):
    d.line([(x1, y), (x2, y)], fill=color, width=2)
    head_right(x2, y, color)


def dashed(points, color=ARROW):
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        length = max(abs(x2 - x1), abs(y2 - y1))
        steps = max(1, int(length // 8))
        for i in range(steps):
            if i % 2:
                continue
            sx = x1 + (x2 - x1) * i / steps
            sy = y1 + (y2 - y1) * i / steps
            ex = x1 + (x2 - x1) * (i + 1) / steps
            ey = y1 + (y2 - y1) * (i + 1) / steps
            d.line([(sx, sy), (ex, ey)], fill=color, width=2)


# 标题
d.text((60, 36), "校园日程智能体 · 工作流总览", font=F_TITLE, fill=INK)
d.text((62, 88), "1 条主流程 + 4 条业务线 · 共 5 条链路", font=F_SUB, fill=MUTED)

# 顶部主流程
box(820, 140, 160, 52, "开始", lane="slate")
box(720, 215, 360, 62, "输入", "粘贴通知 / 语音输入 / 图片 OCR", lane="slate")
box(770, 300, 260, 54, "意图识别（5 类）", lane="slate")
vline(900, 192, 215)
vline(900, 277, 300)
vline(900, 354, 378)

# 总线
d.line([(220, 378), (1480, 378)], fill=ARROW, width=2)
d.rounded_rectangle([892, 370, 908, 386], radius=4, fill=ARROW)

COL = [(220, "① 录入线", "blue"), (640, "② 查询线", "green"),
       (1060, "③ 复盘线", "amber"), (1480, "④ 兜底", "gray")]

for cx, label, lane in COL:
    vline(cx, 378, 405)
    fill, border, ink = LANE[lane]
    tw, th = text_size(label, F_HEAD)
    d.rounded_rectangle([cx - tw / 2 - 16, 405, cx + tw / 2 + 16, 443], radius=19,
                        fill=fill, outline=border, width=2)
    d.text((cx - tw / 2, 414), label, font=F_HEAD, fill=ink)

# ---------------- 录入线 ----------------
box(50, 465, 340, 62, "抽取字段", "名称 / 日期 / 起止时间 / 地点 / 类别", lane="blue")
box(50, 550, 340, 56, "校验与归一化（代码）", "相对日期换算、时间格式、缺字段检测", lane="blue")
diamond(220, 660, 220, 78, "信息齐全？")
vline(220, 527, 550)
vline(220, 606, 621)

# 分支：否 / 是
d.line([(220, 699), (220, 712)], fill=ARROW, width=2)
d.line([(125, 712), (315, 712)], fill=ARROW, width=2)
vline(125, 712, 730)
vline(315, 712, 730)
d.text((96, 716), "否", font=F_SMALL, fill="#DC2626")
d.text((330, 716), "是", font=F_SMALL, fill="#059669")

box(45, 730, 160, 82, "追问缺失项", "草稿存 draft\n下一轮合并，不丢", lane="blue", body_size=12)
box(235, 730, 160, 82, "读当天已有日程", "GET /schedules\n（Supabase）", lane="green", body_size=12)

# 回环虚线
dashed([(45, 771), (20, 771), (20, 496), (48, 496)])
head_right(48, 496)

box(50, 845, 340, 56, "冲突检测（代码）", "区间重叠判断，确定性计算，结果可复现", lane="blue")
d.line([(315, 812), (315, 828), (220, 828), (220, 845)], fill=ARROW, width=2)
head_arrow(220, 845)

diamond(220, 945, 210, 76, "有冲突？")
vline(220, 901, 907)

d.line([(220, 983), (220, 996)], fill=ARROW, width=2)
d.line([(125, 996), (315, 996)], fill=ARROW, width=2)
vline(125, 996, 1014)
vline(315, 996, 1014)
d.text((96, 1000), "是", font=F_SMALL, fill="#DC2626")
d.text((330, 1000), "否", font=F_SMALL, fill="#059669")

box(45, 1014, 160, 92, "冲突说明", "双方标题+时间\n给出空闲时段建议", lane="orange", body_size=12)
box(235, 1014, 160, 92, "复述并确认", "列出摘要，等用户\n回复“确认”", lane="blue", body_size=12)

d.line([(125, 1106), (125, 1128), (220, 1128), (220, 1140)], fill=ARROW, width=2)
d.line([(315, 1106), (315, 1128), (220, 1128)], fill=ARROW, width=2)
head_arrow(220, 1140)

box(50, 1140, 340, 60, "写入网站（POST）", "确认后才保存；source = agent", lane="blue")

# ---------------- 查询线 ----------------
box(470, 465, 340, 62, "解析时间范围", "今天 / 本周 / 某一天 / 某一段", lane="green")
box(470, 550, 340, 56, "读取日程接口", "HTTP GET，只读不改", lane="green")
box(470, 630, 340, 62, "回答", "安排清单 + 空闲时段建议", lane="green")
vline(640, 527, 550)
vline(640, 606, 630)
d.text((470, 716), "只依据接口返回的数据回答，不编造日程", font=F_SMALL, fill=MUTED)
d.text((890, 830), "复盘写回需要网站支持 status 字段", font=F_SMALL, fill=MUTED)

# ---------------- 复盘线 ----------------
box(890, 465, 340, 56, "选择范围", "某一周 / 某一个月", lane="amber")
box(890, 545, 340, 56, "读取该范围计划", "HTTP GET", lane="amber")
box(890, 625, 340, 70, "逐条询问完成情况", "已完成 / 未完成 / 已调整\n原因可选，不得自行判断", lane="amber", body_size=12)
box(890, 720, 340, 70, "汇总与建议", "完成率、计划偏差、未完成原因\n下次改进建议", lane="amber", body_size=12)
vline(1060, 521, 545)
vline(1060, 601, 625)
vline(1060, 695, 720)

# ---------------- 兜底 ----------------
box(1310, 465, 340, 62, "其他问题", "介绍能做什么，引导到上面三条线", lane="gray")

# ---------------- 底部状态与数据 ----------------
d.text((60, 1230), "会话变量（跨轮记忆）：draft 草稿 · pending_event 待确认 · review_range 复盘范围",
       font=F_SMALL, fill=MUTED)
d.text((60, 1248), "追问后不是重新开始：下一轮把 draft 与用户新回复一起送进抽取节点合并，用户只补缺的那一项",
       font=F_SMALL, fill="#B45309")

d.rounded_rectangle([60, 1272, 1740, 1338], radius=14, fill="#F1F5F9", outline="#CBD5E1", width=2)
d.text((90, 1295), "Supabase schedules 表", font=F_BOX, fill="#334155")
d.text((380, 1295), "与", font=F_BOX, fill="#64748B")
d.text((420, 1295), "心流日程网站（读取 / 写入）", font=F_BOX, fill="#334155")
d.text((1140, 1297), "网站与智能体共用同一份数据，避免两边各存一套", font=F_SMALL, fill=MUTED)

# 图例
lx, ly = 60, 1358
d.rounded_rectangle([lx, ly, lx + 18, ly + 18], radius=4, fill="#EFF6FF", outline="#3B82F6", width=2)
d.text((lx + 26, ly - 1), "处理节点", font=F_SMALL, fill=MUTED)
d.polygon([(lx + 130, ly + 9), (lx + 140, ly), (lx + 150, ly + 9), (lx + 140, ly + 18)],
          fill="#FFF7ED", outline="#F97316")
d.text((lx + 158, ly - 1), "判断", font=F_SMALL, fill=MUTED)
dashed([(lx + 240, ly + 9), (lx + 290, ly + 9)])
d.text((lx + 298, ly - 1), "追问后带草稿回到抽取（合并）", font=F_SMALL, fill=MUTED)
d.text((lx + 500, ly - 1), "HTTP 节点调用网站接口", font=F_SMALL, fill=MUTED)

img.save("campus-schedule-agent/diagrams/workflow.png", "PNG")
print("saved")
