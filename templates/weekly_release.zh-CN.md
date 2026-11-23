# InferPilot 周报 <周二 YYYY-MM-DD> 至 <周一 YYYY-MM-DD>

<!-- 按 sglang release note 的样子汇报 InferPilot 的一周：先是读者可以据此行动的亮点，再按领域写完整说明，然后是不兼容
     变更与升级说明、贡献者和完整变更记录。这是一份进度汇报，不发到 GitHub，英文版（`weekly_release.md`）和中文版
     （`weekly_release.zh-CN.md`）各一份：章节、表格、行内代码、数字和链接都相同，`tests/test_templates.py` 会检查。
     它依据的是仓库，而不是哪台机器上的运行：本周合入的 InferPilot PR，以及来自 InferPilot 工作的 sglang PR，即从
     `inferpilot/<slug>` 分支推送的、或者 InferPilot 的 PR 记为它自己移植的。每一行都链接到对应的 PR 或 issue。标题里的
     两个日期就是这一周的起止：周二 00:00 到下周一 24:00，UTC+8。某一节这周没有内容，就不写这一节。 -->

# 亮点

*合入 <m> 个 PR，来自 <c> 位贡献者，另有 <x> 个没有合入就关闭；来自 InferPilot 工作的 sglang PR 新开 <u> 个，合入 <k> 个。*

<!-- 三到六条，相当于 sglang 的 “What's new”，每条以一个加粗的小标题开头：本周 InferPilot 自己多了什么、给 AMD 上的
     sglang 带来了什么，附上对应的 PR，测过的附上数字。先写已合入的。 -->

* **<小标题>：**<变了什么，附上能说明它的数字>（[#<n>](<url>)）。

## 上游 PR

<!-- 本周新开、有改动或已合入的、来自 InferPilot 工作的每个 sglang PR，已合入的排前面。Gates：每个 gate 在 PR 或它的
     feature skill 里记录的结论，没跑的写“未运行”；CI 修复有 `failing_test`、没有 G5，issue 修复有 `issue_repro`，只有
     性能类 issue 才有 G5。结果：收益和它的 workload，修复则写哪一项重新通过。Review 状态指上游的状态。 -->

| PR | 类型 | 模型 / 特性 | 平台 | Gates | 结果 | Review 状态 |
|---|---|---|---|---|---|---|
| [#<n>](<url>) <标题> | feature / issue / ci / perf | | MI30x / MI35x | <每个 gate 的结论，例如 G0-G2 通过，G3 未运行，failing_test 通过> | <收益和它的 workload，或者哪一项重新通过> | 已合入 / 已批准 / 要求修改 / 审阅中 / 已关闭 |

# 完整说明

<!-- 按领域分：先写本周的 sglang PR 给 AMD 带来的，再写 InferPilot 自己的 PR。 -->

## AMD feature 移植

* [Feat] [AMD] <scope>：<标题>（<测得的收益，或证明的一致性>）：[#<n>](<url>)

## issue 修复

<!-- 本周的 PR 修复的 AMD 用户 issue，P0 在前。 -->

* [#<n>](<url>) <标题>（<P0 / P1 / P2>）：由 [#<n>](<url>) 修复；回复<已发出 / 没有发出>

## CI 修复

* [<job>](<job URL>)：<类别>，<引起问题的 commit 或 PR>；由 [#<n>](<url>) 修复

## 性能

* <模型>，<workload>：<收益，在同等预算下单独测得（G5）>（[#<n>](<url>)）

## Kernel

<!-- 本周的 PR 在 AMD 上支持的 kernel：新的 kernel，或者某个算子、模型、数据类型现在用上的 kernel。 -->

* <平台> 上 <模型或数据类型> 的 <算子>：<kernel>（[#<n>](<url>)）

## Runtime & Backend

<!-- InferPilot 的 runner，以及运行各个角色的 agent（Claude Code、claude -p、Cursor）：运行的日志、控制和结果。 -->

* <标题>：[#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## Workflow 与 Gate

<!-- 每种模式都要经过的部分，从 Setup 到 PR：各个阶段、gate 和它们的测量方式、harness。 -->

* <标题>：[#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## 各任务模式优化

<!-- 只改了某一种模式（feature、issue、ci 或 perf）的 PR，标题里写明是哪种模式。 -->

* <标题>：[#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## 每日社区更新

<!-- scan、它关于 AMD CI、用户 issue 和上游改动的日报，以及日报的分享。 -->

* <标题>：[#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## 容器与环境安装

<!-- run_docker.sh、镜像和 bootstrap。 -->

* <标题>：[#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## 测试与 GitHub CI

<!-- InferPilot 自己的测试，以及在每个 PR 上运行它们的 CI。 -->

* <标题>：[#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## 知识库

<!-- 改动 InferPilot 知识库的 PR：feature skill、已知问题、编入索引的 sglang skill、ROCm 说明、reward hack。 -->

* knowledge：<标题>：[#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## 不兼容变更与升级说明

<!-- 用户下次运行前必须改的东西：token 或它的权限、改名或删除的参数、bootstrap 或镜像的变化。先用一句加粗的话说明，
     再写怎么做。 -->

* **<变了什么>。** <怎么做>：[#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## 数据

| | 已合入 | 没有合入就关闭 | 本周结束时仍开着 |
|---|---|---|---|
| InferPilot PR | | | |
| 来自 InferPilot 的 sglang PR | | | |

InferPilot 的 main 上改动了 <f> 个文件，+<a> / −<d> 行。

## 贡献者

<!-- 本周编写、推送、review 或合入 InferPilot 工作的人，就像 sglang 列出新贡献者那样。 -->

* @<user>：<编写 / 推送 / review / 合入> [#<n>](<url>)

**完整变更记录**：https://github.com/At1a8/inferpilot/compare/<本周开始时的 main>...<本周结束时的 main>
