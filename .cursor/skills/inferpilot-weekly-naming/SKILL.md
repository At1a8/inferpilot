---
name: inferpilot-weekly-naming
description: Naming conventions for InferPilot's weekly report (周报) in Chinese and English - the report title and file names, the section headings, and the bold short titles of the Highlights. Use when writing, updating or reviewing an InferPilot weekly report or its templates (templates/weekly_release.md, templates/weekly_release.zh-CN.md), or when naming a new section of it.
---

# InferPilot 周报命名规范

周报的结构和每节写什么，见 InferPilot 仓库的 `templates/weekly_release.md` 和 `templates/weekly_release.zh-CN.md`。这里只管名字。

## 标题与文件

- 标题只写时间，标题下不再放时间表格：
  - `# InferPilot weekly release <周二 YYYY-MM-DD> to <周一 YYYY-MM-DD>`
  - `# InferPilot 周报 <周二 YYYY-MM-DD> 至 <周一 YYYY-MM-DD>`
- 一周是周二 00:00 到下周一 24:00（UTC+8）。
- 文件：`inferpilot_weekly_<周二>_<周一>.md` 和 `inferpilot_weekly_<周二>_<周一>.zh-CN.md`。

## 章节名的规律

1. **写做了什么，不写在哪里、是谁的**：`<对象> <动作>`，动作用 移植、修复、优化、安装、支持、更新。
   - 移植到 AMD 的特性 → AMD feature 移植
   - 用户 issue → issue 修复
   - main 上的 CI → CI 修复
2. **对象要具体**：各模式 → 各任务模式优化；容器与安装 → 容器与环境安装。
3. **工程术语保留英文，不硬译**：运行器与 backend → Runtime & Backend。
4. **大小写**：InferPilot 的组件名首字母大写（Runtime、Backend、Workflow、Gate、Kernel），例如 Workflow 与 gate → Workflow 与 Gate；GitHub 上的普通名词小写（issue、feature、release）；缩写和产品名照原样（AMD、CI、PR、GitHub、Cursor、PARD-2）。
5. **中英文之间空一格**：AMD feature 移植、issue 修复。
6. **并列**：中文标题用“与”（容器与环境安装、Workflow 与 Gate）；Runtime & Backend 是固定写法。
7. **英文版一一对应**：移植 → porting，优化 → optimization，修复 → fixes，支持 → support；句首大写，组件名同样大写，并列用 &。
8. **InferPilot 是总标题**，不单独成一节；它自己的 PR 按下表分类。某一节这周没有内容，就不写这一节。

## 章节名对照表

| 英文版 | 中文版 | 写什么 |
|---|---|---|
| Highlights | 亮点 | 三到六条，每条带加粗小标题 |
| Upstream pull requests | 上游 PR | 来自 InferPilot 工作的 sglang PR 一览表 |
| Full Notes | 完整说明 | 以下各节 |
| AMD feature porting | AMD feature 移植 | 上游 PR 表里类型为 feature 的 PR |
| Issue fixes | issue 修复 | 类型为 issue 的 PR，挂在它来自的 issue 下；修的是 issue 引出的另一个问题时写明修了什么 |
| CI fixes | CI 修复 | 类型为 ci 的 PR |
| Performance | 性能 | 类型为 perf 的 PR，单独测得的 G5 收益 |
| Kernels | Kernel | 在 AMD 上支持的 kernel；feature 本身不算 kernel 优化 |
| Runtime & Backend | Runtime & Backend | runner、运行角色的 agent（Claude Code、claude -p、Cursor）、日志、控制、结果 |
| Workflow & Gate | Workflow 与 Gate | 各模式共用的阶段、gate、harness |
| Task mode optimization | 各任务模式优化 | 只改某一种模式（feature、issue、ci、perf）的 PR |
| Daily community updates | 每日社区更新 | scan、日报和日报的分享 |
| Container & environment setup | 容器与环境安装 | run_docker.sh、镜像、bootstrap |
| Tests & GitHub CI | 测试与 GitHub CI | InferPilot 自己的测试和 CI |
| Knowledge | 知识库 | feature skill、已知问题、sglang skill、ROCm 说明 |
| Breaking Changes & Upgrade Notes | 不兼容变更与升级说明 | 用户下次运行前必须改的东西 |
| Numbers | 数据 | PR 数、main 的改动规模 |
| Contributors | 贡献者 | 编写、推送、review、合入的人 |

上游 PR 表里的每一行，都按类型（feature、issue、ci、perf）放进完整说明里对应的一节。

## 亮点的小标题

每条以加粗小标题开头（中文 `* **小标题：** 正文`，英文 `* **Title:** text`），几个字说清能力或结果。收尾的 `**` 后面空一格：紧跟中文时，标点后的加粗不会生效。

| 类型 | 写法 | 例子（中文 / 英文） |
|---|---|---|
| 新能力 | 支持 X | 支持 Cursor Backend / Cursor Backend support；支持 GitHub CI / GitHub CI support |
| 移植 | <名称> 移植与 <对象> 优化 | PARD-2 移植与 Workflow 优化 / PARD-2 porting and Workflow optimization |
| 性能 | <名称> 性能优化 | PARD-2 性能优化 / PARD-2 performance optimization |
| 修复 | <对象> 修复 | CI 与 issue 修复 / CI and issue fixes |
| 自动化 | <对象>自动 <动作> | 每日社区更新自动 release / Daily community updates, released automatically |

## 新加一节时

先在对照表里找；没有合适的，按上面的规律起中英文两个名字，同时加进两个模板。`tests/test_templates.py` 会检查两个模板的章节、表格、行内代码、数字和链接一致。
