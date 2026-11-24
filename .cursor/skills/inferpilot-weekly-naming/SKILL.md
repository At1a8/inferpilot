---
name: inferpilot-weekly-naming
description: Naming conventions for InferPilot's weekly report (周报) in Chinese and English - the report title, its time range (scripts/weekly_report.py) and file names, the section headings (English in both versions), and the bold short titles of the Highlights. Use when writing, updating or reviewing an InferPilot weekly report or its templates (templates/weekly_release.md, templates/weekly_release.zh-CN.md), or when naming a new section of it.
---

# InferPilot 周报命名规范

周报的结构和每节写什么，见 InferPilot 仓库的 `templates/weekly_release.md` 和 `templates/weekly_release.zh-CN.md`。这里管名字、时间范围和数据从哪里来。

## 标题、时间范围与文件

- 标题只写时间范围，标题下不再放时间表格：
  - `# InferPilot weekly release <起 YYYY-MM-DD HH:MM> to <止 YYYY-MM-DD HH:MM> (UTC+8)`
  - `# InferPilot 周报 <起 YYYY-MM-DD HH:MM> 至 <止 YYYY-MM-DD HH:MM>（UTC+8）`
- 时间范围默认从请求时间往前 7 天的 00:00 到请求的时间点（UTC+8）：例如 2026-11-24 10:00 请求，就是 2026-11-17 00:00 至 2026-11-24 10:00。
- 范围和数据都由 InferPilot 仓库里的 `scripts/weekly_report.py` 给出，不要手算：
  - 默认范围：`python3 scripts/weekly_report.py`
  - 指定范围：`--since`、`--until`，取日期或日期加时间，不带时区就是 UTC+8；`--until` 只给日期时包含那一整天，例如 `--since 2026-11-17 --until 2026-11-23`；只给 `--until` 时，起点是它前 7 天的 00:00。
  - 它输出合入、关闭、仍开着的 InferPilot PR，起止时的 main 和改动规模，以及来自 InferPilot 工作的 sglang PR；`source` 为 `feature_skill:<id>` 的是候选，可能是人手写的移植，由写周报的人判断是否收录。
- 文件：`inferpilot_weekly_<起始日期>_<结束日期>.md` 和 `inferpilot_weekly_<起始日期>_<结束日期>.zh-CN.md`。

## 章节名的规律

章节名只有一套英文名：中文周报也用下表的英文章节名，只翻译正文和亮点的小标题。

1. **写做了什么，不写在哪里、是谁的**：`<对象> <动作>`，动作用 porting、fixes、optimization、setup、support、updates。
   - Features brought to AMD → AMD feature porting
   - User issues → Issue fixes
   - CI on main → CI fixes
2. **对象要具体**：Modes → Task mode optimization；Containers and setup → Container & environment setup。
3. **用代码里的名字**：Runner and backends → Runtime & Backend（代码在 `runtime/`）。
4. **大小写**：句首大写；InferPilot 的组件名首字母大写（Runtime、Backend、Workflow、Gate、Kernel），例如 Workflow and gates → Workflow & Gate；GitHub 上的普通名词小写（issue、feature、release）；缩写和产品名照原样（AMD、CI、PR、GitHub、Cursor、PARD-2）。
5. **并列用 &**：Runtime & Backend、Workflow & Gate、Tests & GitHub CI，和 sglang 的 Breaking Changes & Upgrade Notes 一样。
6. **InferPilot 是总标题**，不单独成一节；它自己的 PR 按下表分类。某一节这周没有内容，就不写这一节。

## 章节名对照表

中英文周报都用这里的章节名。

| 章节名 | 写什么 |
|---|---|
| Highlights | 三到六条，每条带加粗小标题 |
| Upstream pull requests | 来自 InferPilot 工作的 sglang PR 一览表 |
| Full Notes | 以下各节 |
| AMD feature porting | Upstream pull requests 表里类型为 feature 的 PR |
| Issue fixes | 类型为 issue 的 PR，挂在它来自的 issue 下；修的是 issue 引出的另一个问题时写明修了什么 |
| CI fixes | 类型为 ci 的 PR |
| Performance | 类型为 perf 的 PR，单独测得的 G5 收益 |
| Kernels | 在 AMD 上支持的 kernel；feature 本身不算 kernel 优化 |
| Runtime & Backend | runner、运行角色的 agent（Claude Code、claude -p、Cursor）、日志、控制、结果 |
| Workflow & Gate | 各模式共用的阶段、gate、harness |
| Task mode optimization | 只改某一种模式（feature、issue、ci、perf）的 PR |
| Daily community updates | scan、日报和日报的分享 |
| Container & environment setup | run_docker.sh、镜像、bootstrap |
| Tests & GitHub CI | InferPilot 自己的测试和 CI |
| Knowledge | feature skill、已知问题、sglang skill、ROCm 说明 |
| Breaking Changes & Upgrade Notes | 用户下次运行前必须改的东西 |
| Numbers | PR 数、main 的改动规模 |
| Contributors | 编写、推送、review、合入的人 |

Upstream pull requests 表里的每一行，都按类型（feature、issue、ci、perf）放进 Full Notes 里对应的一节。

## 亮点的小标题

小标题跟着正文的语言：英文周报写英文，中文周报写中文。每条以加粗小标题开头（中文 `* **小标题：** 正文`，英文 `* **Title:** text`），几个字说清能力或结果。收尾的 `**` 后面空一格：紧跟中文时，标点后的加粗不会生效。

- 中文小标题：工程术语保留英文，组件名首字母大写（Backend、Workflow），中英文之间空一格，并列用“与”。
- 英文小标题与中文一一对应：移植 → porting，优化 → optimization，修复 → fixes，支持 → support。

| 类型 | 写法 | 例子（中文 / 英文） |
|---|---|---|
| 新能力 | 支持 X | 支持 Cursor Backend / Cursor Backend support；支持 GitHub CI / GitHub CI support |
| 移植 | <名称> 移植与 <对象> 优化 | PARD-2 移植与 Workflow 优化 / PARD-2 porting and Workflow optimization |
| 性能 | <名称> 性能优化 | PARD-2 性能优化 / PARD-2 performance optimization |
| 修复 | <对象> 修复 | CI 与 issue 修复 / CI and issue fixes |
| 自动化 | <对象>自动 <动作> | 每日社区更新自动 release / Daily community updates, released automatically |

## 新加一节时

先在对照表里找；没有合适的，按上面的规律起一个英文名，加进对照表，两个模板里都用这个名字。`tests/test_templates.py` 会检查两个模板的标题和这张表一致，以及两个模板的表格、行内代码、数字和链接一致。
