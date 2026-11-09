# 校园日程智能体

Dify Chatflow 校园日程助手，使用 Supabase 保存日程、任务、复盘、用户偏好，以及独立的持久化上下文文档。

当前版本 **v37 · 稳定删除确认版**。导入包：[校园日程智能体-v37.ifpkg](dist/校园日程智能体-v37.ifpkg)。历史应用包保存在 `archive/ifpkg/`，旧提示词材料保存在 `archive/docs/`。

## 部署与升级

1. 首次部署执行 [基础建表 SQL](docs/schema.sql)；已有 v35 基础表可跳过。
2. 首次启用持久记忆必须执行 [v36 上下文迁移](docs/context-memory.sql)，新增记忆文档、逐轮日志和 RPC；**已部署 v36 时，升级 v37 不需要重跑 SQL**。
3. 启用 Supabase 的 `pg_cron`，执行 [每日清理 SQL](docs/context-memory-cron.sql)。迁移前已启用 Cron 的项目会自动注册任务。没有 Cron 时仍会在新请求进入时清理；无请求时的自动清理依赖每日任务。
4. 在 Dify 导入 `dist/校园日程智能体-v37.ifpkg`，配置环境变量：

   | 变量 | 值 |
   |---|---|
   | `BASE_URL` | Supabase 项目根地址，不含 `/rest/v1`，末尾不带 `/` |
   | `SUPABASE_KEY` | Supabase publishable key，仅供 HTTP 节点使用 |
   | `USER_ID` | `demo`，本版仍按单用户演示设计 |

5. 核对模型，预览验收后发布：

   | 节点 | 模型 | 历史来源 |
   |---|---|---|
   | **选择相关上下文（新增）** | `deepseek-flash` | 文档索引 |
   | 意图识别 | `deepseek-flash` | 文档召回 + 当前状态 + 近期文本 |
   | 抽取日程 | `deepseek-v4-flash-vision-exp`，开启视觉 | 当前图片、草稿、最近3轮文字 |
   | 抽取任务 | `deepseek-flash` | 内置 Memory 3轮 |
   | 抽取规划目标 | `deepseek-flash` | 待确认方案、画像、Memory 6轮 |
   | AI 回复润色 | `deepseek-v4-flash` | 本轮结果，额外生成核心摘要和话题 |
   | 规划建议输出、生成复盘报告 | `deepseek-v4-pro` | 代码结果及所需历史，无内置 Memory |

模型名称沿用原供应商配置，需要在工作区映射到实际可用模型。应用包未携带部署地址或 API Key。

## v37 删除确认修复

修复“删除第一条”出现确认提示后，再回“确认”却提示没有匹配记录的问题。确认这一轮忽略模型重新生成的标题、日期和时间，只按上一轮已展示快照中的 ID 重新查库；删除过滤器也从核对后的 ID 生成。

支持“确认”“确认删除”“好的，确认删除”等表达。记录变化时展示最新信息并重新确认；已不存在时说明未再次删除。取消后或没有当前待确认操作时，不从历史记录执行删除。润色不能在成功回复中新增“没有待处理记录”的矛盾结论。

已有 v36 数据库和 Cron 配置可继续使用，只需导入并发布 v37。

## v36 的记忆机制

每次输入先记录原文和时间、读取文档；按时间与话题选择相关资料，完成操作后保存最终回复、核心摘要、记录 ID 和实际结果。原文不再只保留6轮，跨聊天可以按明确来源找回历史。

原始列表、最近操作焦点、待澄清候选分别保存。因此下面的操作会分别作用于原列表的第二条、第一条：

```text
我这周有什么安排？
把第二条的位置改为主楼会议室
再把第一条删除
确认
```

单条修改不会覆盖原始列表；删除不会重新编号。多张历史表匹配时可说“第二张”继续原请求，新聊天不会自动继承旧的执行确认。

```text
查看上下文文档
查看昨天的对话原文
删除昨天那张表的第一条
忽略之前的上下文
```

默认自动删除 **90天未实际使用**的对话原文、回复、摘要与列表快照；当前未完成操作的来源列表在最近活动7天内暂受保护。业务日程、待办、复盘和用户画像不参与删除。保留期可在 `context_documents.retention_days` 配置，完整策略见 [上下文配置说明](docs/context-configuration.md)。

## 功能与规则

- 文字通知、课表截图批量录入；日程缺关键信息时暂存草稿追问，缺结束时间时明确按60分钟估算。
- 查询按日期读取日程与任务，编号清单与后续引用一致；位置、标题、日期和时间可修改，删除需要确认。
- 规划由代码执行长期/临时限制，排除过去时间，新时间块立即占用；确认前重新查冲突，事务同时保存日程与任务。
- 复盘包括日程与任务栏，区分人工确认完成和按时间默认完成；累计反馈后说“生成报告”，保存报告、统计和建议摘要。
- 所有分支统一经过 AI 开场、收尾润色；核心清单、操作结果与确认要求保留。
- 日程、用户画像仍以业务数据库为事实来源，历史对话只能辅助定位，不证明记录仍存在。

## 目录

```text
workflow/          当前工作流源码与提示词
code/              工作流运行逻辑及旧版独立示例
scripts/           应用包构建与历史流程图生成脚本
dist/              当前可导入应用包（v37）
docs/              当前部署说明、SQL迁移与生成的提示词文档
tests/             工作流回归、RPC测试替身与数据库集成脚本
archive/           历史应用包、旧提示词和诊断资料
diagrams/          历史流程图
```

## 开发与验证

```bash
python -m pip install -r requirements-dev.txt
python scripts/build_workflow.py
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_*workflow.py'
PYTHONDONTWRITEBYTECODE=1 python tests/test_code_nodes.py
```

- `workflow/app.yaml`：生成并纳入版本管理的工作流，保留模型配置。
- `workflow/prompts.yaml`：八个模型节点的提示词源文件。
- `code/context_runtime.py`：实际运行代码，按每个节点的依赖嵌入 DSL。
- `scripts/build_workflow.py`：同步 DSL、提示词文档和 v37 应用包，生成可重复的 ZIP 与 SHA-256 manifest。
- `tests/test_context_workflow.py`、`tests/test_memory_workflow.py`：直接执行包内代码，模拟模型和 RPC；涵盖完整多轮链路与记忆清理。
- `tests/test_business_database.sql`：日程与任务事务写入的数据库集成脚本。
- `tests/test_memory_database.sql`：上下文存储与清理的数据库集成脚本；部署 SQL 后在测试项目执行，最后回滚测试数据。

`code/validate_event.py`、`code/conflict.py` 和原21项检查是旧版独立示例；`archive/`、旧提示词优化材料、流程图为历史参考。

当前仍为单用户匿名演示，无登录；不展开复杂周期课表；规划为贪心算法。索引、召回和列表尺寸有上限，超限时要求缩小范围。仓库环境没有线上凭据或 PostgreSQL 服务，尚未完成真实 Dify/Supabase/Cron 联调。
