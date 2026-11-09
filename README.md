# 校园日程智能体

粘贴一段通知、发一张课表截图，或随口说一句安排，它会把内容拆成多条：
**有具体时间的进日程表，没有时间的进任务栏**；之后能查冲突、改、删、做未来规划、做日/周/月复盘。

运行在 Dify Cloud（Chatflow），数据存在 Supabase，前端是配套的「心流日程」周视图网站。

---

## 目录结构

```
campus-schedule-agent/
├─ dist/                     可直接导入 Dify 的应用包（当前 v31）
├─ docs/                     提示词、建表 SQL、提示词优化材料
├─ diagrams/                 流程图（录入/删除、复盘、总览）
├─ scripts/                  生成流程图的 Python 脚本
├─ code/                     代码节点源码（校验、冲突检测）
├─ tests/                    本地回归测试
├─ archive/                  历史版本与诊断包（不参与运行）
└─ README.md
```

---

## 快速开始

**1. 导入 Dify**

工作区 → 导入 DSL → 选 `dist/校园日程智能体-v31.ifpkg`。

**2. 导入后核对模型**

| 节点 | 模型 |
|---|---|
| 意图识别 | `deepseek-flash` |
| 抽取日程 | `deepseek-v4-flash-vision-exp`（需要视觉） |
| 抽取任务 / 抽取规划目标 | `deepseek-flash` |
| 生成安排说明 | `deepseek-v4-flash` |
| 生成复盘报告 / 规划建议输出 | `deepseek-v4-pro` |

**3. 设置异常处理**

「保存复盘」节点 → 异常处理 → **默认值**（存库失败也不影响回复）。

**4. 发布**

**5. 建表**

Supabase SQL Editor 里执行 `docs/schema.sql`（三张表：`schedules`、`tasks`、`reviews`）。

**6. 配环境变量**（Dify 应用内）

| 变量 | 说明 |
|---|---|
| `BASE_URL` | Supabase 项目 URL |
| `USER_ID` | 演示用固定 `demo` |
| `SUPABASE_KEY` | publishable key |

---

## 能力概览

意图分 10 条链路，入口只有一个对话框：

| 意图 | 行为 |
|---|---|
| `add` | 批量抽取 → 分流 → 查冲突 → 直接保存，回复清单 |
| `task` | 记一条待办，进任务栏 |
| `delete` | 定位（标题 + 日期 + 时间）→ 二次确认 → 删除 → 核对行数 |
| `update` | 定位唯一命中 → PATCH 修改 |
| `query` | 用全局窗口数据回答某天/某周的安排 |
| `plan` | 抽目标 → 算空档 → 排时间块 → 给建议 → 问确认 |
| `plan_confirm` | 确认后同时写日程和任务 |
| `review` | 两轮复盘的第一轮：统计 + 已完成/未完成清单 + 提问 |
| `review_feedback` | 第二轮：生成报告 → 存库 → 回复 |
| `other` | 兜底引导 |

---

## 关键设计

**批量抽取**
一条通知里有几个安排就出几个 `item`；课表截图按行拆成多门课。

**日程 vs 任务（代码判断，不交给模型）**
有开始时间 → `schedules`；只有日期或只有截止 → `tasks`；标题都没抽到才追问。

**冲突不拦截**
和已有日程撞了也照常保存，只在回复里提示“与某条冲突”，由你决定删还是改。

**删除有上下文**
找到多条时记住候选，下一句按日期/时间匹配（`11.3`、`2026-11-03`、`15:00` 都认）；
完全相同的重复记录会一并删除；删完用 `return=representation` 核对真实删除行数，不会出现“说删了其实没删”。

**复盘两轮**
第一轮先列出「已完成 / 未完成」清单，只问未完成的原因、最有成就感的事、可沉淀的经验；
第二轮生成报告并写入 `reviews` 表，下次复盘会带上最近 3 次做对比。

**规划产出两样**
每个目标建一条任务（进任务栏），同时把目标切成时间块写进日程（每块 ≤2 小时、每天 ≤2 块、避开已有安排）。

**图片输入**
带文件时跳过意图识别，直接进抽取节点（视觉模型），不需要额外打字说明。

---

## 数据模型

| 表 | 关键字段 | 说明 |
|---|---|---|
| `schedules` | `title` `event_date` `start_time` `end_time` `location` `category` `status` `source` `manual_override` | 日程；`status=done` 表示完成，`manual_override` 区分手动勾选与到点自动完成 |
| `tasks` | `title` `task_date` `done` | 任务栏 |
| `reviews` | `period` `range_start` `range_end` `report` `stats` | 复盘报告存档 |

---

## 已知限制

- 意图识别是文本模型，所以纯图片走的是「有文件就直通抽取」的旁路，而不是让意图节点读图。
- 空档按 08:00–22:00 计算，块长 60–120 分钟，每天最多 2 块——排期是贪心的，不保证全局最优。
- 冲突检测只看前后 7 天窗口。
- 单用户演示（`user_id=demo`，RLS 全开），正式使用需要接入登录与行级权限。

---

## 开发提示

- DSL 源文件（`app.yaml`）与打包、校验脚本在上级目录 `../_ifpkg_extract`，不纳入本仓库。
- 重新生成流程图（在 workspace 根目录执行）：

```powershell
python campus-schedule-agent/scripts/draw_workflow.py
python campus-schedule-agent/scripts/draw_review_flow.py
python campus-schedule-agent/scripts/draw_create_delete.py
```

- 本地跑代码节点测试：

```powershell
$env:PYTHONIOENCODING="utf-8"
python campus-schedule-agent/tests/test_code_nodes.py
```

---

## 版本

当前 **v31**。历史版本、诊断包都放在 `archive/`，仅作追溯，不参与运行。
