# InferPilot 周报 <YYYY>-W<ww> — <周一 YYYY-MM-DD> 至 <周日 YYYY-MM-DD>

<!-- 按 sglang release note 的样子汇报 InferPilot 一周的工作：先是读者可以据此行动的亮点，再按领域写完整说明，
     然后是不兼容变更与升级说明、贡献者和完整变更记录。这是一份进度汇报，不发到 GitHub，英文版
     （`weekly_release.md`）和中文版（`weekly_release.zh-CN.md`）各一份：章节、表格、行内代码、数字和链接都相同，
     `tests/test_templates.py` 会检查。来源：本周的运行（每个容器的 `exp/index.md` 和它的运行目录）、InferPilot
     仓库里分享的日报、本周合入的 InferPilot PR，以及从 `inferpilot/<slug>` 分支推送的 sglang PR。每一行都链接到
     它的依据：上游 PR 或 issue、日报、InferPilot PR，或者用目录名指明的运行（`issue_41299-glm-dcp_20261106-071502Z`）。
     某一节这周没有内容就写“无”。时间用 UTC。 -->

| 周（UTC） | sglang main | 镜像 | 运行 | 上一期周报 |
|---|---|---|---|---|
| <周一> 00:00 至 <周日> 24:00 | `<第一次 scan 的 sha>` 至 `<最后一次的 sha>` | `<各次运行用的每日镜像>` | <c> 个容器里的 <n> 次运行 | <链接，或无> |

# 亮点

*<n> 次运行（feature <f>、issue <i>、ci <c>、perf <p>、scan <s>）；<r> 个 PR 可以 review，<m> 个已合入上游；回复了 <u> 个用户 issue。*

<!-- 三到六条，相当于 sglang 的 “What's new”：本周 AMD 用户得到了什么，每条带实测数字和对应的 PR。先写已合入的，
     再写可以 review 的；问题只在挡住 AMD 时才写在这里（main 上红着的 CI job、没有修复的 P0）。 -->

* <AMD 用户这边变了什么，附上能说明它的数字>（[#<n>](<url>)，运行 `<运行目录>`）。

## 上游 PR

<!-- 本周新开、有改动或已合入的每个 sglang PR：由 InferPilot 的运行准备、由人推送，已合入的排前面。Gates：每个
     gate 在 validation report 里记录的结论，没跑的写“未运行”；CI 修复有 `failing_test`、没有 G5，issue 修复有
     `issue_repro`，只有性能类 issue 才有 G5。结果：收益和它的 workload，修复则写哪一项重新通过。Review 状态指
     上游的状态。 -->

| PR | 类型 | 模型 / 特性 | 平台 | Gates | 结果 | Review 状态 |
|---|---|---|---|---|---|---|
| [#<n>](<url>) <标题> | feature / issue / ci / perf | | MI30x / MI35x | <每个 gate 的结论，例如 G0-G2 通过，G3 未运行，failing_test 通过> | <收益和它的 workload，或者哪一项重新通过> | 已合入 / 已批准 / 要求修改 / 审阅中 / 已关闭 |

## 使用方法

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/At1a8/inferpilot/main/scripts/run_docker.sh)
```

<!-- 本周结束时的 InferPilot commit 和最新的每日镜像；用户下次运行前必须做的事，写在“不兼容变更与升级说明”里。 -->

| InferPilot | sglang 镜像 |
|---|---|
| `<main 的 sha>`（[变更](https://github.com/At1a8/inferpilot/compare/<sha of the previous weekly>...<main sha>)） | `rocm/sgl-dev:<最新的每日 tag>` |

# 完整说明

## 移植到 AMD 的特性

* [Feat] [AMD] <scope>：<标题>（<测得的收益，或证明的一致性>）：[#<n>](<url>)，运行 `<运行目录>`

## 用户 issue

<!-- 本周运行处理过的 issue，P0 在前：每次运行得出的结论，以及人随后做了什么。 -->

* [#<n>](<url>) <标题>（<P0 / P1 / P2>）：<已复现 / 未复现 / main 上已修复 / 已诊断 / 由 [#<n>](<url>) 修复>；回复<已发出 / 已起草>

## main 上的 CI

* [<job>](<job URL>)：<类别>，<引起问题的 commit 或 PR>；<由 [#<n>](<url>) 修复 / 已移交 / flaky，已测出失败率>

## 性能

* <模型>，<workload>：<收益，在同等预算下单独测得（G5）>，或者放弃：<原因>（[#<n>](<url>)，运行 `<运行目录>`）

## Kernel 移交

* <模型> 在 <平台> 上的 <算子>：<已记录 / 已集成 / 没有收益>（<加速比>），运行 `<运行目录>`

## 知识库

<!-- 本周合入的、curator 向 InferPilot 提的 PR：验证过的 feature skill、已知问题、编入索引的 sglang skill、ROCm
     说明、reward hack。 -->

* knowledge：<标题>：[#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## InferPilot

<!-- 本周合入的 InferPilot 自身的 PR（curator 的除外）：它的 runner、角色、gate、脚本和模板。 -->

* <领域>：<标题>：[#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## 日报

* <YYYY-MM-DD>：[#<n>](https://github.com/At1a8/inferpilot/issues/<n>)，<失败的 CI job>，<open 的 P0 issue>，<排好的任务>

## 不兼容变更与升级说明

<!-- 用户下次运行前必须改的东西：token 或它的权限、改名或删除的参数、bootstrap 或镜像的变化。先用一句加粗的话说明，
     再写怎么做。 -->

* **<变了什么>。** <怎么做>：[#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## 未完成

<!-- 等人处理的事和停下来的运行：待推送的分支、待发出的回复草稿、待批准的设计、受阻或停下的运行，写明原因和人接下来
     要做什么。 -->

* <任务>：在 <阶段> <状态>，<原因>；下一步：<人要做的事>（运行 `<运行目录>`）

## 下周

<!-- 本周最后一份日报里排在前面的任务，按 dispatch.md 的 §6 排序。 -->

| 排名 | 任务 | 来源 | 类型 | 级别 | 优先级 |
|---|---|---|---|---|---|
| 1 | | | | L1 / L2 / L3 | |

## 数据

| 模式 | 运行次数 | 可以 review | 其他结局 | Agent 小时 | GPU 小时 |
|---|---|---|---|---|---|
| feature | | | | | |
| issue | | | | | |
| ci | | | | | |
| perf | | | | | |
| scan | | | | | |

## 贡献者

<!-- 本周推送、review 或合入 InferPilot 工作的人，就像 sglang 列出新贡献者那样。 -->

* @<user>：<推送 / review / 合入> [#<n>](<url>)

**完整变更记录**：https://github.com/At1a8/inferpilot/compare/<sha of the previous weekly>...<main sha>
