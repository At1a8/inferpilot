# InferPilot

[English](README.md) | **简体中文**

> 本文与英文版 README 同步维护；如有出入，以英文版为准。

InferPilot 是面向 AMD GPU 的开源大模型推理框架 agent，首先支持 sglang。它把一个任务——把上游特性移植到 AMD、处理 AMD 用户的 issue、修复变红的 AMD CI job、弥补系统级的性能差距——变成一个维护者可以审阅的 pull request：**先保证正确**（独立的单元门禁，对照同模式基线的精度 / 长上下文 / 压力门禁，有对照实验的排查循环），**再追求性能**（同等预算下的 A/B），并遵守 [`knowledge/guardrails.md`](knowledge/guardrails.md) 中的社区规则。InferPilot 只提出改动；审阅、push 和合入由人来做。

InferPilot 为什么存在、与既定设计如何对应，见 [`docs/design.md`](docs/design.md)（英文）。

## 模式

| 模式 | 任务 | 阶段 | 产出 |
|---|---|---|---|
| `feature` | 把上游 PR 或设计移植到 AMD | Setup → Spec → Recon → Design → Implement → (Kernel) → Verify → Performance → Review → PR → (Spinoff) → Report | 分支 + PR 描述 + 验证报告 |
| `issue` | 一个 AMD 用户 issue | Setup → Triage → Reproduce → Diagnose（已有修复 PR 时跳过）→ Implement → (Kernel) → Verify → Performance（只改测试、文档或 CI 文件的修复跳过）→ Review → PR → (Spinoff) → Reply → Report | 修复 PR，或一个结论（已分诊、未复现、main 上已修复、已定位）；总会附一份回复草稿，并在结束时按运行结果重写 |
| `ci` | 一个变红的 AMD CI job 或 Failure Tracker 簇 | Setup → Classify → Bisect → Implement → (Kernel) → Verify → Performance（跳过的条件同 issue）→ Review → PR → (Spinoff) → Report | 修复 PR，或把偶发 / 基础设施失败移交出去 |
| `perf` | 针对某个模型和固定负载的系统级性能目标 | Setup → Profile → Plan → Implement → (Kernel) → Verify → Performance → Review → PR → (Spinoff) → Report | 带实测收益的 PR，或放弃改动；kernel 级的工作交给 kernel 后端 |
| `scan` | 无——框架当天的状态：main 上的 AMD CI、开放的 AMD 用户 issue、AMD 需要跟进的上游改动（平台门控和 host 同步线索按需附带） | Collect → Dispatch → Report | 日报（作为 issue 分享到 InferPilot 仓库，否则是一个由你来分享的文件），附排好序的任务和启动每个任务的定义 |

括号中的阶段只在适用时运行：有 kernel hand-off 且 `kernel_backend` 不是 `none` 时才跑 Kernel。issue 或 CI failure 的修复改了 server 会运行的代码时，Performance 要测它（G5：不比 base 慢）；只改测试、文档或 CI 文件的修复跳过这一步，除非问题本身就是性能问题（被分诊为 `performance` 的 issue，或某个测试因它测出的数值而失败的 CI failure）：这时无论修复改了什么，掉下去的数值都必须回来。报告者的代码失败、而 main 通过的 issue，结论是*已在 main 上修复*；设置 `bisect_fixed` 时，会先跑一个 Bisect 阶段找出修复它的提交。运行中发现了不属于本任务的问题时才跑 Spinoff：没有本次改动的 base 提交上也出现的失败，或者某个角色在工作中遇到的线索（读到的代码里的 bug、本身就坏掉的测试或镜像）。对每个在 base 上出现的失败，找出跟踪它的上游 issue，或起草一个 issue 由你提交。线索列出来并附上复现方法；设置 `confirm_leads` 时，先把每条线索放到 base 提交上确认，能复现的再起草 issue。

任务从一份**任务定义**开始（[`templates/task_definition.md`](templates/task_definition.md)）：也就是 `inferpilot.js` 接受的那些字段，每个角色读的都是同样的字段名。可以直接给出这些字段，也可以用一句话描述需求：这时先运行 Intake 阶段，把需求整理成任务定义，并在不改动任何东西的前提下核实能核实的内容（checkout、本机上的模型、GPU、issue）。如果任务定义仍缺少该模式需要的内容——issue、checkout、`feature` 和 `perf` 以及 issue、CI 修复需要的模型、`perf` 需要的固定负载——就什么都不启动，而是返回问题，每个字段一个，并尽量附上选项或默认值。完整的任务定义会跳过 Intake。

实现阶段一次只处理一个工作包；独立的测试工程师会重新运行它的单元测试，检查这个测试在没有该改动时会失败，并做关闭特性时的检查；一直通不过的工作包会被回滚，而门禁还没出结果的工作包会再调用一次测试工程师把它跑完，仍没有结果就停下交给人，不会回滚。验证失败交给排查者：它先做对照实验，再考虑加插桩，最后给出修复、证明该失败在基线上本就存在，或者给出无法定论的结论并停止任务。在 PR 之前，审阅者像维护者一样阅读 diff 和证据，并以一份已知 reward hack 目录为起点——也就是让数字变好、却违背了它本来含义的改动（[`knowledge/reward_hacks.md`](knowledge/reward_hacks.md)）；要求的修改，以及为每个确认的 hack 写的测试，都会变成工作包，重新经过 Implement 和 Verify。性能数据只有在 Setup 时固定的负载上、在任务所针对的服务路径上测得才算数，任务要求时（`held_out`），收益还必须在与负载一起固定下来的 held-out 工作点上同样成立（[`knowledge/perf_evaluation.md`](knowledge/perf_evaluation.md) §3）。

kernel 级的工作——调优 InferPilot 为某个特性写的 kernel，或者 kernel 级的性能机会——会在 Implement 和 Verify 之间的 Kernel 阶段交给 **kernel 工作流后端**（[`knowledge/kernel_backends.md`](knowledge/kernel_backends.md)）：

| `kernel_backend` | hand-off 的处理方式 |
|---|---|
| `none`（默认） | 连同其材料一起记录在报告里，不运行任何东西 |
| `geak` | 接入 GEAK 的接口：由 `kernel_backend_dir` 指定的 GEAK checkout 里的 kernel lane 作为外部程序运行；InferPilot 本身不带任何 GEAK 的东西 |
| `command` | 由 `kernel_backend_command` 指定的 kernel agent 命令行工具运行 |

收益会以工作包的形式回来：由 implementer 集成，再由 InferPilot 自己的门禁评判。后端失败或者没有找到收益，都不会阻塞任务。集成后的 kernel 如果在 Verify 失败，会先退回功能版 kernel，再带着这个失败用例作为新增测试交给后端重跑一次；只有通过了，才会再次集成。

## 角色

每个阶段调用一个角色：一个读取 `roles/<role>.md`、完成自己那部分工作并返回结构化 JSON 的 agent；`inferpilot.js` 持有这些 schema，并且只根据其中的必填字段做分支。所有角色都遵守四条规则：

- 只有 **implementer** 修改框架源码，而且只在任务自己的分支上改；
- 负责测量的角色（test engineer、verify engineer、performance engineer）、intake 和 reviewer 从不修改框架源码；负责测量的角色从不复用别人的数据，而是全部重新跑一遍；
- **curator** 只修改 InferPilot 自己的 `knowledge/`，并把改动作为 PR 提到 InferPilot 仓库，由你审阅合并（需要能写这个仓库的 `GITHUB_TOKEN`；没有时改动留在 checkout 里，不提交）；
- 没有任何角色会 push，或往上游发帖：PR engineer 只返回确切的 `git push` 命令，issue 回复都是草稿；会发出去的只有两样，都发到 InferPilot 自己的仓库，而且只用你的 `GITHUB_TOKEN`：curator 的知识更新，作为一个由你合并的 PR；scan 的日报，作为一个 issue（token 不能往那里写时，它是一个由你来分享的文件）。

| 角色 | 阶段 | 职责 |
|---|---|---|
| `intake` | Intake | 把一句话的需求或不完整的定义整理成任务定义：判断模式，用只读检查补全字段，把采用的默认值记为假设；缺任务依据、模型或负载，或者某项信息要付出代价才能拿到时，提问而不是猜；拒绝 InferPilot 不做的事 |
| `director` | Setup、Spec、Report | 创建运行目录和任务自己的分支，固定 commit、镜像和 GPU 并跑基线；写出验收标准可测量的特性说明（feature spec）；为每种结局写最终报告，并写明人接下来该做什么 |
| `scout` | Collect | 用下文的脚本收集候选任务 |
| `dispatcher` | Dispatch | 确定每个候选任务的差距类型、难度级别、CUDA 依赖及其在 AMD 上的起点、工作量和优先级，并排序成队列 |
| `analyst` | Recon | 把特性涉及的每个子系统映射到目标框架中的文件、函数和挂载点，并列表说明源设计的哪些假设在 AMD 上成立；只读不写 |
| `architect` | Design、Plan | 把 recon 或 profile 的结果拆成可以单独测试的工作包，附上不变量、显存预算、同步方式、启动时的拒绝条件和测试计划；标出必须由人审阅的设计；把每个性能机会分配给 InferPilot、kernel 后端或 Hyperloom |
| `issue_triager` | Triage、Reproduce、Spinoff、Reply | 根据 issue、评论和关联 PR 确定优先级和处理方式；起草回复；写出作为该 issue 门禁的复现步骤；为运行中发现的、不属于本任务的每个问题找出上游 issue 或起草一个，并列出线索（设置 `confirm_leads` 时先在 base 提交上确认）；结束时按运行结果重写回复 |
| `ci_guardian` | Classify、Bisect | 给变红的 job 分类（代码回归、依赖升级、测试缺陷、偶发、基础设施），后两类移交出去；判断它是不是性能问题，是的话给出修复必须恢复的数值；找到并确认引入问题的 commit，规划修复；对已在 main 上修复的 issue，设置 `bisect_fixed` 时找出修复它的 commit |
| `implementer` | Implement | 一次一个工作包，diff 尽量小，关闭特性时行为不变，只影响 HIP 或放在开关后面，提交时已通过 lint、测试已注册；编写带单元测试的功能性 kernel（调优走 hand-off）；应用现成的 PR 和验证过的 kernel 收益；回滚失败的工作包 |
| `test_engineer` | Implement | 在每个工作包的 commit 上重新运行它的单元测试，检查这个测试确实可能失败（改变行为的改动在父 commit 上验证，不改变行为的改动在故意改坏的副本上验证），并检查关闭特性时行为不变（没有开关的工作包，改为检查它涉及的模块已有的测试通过，或者和在父 commit 上失败的方式相同） |
| `verify_engineer` | Verify | 在全新启动的服务上对照同模式基线运行正确性门禁（G0、G2、组合性、模式专属门禁，任务要求时再加 G3 和 G4），对每个失败分类计数 |
| `investigator` | Investigate、Diagnose | 先跑对照实验，再一次只验证一个机制，并维护假设台账；返回修复工作包、基线同样存在的已知问题、无法定论的结论，或者（对 issue 而言）不在 AMD 这一侧的根因；探测代码只放在开关后面 |
| `perf_engineer` | Profile、Performance | 先确认运行走了预期的路径，再把稳态 trace 整理成固定的表格（kernel、重叠、融合模式、层）、指标和带上限的优化机会；在固定负载上以同等预算评估改动，对照噪声和任务指定的守护负载（任务要求时再加 held-out 工作点和更多守护负载），并逐项检查已知的测量类 hack；对照 base 测量修复，以及性能问题必须恢复的数值 |
| `reviewer` | Review、Reply | 像维护者一样阅读 diff 和证据，以已知 reward hack 目录为起点：shape、dtype、并行布局、graph、回退路径、不造成损害、框架的惯例、公平的基准测试；要求的修改，以及为每个确认的 hack 写的测试，以工作包形式返回；在报告前把 issue 的回复草稿逐条对照运行记录核对；可以换用另一个模型（`review_model`） |
| `kernel_liaison` | Kernel | 把 kernel hand-off 转成后端的任务，运行 `command` 类后端，依据后端自身的验证评判结果，并把收益作为工作包返回 |
| `pr_engineer` | PR | 不改任何代码：检查代码整洁、lint 和 CI 注册（缺什么就作为工作包退回），在不改变代码内容的前提下整理成每个工作包一个 commit，试合并 main 只用来报告冲突，在最终 commit 上检查关闭特性时的行为（没有开关的改动，改为检查它涉及的模块已有的测试），写 PR 描述和 push 命令 |
| `curator` | Report | 把这次运行证实的内容写进 `knowledge/`（已知问题、特性 skill、CUDA → AMD 对照条目、新发现的 reward hack、方法经验），每个数字都能追溯到这次运行，并作为 PR 提到 InferPilot 仓库 |

## 安装

InferPilot 以 root 身份运行在 ROCm 容器里：Claude Code、InferPilot、git 以及框架的服务和测试都在容器里运行，紧挨着 GPU 和框架的 Python 环境。每个任务用一个自己的容器，任务用到的一切都在这个容器里：`/workspace` 里的 InferPilot checkout 和它的运行目录，以及镜像自带的 sglang `/sgl-workspace/sglang`，任务在它上面开自己的分支修改。容器之间不共享这些，所以互不干扰；它们随所在的容器保留，容器删除时一起删除。Claude Code 也装在每个容器里，并预先回答好首次启动的问题。容器只从你宿主机的 home 里拿一个文件：保存 key 的 `~/.inferpilot_env`，以只读方式挂载；home 的其余部分不挂进来，因为 agent 以 root 身份、自动批准权限运行，而 home 里有它们用不着的凭据。

每一步都可以重复执行：先检查，保留已有的，只补缺的；再次运行 run_docker.sh 会回到原来的容器。命令里的 `检查 || 动作` 表示只有检查不通过时才执行动作：在新机器上每个动作都会执行，再运行一次则全部跳过。除了第 2 步在用每日镜像新建的容器里丢弃镜像构建对 `/sgl-workspace/sglang` 打包文件的改动，不会删除、覆盖或重新克隆任何东西。

### 宿主机需要什么

- 装有 AMD Instinct GPU（MI300 或 MI350 系列）且已加载 ROCm 驱动的 Linux（存在 `/dev/kfd`）。
- 你能使用的 Docker（用户在 `docker` 组里，或者有 sudo），以及 git、curl 和 python3。
- 能访问 claude.ai、github.com 和 Docker Hub；每个 sglang 镜像约需 45 GB 磁盘。
- 一个 AMD LLM 网关 key（`LLM_GATEWAY_KEY`）、Anthropic API key 或其他网关；都没有时，用 Claude 订阅。
- 一个 GitHub token（`GITHUB_TOKEN`），强烈建议准备：scan 和 issue 运行要通过 GitHub API 读取数据；它能写 InferPilot 自己的仓库时，curator 的 PR 和日报也用它提交到那里。
- 模型权重放在会挂载进容器的目录里（`/data`、`/mnt`、`/kmd2` 等；其他目录用 `EXTRA_MOUNTS` 加上）：InferPilot 从不下载权重。

### 1. 保存 key（宿主机）

```bash
[ -e ~/.inferpilot_env ] || (umask 077 && echo 'export LLM_GATEWAY_KEY=<your AMD LLM gateway key>' > ~/.inferpilot_env)
(umask 077 && grep -qs GITHUB_TOKEN ~/.inferpilot_env || echo 'export GITHUB_TOKEN=<your GitHub token>' >> ~/.inferpilot_env)
```

宿主机上不需要 Claude Code：第 3 步会把它装在容器里。运行这两行之前，先把 `<your AMD LLM gateway key>` 和 `<your GitHub token>` 换成你自己的值：每一行都只在 `~/.inferpilot_env` 里还没有这个 key 时才写入，所以以后要换 key，直接编辑这个文件。`~/.inferpilot_env` 把 key 保存在宿主机上，只有你能读取；run_docker.sh 会把它以只读方式挂进它启动的每个容器，所以要在启动容器之前创建它（没有它就启动的容器，可以在自己的 `/root/.inferpilot_env` 里放一份）。

- **LLM key（必填）。** 写的是 AMD LLM 网关 key 时，第 3 步会让 Claude Code 走这个网关（`https://llm-api.amd.com/Unified`，key 放在 `Ocp-Apim-Subscription-Key` 请求头里），网关按 Claude 模型的常用名字提供服务。不在 AMD 内部时，改写成 `export ANTHROPIC_API_KEY=sk-ant-...`，或者其他网关（`ANTHROPIC_BASE_URL` 加上 `ANTHROPIC_AUTH_TOKEN` 或 `ANTHROPIC_CUSTOM_HEADERS`）。用 Claude 订阅时，在任何有 Claude Code 和浏览器的地方运行 `claude setup-token`，写入 `export CLAUDE_CODE_OAUTH_TOKEN=<token>`；或者不写 key，在每个新容器首次启动时登录（第 4 步）。
- **GitHub token（强烈建议）。** InferPilot 在 GitHub 上的所有操作都只用这一个 token：`GITHUB_TOKEN`。scan 和 issue 运行要通过 GitHub API 读取 issue、PR 和 CI 跟踪记录：没有 token 时，GitHub 对每个 IP 地址每小时只允许 60 次请求、每分钟 10 次搜索，同一出口 IP 下的所有人共用这个额度，扫描会非常慢，还可能中途停下；有 token 时每小时 5000 次。它还会往 InferPilot 自己的仓库写东西，而且只写这一个仓库：curator 的知识更新，即从 `inferpilot/knowledge/<run>` 分支向 main 提一个 PR，由你审阅合并；以及 scan 的日报，作为一个 issue。容器里的每个 agent 都能读到 `~/.inferpilot_env` 里的内容，所以不要给这个 token 更多权限：在 github.com 上进入 Settings → Developer settings → Personal access tokens → Fine-grained tokens，*Only select repositories* 选 `At1a8/inferpilot`，*Repository permissions* 里 *Contents*、*Pull requests* 和 *Issues* 都选 *Read and write*（每个 fine-grained token 都还能读取所有公开仓库，包括 sglang），也不要把你日常用来 push 的 token 放进去。InferPilot 只 push `inferpilot/` 开头的分支；在 GitHub 上给 main 开分支保护（要求通过 PR 合并），这个 token 就也改不了 main。不能往那里写的 token（比如只读的）照样能用来 scan：知识更新会留在容器的 checkout 里，报告会给出提交它们的命令；日报则是一个由你来分享的文件。
- **Git 身份（建议设置）。** 容器里没有你的 home，也就没有你的 git 配置：InferPilot 在那里的提交作者会是 `root@<本机主机名>`。加上 `export INFERPILOT_GIT_NAME="<你的名字>"` 和 `export INFERPILOT_GIT_EMAIL=<你的邮箱>`，第 3 步会把它们设给 root。

### 2. 启动容器（宿主机）

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/At1a8/inferpilot/main/scripts/run_docker.sh)    # 新建一个容器；回到已有容器用 NAME=<name>
```

这一行下载 [`scripts/run_docker.sh`](scripts/run_docker.sh) 并运行它，所以宿主机上不需要 InferPilot 的代码；在 InferPilot 的 checkout 里直接运行 `scripts/run_docker.sh` 效果相同。它就是常见的 ROCm `run_docker.sh`：一个带 GPU 的特权容器，以相同路径挂载 `/home` 和存在的数据目录（`/data`、`/mnt` 等，`EXTRA_MOUNTS` 可以再加），并在容器的 `/workspace` 里打开一个 root shell。InferPilot 的 agent 以 root 身份、自动批准权限运行，因此能读到这台机器上所有人的 home，包括里面的凭据（git、ssh）：需要时用 `MOUNT_HOME=0` 不挂 `/home`，适合不能看到那些内容的任务（比如独立重新实现）。你的 key 另外以只读方式挂成 root 的 `~/.inferpilot_env`。和很多同类脚本不同，它不挂载宿主机的 Docker socket（`DOCKER_SOCKET=1` 才挂）：挂上它，agent 就能操作宿主机上的所有容器。

它做什么，取决于 `NAME` 是否指向一个已有的容器、以及是否指定了 `IMAGE`：

| | 不指定 `IMAGE` | 指定了 `IMAGE` |
|---|---|---|
| 不指定 `NAME`，或没有名为 `NAME` 的容器 | 用最新的每日镜像新建容器；在它的 `/sgl-workspace/sglang` 里执行 `git checkout -- .`、`git checkout main`、`git pull` | 用 `IMAGE` 新建容器；代码保持镜像原样 |
| 已有名为 `NAME` 的容器 | 进入这个名字对应的旧容器，代码保持离开时的样子，并警告 name 重复，写明它的 name 和 image | 同左；它的镜像不是 `IMAGE` 时，警告里同时说明不是指定的镜像版本 |

- **镜像。** 任务要在最新的 sglang 上测试，所以启动脚本会检测宿主机 GPU，并选择匹配的最新每日 ROCm 10 `rocm/sgl-dev` 镜像：gfx942（MI300/MI308/MI325）使用 `rocm10-mi30x`，gfx950（MI350/MI355）使用 `rocm10-mi35x`；这个 GPU 没有 ROCm 10 镜像时（Docker Hub 上没有，或 Docker Hub 连不上时本机没有），依次改用 ROCm 7.2.4、7.2.0。检测不了时会警告并回退到 MI35x（[`scripts/latest_image.py`](scripts/latest_image.py)，例如 `v0.5.23-rocm10-mi35x-20261103`；同一页面上的 `miles` 镜像用于强化学习，会被跳过）。每天的新镜像在第一次使用时拉取，约 43 GB。`IMAGE_VARIANT` 可以覆盖自动检测和回退，例如 `IMAGE_VARIANT=rocm724-mi30x`；要固定镜像，设置 `IMAGE=rocm/sgl-dev:<tag>`，这时 `/sgl-workspace/sglang` 保持镜像原样。容器一直使用它启动时的镜像：回到一个镜像已经不是最新每日镜像的容器时，run_docker.sh 会提醒；在里面跑任务，会在 Setup 阶段、创建任何东西之前停下来，问你是否仍在这个镜像上运行（`image_ok`），除非任务指定的就是这个镜像（`image`）。scan 会照常进行，并在日报里写明所用的镜像。
- **测的是哪份代码。** 镜像提供 PyTorch、aiter、sgl-kernel，以及它自带的 sglang checkout `/sgl-workspace/sglang`（带完整历史），sglang 就是以 editable 方式从这里安装的：这里检出的是什么，运行的就是什么，所以不需要另外克隆或指定 sglang。用每日镜像新建的容器一开始就在当前的 main 上：run_docker.sh 会丢弃镜像构建在这里的改动、切到 main 并拉取。之后不要在这里 `pip install -e`：这时它的 `pyproject.toml` 是上游默认的版本，会装进 CUDA 的包。用指定的 `IMAGE` 新建的容器、以及再次进入的容器，代码都保持原样。每个任务都在它上面、从最新的 main 开自己的分支，Setup 会先 fetch。在基础 commit 上跑的 baseline 和 scan 读的当前 main，用的是同一个仓库在运行目录下的 worktree（每个约 0.2 GB）。运行记录会写下镜像、镜像里 sglang 的版本和任务的基础 commit。
- **一个任务一个容器。** 不指定 `NAME` 时，每次运行都新建一个容器，名字是 `inferpilot_<你的用户名>_<月日>_<4 位随机字母或数字>`（UTC+8 的日期；保证没有同名容器），并打上 `inferpilot.user=<你的用户名>` 标签；run_docker.sh 会打印它的名字和镜像、回到它的命令（`NAME=<name> scripts/run_docker.sh`），以及你的其他 InferPilot 容器。`NAME` 指向已有的容器时，run_docker.sh 会回到那个容器（已停止的会先启动），`/workspace` 保持你离开时的样子：它从不删除容器，也不改容器里的代码。打开的 shell 是进入容器的 `docker exec`，所以退出 shell 后容器继续运行（`docker stop <name>` 可以停掉它）。不再需要的容器用 `docker rm -f <name>` 删除，它的 `/workspace` 也随之删除；在那之前，每个容器都会让它的镜像（约 43 GB）一直留在宿主机上。不管有多少个容器，一台机器同时只跑一个 InferPilot 任务（[运行](#运行)）。你自己的 run_docker.sh 也可以用：把 key 文件挂成 root 的 `~/.inferpilot_env`（`-v $HOME/.inferpilot_env:/root/.inferpilot_env:ro`），或者用 `INFERPILOT_KEYS` 指定这个文件；在挂载了 `/home` 的容器里，bootstrap 也会到你的 home 里去找（第 3 步）。
- **没有终端时。** 在作业调度器（`srun`）、CI 或 agent 的 shell 里没有终端，打不开交互 shell：这时 run_docker.sh 让容器在后台运行，并打印进入它的 `docker exec` 命令（`DETACH=0` 或 `1` 可以覆盖它的判断）。Docker daemon 拒绝 `--privileged` 时，用 `PRIVILEGED=0` 去掉它；GPU 仍然通过 `/dev/kfd` 和 `/dev/dri` 进入容器，但容器写不了 `/proc/sys`。Docker daemon 拒绝挂载整个 `/home` 时，用 `MOUNT_HOME=0` 不挂它，再用 `EXTRA_MOUNTS=$HOME` 只挂你自己的 home。启动被拒时，run_docker.sh 会说出该设哪个变量。

此时容器里还没有 `claude`（`claude: command not found`）：第 3 步会安装它。

### 3. 在容器里配置 InferPilot（每个容器做一次）

这一步把 InferPilot 放进容器的 `/workspace`，并让容器能运行它，具体做什么见下面第二张表。按下面的写法会同时配置 Claude Code 和 Cursor，任务用哪个跑都可以（第 4 步）；去掉其中一个选项，就少装对应的组件：

```bash
[ -d /workspace/inferpilot/.git ] || git clone https://github.com/At1a8/inferpilot /workspace/inferpilot
cd /workspace/inferpilot && python3 -B -m inferpilot.bootstrap --cursor --claude
source ~/.bashrc
claude --version    # 装在这个容器里的 Claude Code
agent --version     # Cursor CLI，也装在这里
```

| bootstrap 命令 | 安装的内容 | 任务可以用 |
|---|---|---|
| `python3 -B -m inferpilot.bootstrap --cursor --claude` | Claude Code、Node.js 和 Cursor CLI（`agent`） | Claude Code 或 Cursor |
| `python3 -B -m inferpilot.bootstrap --cursor` | Node.js 和 Cursor CLI | Cursor |
| `python3 -B -m inferpilot.bootstrap`（等同于 `--claude`） | Claude Code | Claude Code |

`--runner` 在 Claude Code 之外再装 Node.js，用来在没有 Claude Code 会话时让每个角色跑在 `claude -p` 上（第 4 步）；加了 `--cursor` 时 Node.js 已经装上。Cursor app 的登录不会带进容器，所以容器里的 Cursor CLI 要登录一次（每个容器一次）：

```bash
NO_OPEN_BROWSER=1 agent login   # 在别处的浏览器里打开它打印出的链接并登录，大约 20 分钟内有效（按 q 显示二维码）
agent status                    # Logged in as ...
```

或者在宿主机上你的 key 文件里设置 `CURSOR_API_KEY`（Cursor Dashboard 的 API Keys），之后启动的容器都会拿到。

| 步骤 | 在新容器里做什么 |
|---|---|
| key | root 的 `~/.inferpilot_env`（run_docker.sh 从你的 home 挂进来），或者 `INFERPILOT_KEYS` 指定的文件。在挂载了 `/home` 的容器里，也用你 home 里的那份：先看 `INFERPILOT_HOST_HOME`，没有时发出警告，改用容器名对应的那个用户的 home（`<you>`、`<you>-0116`）。没有 key 文件时其余步骤照常完成，"下一步"会说明 key 该放在哪里 |
| `claude` | （加 `--claude`，或两个选项都不加时安装）用 Claude Code 的原生安装器装在容器里（`~/.local/bin/claude`，并加到 `PATH`）：装的是最新版本，下载量较大（网速慢时几分钟没有输出，是在下载）。容器存在期间一直保持这个版本（`DISABLE_AUTOUPDATER=1`）；新容器会装最新的 |
| Claude Code 配置 | （加 `--claude`，或两个选项都不加时进行）在 root 的 `~/.claude.json` 里预先回答首次启动的问题：完成引导、信任 `/workspace/inferpilot`、接受 bypass permissions 模式，用 `ANTHROPIC_API_KEY` 时还批准使用这个 key。文件里的其他内容保留；和 root 的其他东西一样，它随容器删除 |
| git | 信任宿主机挂载目录里的 checkout，它们属于你而不是 root（`safe.directory '*'`） |
| git 身份 | root 以 key 文件（或环境变量）里的 `INFERPILOT_GIT_NAME <INFERPILOT_GIT_EMAIL>` 提交，所以不会出现作者为 `root@<本机主机名>` 的提交；两者都没有时，它会提示，不会自己编一个 |
| shell | 在 root 的 `~/.bashrc` 里设置 `PYTHONDONTWRITEBYTECODE=1`（宿主机挂载目录里的 checkout 不会出现 root 所有的 `__pycache__`）和 `DISABLE_AUTOUPDATER=1`，并加载 key 文件，之后才放进去的也会加载；设置了 `LLM_GATEWAY_KEY` 且没有 `ANTHROPIC_BASE_URL` 时，让 Claude Code 走 AMD LLM 网关 |
| 框架 | 镜像里的 `/sgl-workspace/sglang`，原样保留：用每日镜像新建的容器里，run_docker.sh 已经把它切到当前的 main（第 2 步）；用指定的 `IMAGE` 新建的容器、以及再次进入的容器，代码保持原样。这里有未提交的改动时会提示，因为任务的 Setup 会因此停下，直到你自己处理（用指定 `IMAGE` 新建的容器里，就是镜像构建对打包文件的改动）；用 `--framework-dir` 可以改用宿主机上的 checkout（已有的原样使用，缺失时带完整历史克隆） |
| evalscope | 供 `scripts/eval_accuracy.sh` 跑所有精度测试集：装在单独的 venv `/opt/evalscope` 里（约一分钟、1.2 GB），并链接为 `/usr/local/bin/evalscope`。不装进镜像的 Python，否则会把 `datasets` 和 `fsspec` 降到比 sglang 自带的更低的版本；`PATH` 上已经有 `evalscope` 时保留原样 |
| node、cursor | node 在加 `--runner` 或 `--cursor` 时运行，供 `runtime/run.mjs` 使用：安装 Node.js（默认最新的 LTS，可用 `INFERPILOT_NODE_VERSION` 指定），按 nodejs.org 的校验和核对后解压到 `/opt/node/<版本>`，并链接到 `/usr/local/bin`。cursor 只在加 `--cursor` 时运行，供 `--backend cursor` 使用：用官方安装脚本装 Cursor CLI（`agent`）。已有的 Node.js 20+ 或 `agent` 保留原样。CLI 需要你自己登录，每个容器一次：`NO_OPEN_BROWSER=1 agent login`，在浏览器里打开打印出的链接；或者在宿主机上你的 key 文件里设置 `CURSOR_API_KEY` |
| 检查 | GPU、sglang、evalscope、API 访问、`GITHUB_TOKEN`，key 文件里设置的也算；加 `--cursor` 时还检查 Cursor CLI（只检测） |

只有 `/workspace/inferpilot` 还不存在时才会克隆；bootstrap 从不写入 key。

### 4. 交给它任务（容器内）：用 Claude Code 或 Cursor

Claude Code 在它的 Workflow 工具里运行 `inferpilot.js`。Cursor 没有这样的工具：在 Cursor 上，由 InferPilot 自己的 runner `runtime/run.mjs` 运行 `inferpilot.js`，每个角色都跑在 Cursor CLI 上。两种方式交给它的任务相同，就是[运行](#运行)一节里的参数：

| 用 | 第 3 步怎么配置 | 怎么启动 | 怎么交给它任务 |
|---|---|---|---|
| Claude Code | `--claude`（或不加选项） | `IS_SANDBOX=1 claude --dangerously-skip-permissions` | 在它的输入框里用一句话说出任务 |
| Cursor CLI 的对话 | `--cursor`，`agent login` | `cd /workspace/inferpilot && agent`，由它启动 runner | 在它的输入框里用一句话说出任务 |
| Cursor app | `--cursor`，`agent login` | 由它的 agent 启动 runner | 在 app 的对话里用一句话说出任务 |
| 在 shell 里直接跑 runner | `--cursor`，`agent login` | `node runtime/run.mjs inferpilot.js --backend cursor ...` | 把参数写进一个 JSON 文件 |

Claude Code 和 Cursor 的 agent（CLI 的对话、app）会替你主持运行：把运行提出的问题转给你，并按结果里的 `next` 发起接下来的步骤（见[运行](#运行)）；在 shell 里直接跑 runner 时，日志末尾会列出同样的步骤，由你来执行。

#### 用 Claude Code

```bash
cd /workspace/inferpilot && IS_SANDBOX=1 claude --dangerously-skip-permissions
```

`IS_SANDBOX=1` 让 Claude Code 以 root 身份自动批准权限，InferPilot 的 agent 需要这一点。第 3 步已经在 Claude Code 的配置里回答了首次启动的问题，所以它会直接打开输入框。如果还是出现了问题（第 3 步报告 `claude-config` 为 `skipped`），其中有三个按默认选项会卡住：

| 问题 | 回答 |
|---|---|
| Do you want to use this API key?（只在用 `ANTHROPIC_API_KEY` 时才问，网关 key 不会问） | **Yes**；默认的 No 会转去让你登录 |
| Is this a project you trust? | **Yes, I trust this folder**；默认选项会退出 |
| Bypass Permissions mode | **Yes, I accept**；默认选项会退出 |

有 key 时，首次启动不会要求登录。如果它让你选择登录方式（select a login method），或者显示 `Paste code here if prompted >`，说明这个 shell 里的 Claude Code 没有拿到 key：什么都不要输入，连按三次 Ctrl-C 退出，然后看第 3 步输出的最后几行，那里写明了缺什么。用 Claude 订阅、又没有 token（第 1 步）时，这个登录才是入口，而且每个新容器都要再登录一次：选择 *Claude account with subscription*，在任意浏览器里打开它打印出的 URL，登录后把页面上显示的授权码粘贴到这个提示后面。

启动完成后，最下面一行会一直显示 `bypass permissions on`：这是 `--dangerously-skip-permissions` 打开的模式，不是在问你问题。保持这个模式不变（Shift+Tab 会切换到执行命令前要等你确认的模式，InferPilot 的 agent 就会停住），在它上方的输入框里输入任务：

```
use /workspace/inferpilot/inferpilot.js with mode=scan, inferpilot_dir=/workspace/inferpilot
```

scan 不改任何代码。它写出框架当天的日报，覆盖上一份日报以来（没有时为最近 7 天）的情况：main 上的 AMD CI（每个失败的 job、从何时开始失败、日志里说了什么）、按 P0、P1、P2 分好的开放 AMD 用户 issue，以及 AMD 需要跟进的上游改动。它把其中的任务排好序写进 `exp/scan_<time>/task_queue.md`，每项附可以启动它的任务定义，并把去掉本机信息的日报写进 `exp/scan_<time>/daily_report.md`，并用 `GITHUB_TOKEN` 作为 issue 分享到 InferPilot 仓库（第 1 步；token 不能往那里写时，这个文件由你来分享）。一天一份日报就够了：已有不到 24 小时的日报时，scan 会先问你要不要重扫（回答 `rescan`）；`upstream_repos` 可以加上要跟进的其他仓库，比如 vLLM。要接手一个任务，并在复现之后停下来确认环境，就给出它的字段：

```
use /workspace/inferpilot/inferpilot.js with mode=issue, task=https://github.com/sgl-project/sglang/issues/41299,
inferpilot_dir=/workspace/inferpilot, model_path=<weights>, gpu_ids=0, stop_after=Reproduce
```

或者直接用一句话描述，让 Intake 阶段写出任务定义：

```
use /workspace/inferpilot/inferpilot.js with inferpilot_dir=/workspace/inferpilot, request="DeepSeek-V4.1 在 MI355X 上
decode 太慢，帮我优化"
```

如果缺了什么（这里缺的是每次测量都用的固定负载），这次运行什么都不启动，返回 `status: needs_input`，每个字段一个问题。在对话里回答后，Claude Code 会用同样的参数加上 `answers={"workload": ...}` 再次运行 `inferpilot.js`。[运行](#运行)一节列出了全部参数。

**没有可以输入的终端时**（agent 的 shell、作业调度器），仍然要用交互式会话，把它放进 tmux 里跑。`claude -p "use inferpilot.js ..."` 不能代替它：Workflow 工具在后台运行 `inferpilot.js`，print 模式几秒内就给出回答，随后结束进程，这次运行也随之终止，还会留下这台机器的锁。[`scripts/run_in_tmux.sh`](scripts/run_in_tmux.sh) 在 tmux 会话里启动 Claude Code，等到输入框出现后，把文件里的任务粘贴进去；遇到首次启动的问题（`claude-config` 没有回答它）或登录提示时会停下，并说明是哪一种：

```bash
command -v tmux >/dev/null || apt-get install -y tmux
echo 'use /workspace/inferpilot/inferpilot.js with mode=scan, inferpilot_dir=/workspace/inferpilot' > /workspace/task.txt
/workspace/inferpilot/scripts/run_in_tmux.sh /workspace/task.txt
tmux capture-pane -p -t inferpilot | tail -40
```

**runner 上的 Claude 角色。** 没有 Claude Code 会话时，runner 也可以让每个角色跑在 `claude -p` 上（所需的 Node.js 随 `--cursor` 或 `--runner` 安装），启动方式和下面 Cursor 的一样：

```bash
cd /workspace/inferpilot && mkdir -p exp
echo '{"mode": "scan", "inferpilot_dir": "/workspace/inferpilot"}' > exp/scan.json
nohup node runtime/run.mjs inferpilot.js --backend claude --args-file exp/scan.json --result-file exp/scan.result.json < /dev/null > exp/scan.log 2>&1 &
```

#### 用 Cursor

**在容器的 shell 里。** Cursor CLI 登录后（第 3 步），把任务参数写进文件，在后台启动 runner；它让每个角色跑在 `agent -p` 上：

```bash
cd /workspace/inferpilot && mkdir -p exp
echo '{"mode": "scan", "inferpilot_dir": "/workspace/inferpilot"}' > exp/scan.json
nohup node runtime/run.mjs inferpilot.js --backend cursor --args-file exp/scan.json --result-file exp/scan.result.json < /dev/null > exp/scan.log 2>&1 &
tail -f exp/scan.log    # Ctrl-C 只是退出查看，运行会继续
```

**查看运行状态。** runner 存活期间每 30 秒重写一次 `exp/scan.status.json`（状态、阶段、正在进行的 agent 调用及各自的最后一行和它出现的时间 `last_at`）；`updated` 超过两分钟没更新，说明 runner 已经不在了。`exp/scan.agents/` 保存每次 agent 调用的 prompt、完整的输出流和 stderr（`--agent-log-dir` 可以换个位置），`--show-text` 会把 agent 写的文字也打进日志。

**改变运行的停止点。** runner 在每个阶段边界读取 `exp/scan.control.json`（参数文件名换成 `.control.json`；`--control-file` 可以指定别的文件）：`{"stop_after": "<phase>"}` 在该模式的这个阶段之后停（这个 scan 是 `Collect`，feature 任务可以是 `Verify`），`{"stop_after": "now"}` 在当前阶段结束后停，`{"stop_after": ""}` 跑到结束。停止点变化时日志里会写一行；运行已经过了那个阶段、或者文件里不是 JSON 对象时，日志也会说明；运行开始时就已经存在的文件，要等它被改过之后才生效。只有 runner 会读控制文件，两个 backend 都一样；在 Claude Code 里直接跑（由它的 Workflow tool 执行）时没有控制文件，停止点就是启动时的 `stop_after`。

**停止一个运行。** `kill <runner 的 pid>`（SIGTERM；日志第一行给出这个 pid），或者对前台启动的 runner 按 Ctrl-C，会停掉它的所有 agent 以及它们启动的一切，包括用 `setsid` 或 `nohup` 启动的 server（它们都继承了标明这个 runner 的 `INFERPILOT_RUN_TAG`），释放机器锁，并在 `--result-file` 里写下 `"status": "interrupted"`。以其他方式死掉的运行（`kill -9`、被 OOM killer 杀掉）会留下它的进程：下一个运行会把它们列出来（任务容器里的也包括在内），`--kill-orphans` 会先停掉它们。容器重启会带走其中的进程，但会留下机器锁，而新容器判断不了锁的 runner 是否还在：用 `python3 scripts/run_lock.py release --force` 释放它。

文件里写的就是上面交给 Claude Code 的那些参数，格式为 JSON。在复现之后停下的那个 issue 任务写成 `{"mode": "issue", "task": "https://github.com/sgl-project/sglang/issues/41299", "inferpilot_dir": "/workspace/inferpilot", "model_path": "<weights>", "gpu_ids": "0", "stop_after": "Reproduce"}`；用一句话描述的写成 `{"inferpilot_dir": "/workspace/inferpilot", "request": "..."}`。`exp/scan.log` 按这个模式的路线给每个阶段编号，并附上到目前为止的用量（`=== PHASE 2/3 [##-]: Dispatch ===  so far: 1 agent call, 410k tokens, 4 min`）；写出每次 agent 调用，调用里的每个工具调用开始时各写一行（执行的命令、读写的文件）；安静五分钟后写一行"仍在运行"；每次调用结束时写出用时和 token（按 CLI 的计数，含缓存），整个运行结束时写一行总结；`exp/scan.result.json` 是最终结果：其中有 `status`，以及 `next`，即运行结束后接下来做什么。日志末尾列出同样的步骤（`=== NEXT: ...`）：只有你能回答的问题；接下来可以发起的每次运行，参数写在你那个文件旁边（`exp/scan.start-1.json` 是 scan 排名第一的任务，`exp/<name>.again.json` 是同一个任务再跑一次、待你填好回答），并附上启动它的命令；以及只有你能做的事。

**从 Cursor app。** 它的 agent 同样不会自己运行 `inferpilot.js`：它把任务写进文件、在后台启动 runner，并随时读取运行进展，具体做法写在 checkout 根目录的 [`AGENTS.md`](AGENTS.md) 里。把一个 InferPilot 的 checkout 作为 app 的工作区打开，Cursor 就会把这个文件当作规则加载：可以通过 Remote-SSH 打开 GPU 机器上的任意一个克隆（比如你运行 run_docker.sh 的那个），agent 会在你的容器里执行每条命令；也可以直接连接到容器，打开 `/workspace/inferpilot`。然后像对 Claude Code 一样用一句话说出任务；agent 会追问这个模式需要而你没说的内容，并告诉你运行在哪里。它默认用 Cursor CLI；想让角色跑在 `claude -p` 上，跟它说一声即可（需要 Claude Code 和 Node.js：bootstrap 加 `--cursor --claude` 或 `--runner`）。Cursor CLI 自己的对话界面（容器里的 `agent`）也读取同一个文件。

角色跑在 Cursor 上时，代码和提示词会发到 Cursor 的服务，而不是 AMD 的 LLM 网关，并计入账户的用量。在容器外，每条命令都要通过 `docker exec -i <容器> bash -ic '...'` 执行：只有交互式 shell 才会读 `~/.bashrc`，它负责加载 key 文件、把 `claude` 加进 PATH。普通的 `bash -c` 两样都没有，Claude 角色会失败，scan 也会在没有 `GITHUB_TOKEN` 的情况下运行。

### 5. 运行结束后（容器内）

每次过了 Setup 的运行，都在 `/workspace/inferpilot/exp`（git 会忽略它）里有自己的目录，名字是 `<mode>_<slug>_<time>`：模式、任务的简短名字和 UTC 开始时间，例如 `issue_41299-glm-dcp_20261106-071502Z` 或 `scan_20261106-064327Z`。`exp/index.md` 列出这个容器里的每次运行和它的结局。一个运行目录里有：

| 文件 | 内容 |
|---|---|
| `run.json` | 任务定义、intake 的假设，以及所在的容器（任务的分支在那个容器里） |
| `validation_report.md`、`run_report.md` | 报告：PR 准备好时是前者，其他结局是后者（运行结果中的 `report_path` 给出路径） |
| `experiment_ledger.md` | 环境、基线和每一次测量 |
| `logs/` | 每次启动服务或 benchmark 一个日志，`<tag>_<time>_XXXXXX.log`，由 `mktemp` 生成，旁边是它的 `.pid` 和 `.exit` |
| `pr_body.md`、`patches/` | PR 描述，以及导成 patch 的分支 commit |
| `reply_draft.md`、`reply_check.md` | issue 的回复草稿（由你来发），以及 reviewer 对照运行记录核对它的结果 |
| `component_issue.md` | 根因在框架之外的 AMD 组件（aiter、MoRI、ROCm、PyTorch 的 ROCm 版）时，给那个组件的仓库起草的 issue（由你来提） |
| `knowledge_pr.md`、`knowledge.patch` | curator 向 InferPilot 仓库提的 PR 的描述，以及它的改动；`scripts/propose_knowledge.py --patch` 之后可以在任何 checkout 里把它提出来 |
| `daily_report.md`、`task_queue.md` | scan 的日报，已作为 issue 分享，或由你来分享（见下文）；带本机细节的排好序的任务，每项附可以启动它的任务定义，旁边是各个来源找到的内容（`<source>.json`） |

scan 的 `daily_report.md` 就是为分享而写的，`scripts/daily_report.py check` 已经确认它不含本机信息。`GITHUB_TOKEN` 能写这个仓库时（第 1 步），director 会把它作为标题为 `InferPilot daily report <date>` 的 issue 分享到 InferPilot 仓库（当天的已经在那里时除外），结果里会给出它的链接。没有这样的 token、而当天的日报还不在那里时，在 GitHub 上 InferPilot 仓库里新建一个 issue，用这个标题，把整个文件粘贴进去；或者设好 token 后运行 `python3 scripts/share_daily_report.py exp/scan_<time>/daily_report.md`。Dispatch 失败的 scan 什么都不分享：它留下的日报可能不完整。文件第一行是一个隐藏标记，下一次 scan 不管在哪个容器里，都能靠它在那里找到这份日报，并标出之后的变化。

push 之前，先读完每一行改动，再按 `validation_report.md` 中 *Before pushing* 一节亲自重跑 PR 的测试和测量：这一节会先占住机器并确认所有 GPU 空闲，按各自的 commit 重新建出两边的代码树，检查每条命令导入的是哪份代码，并按运行时的方式测量，因为到那时运行已经释放了机器，也删掉了它的基线 worktree。新 PR 的描述在 checklist 下面有一行 `Developed with the [InferPilot](https://github.com/At1a8/inferpilot) AI agent; reviewed and measured by AMD engineers.`，说的就是你做了这些；给已有 PR 写的验证评论没有这一行。你的结果和描述里的数字相差超出正常波动时，把描述里的换成你的。没重跑过的 PR 不要 push，这一行也要一字不改：在 GitHub 上搜索时就是靠它找出所有 InferPilot 的 PR。

InferPilot 从不 push。任务的分支 `inferpilot/<slug>` 在 `/sgl-workspace/sglang` 里，报告给出确切的 `git -C /sgl-workspace/sglang push ...`：可以在容器里用有 push 权限的凭据执行它。运行目录随容器保留和删除；要保留某次运行，或者要在宿主机上 push（你的 git 凭据在那里），就在宿主机上把它复制出来（`<name>` 是容器名，`<run>` 是运行目录的名字）：

```bash
docker cp <name>:/workspace/inferpilot/exp/<run> ~/
```

复制出来的文件属于你而不是 root。在宿主机上你的 sglang checkout 里，先用报告给出的基础 commit 执行 `git checkout -b inferpilot/<slug> <base>`，再执行 `git am ~/<run>/patches/*.patch`，然后 push。

### 重复执行与选项

`inferpilot.bootstrap` 的每一步都会报告 `kept`、`installed`、`updated`、`added`、`created`、`skipped` 或 `failed`；最后有一份汇总，"下一步"只列出仍然缺少的内容，任何一步失败时退出码为 1。在新容器里，它会安装 Claude Code 和 evalscope（各需要一两分钟），并写好 Claude Code 的配置以及 git 和 shell 的设置。`--claude-only` 只运行 key 和 Claude Code 相关的步骤，`--check` 只运行检查；`--claude` 和 `--cursor` 选择安装什么：两个都不加时装 Claude Code；只加 `--cursor` 时装 Node.js 和 Cursor CLI；两个都加时三样都装。`--runner` 在 Claude Code 之外再装 Node.js。在容器里，`cd /workspace && pip install -v git+https://github.com/At1a8/inferpilot` 同样会执行第 3 步，把 InferPilot 克隆到 `/workspace/inferpilot`（加 `-v` 时 pip 才显示输出；用了缓存的 wheel 时会跳过）。

| 变量 | 使用者 | 默认值 |
|---|---|---|
| `IMAGE` | `scripts/run_docker.sh` | `IMAGE_VARIANT` 下最新的每日 `rocm/sgl-dev` 镜像（访问不了 Docker Hub 时用本地最新的） |
| `IMAGE_VARIANT`、`NAME`、`EXTRA_MOUNTS`、`MOUNT_HOME` | `scripts/run_docker.sh` | 根据 gfx942/gfx950 自动检测（回退到 MI35x），ROCm 10，没有则 7.2.4，再没有则 7.2.0；新容器的 `inferpilot_<你的用户名>_<月日>_<随机>`；额外要挂载的目录，用空格分隔；设为 `0` 时不挂 `/home` |
| `DOCKER_SOCKET` | `scripts/run_docker.sh` | 不设置；设为 `1` 时额外挂载宿主机的 Docker socket 和 `docker` |
| `DETACH`、`PRIVILEGED` | `scripts/run_docker.sh` | 不设置：没有终端时在后台启动，否则打开交互 shell；`PRIVILEGED=0` 去掉 `--privileged` |
| `INFERPILOT_LOCK_FILE` | `scripts/run_lock.py` | `/dev/shm/inferpilot-run.lock`，这台机器"一个任务"的锁 |
| `INFERPILOT_HOME` | bootstrap | `./inferpilot`，或你所在的 checkout |
| `INFERPILOT_KEYS` | bootstrap | root 的 `~/.inferpilot_env`，也就是 run_docker.sh 挂载你那份的位置：容器里的 shell 加载的 key 文件 |
| `INFERPILOT_HOST_HOME` | `scripts/run_docker.sh`（挂载了 `/home` 时）、bootstrap | 在挂载了 `/home` 的容器里，指你在那里的 home，bootstrap 到这里找 key 文件（没有它时，用容器名对应的那个用户的 home） |
| `INFERPILOT_CONTAINER` | `scripts/run_docker.sh` 设置；bootstrap、`scripts/run_lock.py` | 容器名（没有它时，挂载了 socket 就问 Docker）：bootstrap 到它对应的那个用户的 home 里找 key 文件，任务锁把它记为持有者 |
| `INFERPILOT_REPO_URL`、`INFERPILOT_REF` | bootstrap | `https://github.com/At1a8/inferpilot.git`，它的默认分支 |
| `CLAUDE_VERSION`、`CLAUDE_BIN_DIR` | bootstrap | `latest`，`~/.local/bin` |
| `CLAUDE_CONFIG_DIR` | Claude Code、bootstrap | 不设置（`~`）：Claude Code 自己的变量，指它的配置所在的目录；bootstrap 把首次启动的回答写在那里 |
| `EVALSCOPE_VERSION`、`EVALSCOPE_VENV` | bootstrap | `latest`，`/opt/evalscope` |
| `INFERPILOT_CLAUDE`、`INFERPILOT_CURSOR`、`INFERPILOT_RUNNER`、`INFERPILOT_NODE_VERSION` | bootstrap | 不设置；设为 `1` 时分别等同于 `--claude`、`--cursor`、`--runner`（设为 `0` 表示关闭；前两个都没有时装 Claude Code）；默认装最新的 Node.js LTS（`22` 表示最新的 22.x，也可以写确切的 `v22.11.0`） |
| `INFERPILOT_CLAUDE_BIN`、`INFERPILOT_CURSOR_BIN` | `runtime/run.mjs` | `PATH` 上的 `claude`、`agent`：`--backend claude` 和 `--backend cursor` 运行的 CLI |
| `INFERPILOT_GIT_NAME`、`INFERPILOT_GIT_EMAIL` | `~/.inferpilot_env`、bootstrap | 不设置；容器里 root 提交时用的身份 |
| `GITHUB_TOKEN` | `~/.inferpilot_env`、bootstrap、访问 GitHub 的脚本 | 不设置；唯一的 GitHub token，强烈建议设置（第 1 步）：用它读取 GitHub API；它对 InferPilot 自己的仓库有 Contents、Pull requests、Issues 读写权限时，curator 用它提 PR，director 用它分享 scan 的日报 |
| `LLM_GATEWAY_KEY` | `~/.inferpilot_env`、容器的 shell 配置 | 不设置；设置后 Claude Code 走 AMD LLM 网关，除非 `ANTHROPIC_BASE_URL` 指定了别的地址 |
| `INFERPILOT_FRAMEWORK_DIR`、`INFERPILOT_FRAMEWORK_REPO` | bootstrap：`--framework-dir`、`--framework-repo` | 镜像里的 `/sgl-workspace/sglang`；sglang |
| `INFERPILOT_SKIP_BOOTSTRAP` | bootstrap | 不设置；设置后安装这个包时没有任何副作用（CI、构建镜像） |

## 运行

InferPilot 是一个工作流：`inferpilot.js` 负责控制流，`roles/` 是 agent 的指令，`knowledge/` 是方法，`templates/` 是产物模板。在 Claude Code 里，用一句话说出 `inferpilot.js` 和它的参数即可，就像 [第 4 步](#4-交给它任务容器内用-claude-code-或-cursor)那样，由它的 Workflow 工具执行。在其他地方，由 InferPilot 自带的 runner `runtime/run.mjs`（Node 20+）执行，每个角色交给一个命令行 agent：`--backend claude` 运行 `claude -p`，`--backend cursor` 运行 Cursor CLI（`agent -p`），用 `agent login` 登录的账号或 `CURSOR_API_KEY`。在容器里，`python3 -B -m inferpilot.bootstrap --cursor` 会装好 Node.js 和 Cursor CLI；`NO_OPEN_BROWSER=1 agent login` 会打印一个链接，在别处的浏览器里打开就能登录。dry run 用的也是这个 runner。

```bash
node runtime/run.mjs inferpilot.js --backend cursor \
  --args '{"mode":"scan","inferpilot_dir":"'"$PWD"'","framework_dir":"/path/to/sglang"}'
```

`--model`、`--args-file`、`--result-file`、`--concurrency`、`--agent-timeout-ms`、`--schema-retries`、`--kill-orphans`、`--control-file`、`--agent-log-dir` 和 `--show-text` 的说明在 `runtime/run.mjs` 开头。

| 参数 | 含义 |
|---|---|
| `inferpilot_dir` | 这个 checkout 的绝对路径（必填） |
| `request` | 用一句话描述的任务，任何语言都可以；Intake 阶段会把它整理成下面这些字段（`roles/intake.md`），同时给出的字段优先 |
| `answers` | 对 `needs_input` 结果的回答，格式为 `{"<field>": <value>}`，与之前相同的参数一起传入 |
| `mode` | `feature` \| `issue` \| `ci` \| `perf` \| `scan`（必填；没给时从 `request` 推断） |
| `task` | 上游 PR / 设计、issue URL、CI job / 簇，或性能目标（`scan` 以外必填） |
| `goal` | 什么算完成；可以测量的模式要写成可测量的；spec 和性能计划都要回应它 |
| `framework_dir` | 每个任务在其中工作的框架 checkout，任务从当前的 main 开自己的分支（sglang 默认用镜像里的 `/sgl-workspace/sglang`，镜像就是从这里安装 sglang 的） |
| `model_path` | 基线和各项门禁运行的模型（`feature` 和 `perf` 必填；`issue` 和 `ci` 也必填，除非 `stop_after` 让运行在修复之前停下：修复改了 server 会运行的代码时，要对照 base 测 gsm8k，即 G2） |
| `framework`、`platform`、`gpu_ids` | 目标与环境（`sglang`；`gpu_ids` 为空时由 Setup 挑选空闲的 GPU，之后每个角色都通过 `HIP_VISIBLE_DEVICES` 使用这组 GPU） |
| `workload` | 每次测量都使用的基准负载，例如 `{"isl":8192,"osl":1024,"conc":64}`；`perf` 必填，在 Setup 时固定 |
| `server_script` | 你自己的启动 server 的脚本，用于 `model_path`。InferPilot 会设置 `PORT`、`HIP_VISIBLE_DEVICES`、`PYTHONPATH` 和 `EXTRA_ARGS`（特性的开关参数，baseline 时为空），脚本要把 `--port $PORT` 和 `$EXTRA_ARGS` 传给 server；`feature` 和 `perf` 必填（[`templates/task_definition.md`](templates/task_definition.md) §1.1）。没有的话可以回答 `template`：用 InferPilot 的 `templates/harness/server.sh`，按模型在 sglang cookbook 里的配方启动 |
| `client_script` | 你自己的压测脚本，对 `PORT` 上的 server 跑负载，日志写到 `OUT_DIR`；它就是基准，原样运行；`feature` 和 `perf` 必填：每个 feature 都要和 baseline 比性能（G5），声称了收益就验证这个收益，没有声称就验证没有变慢；`issue` 和 `ci` 也必填，除非 `stop_after` 让运行在修复之前停下：修复改了 server 会运行的代码时，要用它对照 base 测性能，不能变慢（G5）。没有的话，feature 和 perf 任务可以回答 `template`：用 InferPilot 的 `templates/harness/perf.sh`（sglang 的 serving 压测）跑 `workload`，选它的任务要给出完整的 `workload`；feature 用它只判定有没有比 baseline 慢，不验证源 PR 的收益 |
| `accuracy_script` | 你自己的精度评测脚本，对 `API_URL` 上的 server 测精度，结果写到 `RESULT_ROOT` 下，每一组都原样运行；可选，不给时用 InferPilot 的 `templates/harness/accuracy.sh`（`scripts/eval_accuracy.sh`） |
| `harness` | InferPilot 模板的参数取值，例如 `{"CONTEXT_LENGTH": 8192, "ENABLE_THINKING": 0}`（`python3 scripts/harness.py show` 列出全部参数）：每个参数先取任务给的值，没有则取 feature 源头测试里用的值（PARD-2 是 vLLM 的 `--max-model-len 4096`），server 的参数再取模型的 sglang cookbook 配方，最后才用模板默认值（[`templates/task_definition.md`](templates/task_definition.md) §1.2） |
| `container` | 只在 InferPilot 运行在宿主机上时使用：框架所在的容器，通过 `docker exec` 访问 |
| `exp_root` | 运行目录放在哪里（默认 `<inferpilot_dir>/exp`，git 会忽略它）。放在宿主机挂载目录里时，运行目录比容器活得久，换一个新容器重试时能从 `RUN_DIR/branch.bundle` 取回任务的提交，不用再写一遍：容器可能中途消失时（会被抢占的节点、作业到了时间上限）就这样用 |
| `kernel_backend` | `none`（默认）\| `geak` \| `command`：kernel hand-off 交给谁 |
| `kernel_backend_dir` | `geak` 后端：GEAK checkout，其中的 kernel lane 作为外部程序运行 |
| `kernel_backend_command` | `command` 后端：带 `{task_dir}`、`{out_dir}`、`{gpu_ids}`、`{budget}` 占位符的 shell 命令；用于 `geak` 时替换默认命令 |
| `kernel_budget` | kernel 后端的优化轮数（默认 6），即命令里的 `{budget}` |
| `apply_prs` | `perf`：要应用并做 A/B 的候选 PR |
| `sources`、`since` | `scan`：`ci`、`issues`、`upstream`（默认）以及 `hardware`、`perf` 中的任意几个；时间窗口的起点（默认为上一份日报） |
| `image`、`image_ok` | 任务必须运行的镜像（默认是容器对应 ROCm 和 GPU 下最新的每日镜像；不是的话 Setup 会停下来问）；为 `true` 时仍在容器现有的镜像上运行 |
| `rescan`、`upstream_repos` | `scan`：为 `true` 时，即使已有 24 小时内的日报也重新扫描；要跟进的其他上游仓库，写成 `[{"name": "vllm", "url": "https://github.com/vllm-project/vllm"}]` |
| `bisect_fixed` | `issue`：为 `true` 时，若报告者的 commit 失败而 main 通过，就以复现脚本为测试在两者之间做 bisect，找出修复它的提交 |
| `reported_on` | `issue`：报告里这个问题所在的模型和启动配置（如 `DeepSeek-V4.1, --tp-size 8`）。一次运行如果发现它要处理的问题不在自己准备好的配置上，会在 Reproduce 之前停下，并在结果里把这个字段交给下一次运行（`next.rerun_with`；报告里其他问题用 `next.start`）：没有给 `gpu_ids` 时，Setup 按这个配置需要的数量选 GPU，并按它写启动脚本 |
| `confirm_leads` | 默认 `false`：线索（角色遇到的、不属于本任务的问题）在报告里列出，附上复现方法，Spinoff 不占用 GPU；为 `true` 时，在报告前把每条线索放到 base 提交上确认，能复现的起草 issue |
| `stop_after` | 在该模式的指定阶段之后停下（例如 `Design`、`Reproduce`）；填了该模式没有的阶段，会作为问题返回 |
| `resume_from`、`run_dir` | `feature`：人审阅通过 `design.md` 后，用 `implement` 继续一个停在 Design 的运行 |
| `auto_approve_design` | `feature`，默认 `true`：architect 标记需要人审的设计和 L3 任务直接进入 Implement，报告会写明设计没有经过人审。设为 `false` 时，这样的设计会停在 Design 等人审（`awaiting_design_review`）。`stop_after` 仍然生效 |
| `held_out`、`guards`、`sweep` | 任务 workload 和 goal 之外的性能检查，默认都不开启：`held_out` 在 held-out 工作点（一半并发）上再测一次收益，并要求收益在那里同样成立；`guards` 在 goal 指定的守护负载之外，再加 InferPilot 自己选的守护负载；`sweep` 测 workload 之外的点（更多并发或长度） |
| `tune` | `feature`，默认 `false`：功能按目标 PR 或设计的配置运行（FLy 就是 vLLM 的 window 2、threshold 0.3、top-k 3），并要在这个配置下达标；设为 `true` 时增加一个通过测量来选参数的工作包 |
| `long_context`、`stability` | 默认门禁之外的正确性门禁，默认都不开启：`long_context` 运行 G3，即任务自己的长上下文测试（`long_context_script`：在长 prompt 上做 needle-in-a-haystack 测试）；`stability` 运行 G4，即对同一个 server 把 `client_script` 一遍接一遍地连续运行 48 小时以上（`scripts/stability.sh`；任何模式下任务都要给出自己的 `server_script` 和 `client_script`，缺哪个就问哪个：G4 不会自行改用模板），一次运行里只跑一次：之后代码再有改动不会重跑，报告会写明它跑在哪个 commit 上。带它的一轮 Verify 会申请约 6 天的时间上限，只有 G4 失败、基线要跑同样的测试作对照时才会用满 |
| `long_context_script` | 你自己的长上下文测试脚本，对 `PORT` 上的 server 跑测试，把每个请求的结果写到 `OUT_DIR`；G3 在 baseline 和改动上原样运行它，至少跑两个并发（`CONCURRENCY`）；打开 `long_context` 时必填，任务没给时会提问：InferPilot 没有对应的模板（[`templates/task_definition.md`](templates/task_definition.md) §1.1） |
| `review_model` | reviewer 使用的模型，随它的 agent 调用一起传入（InferPilot 的 runner 把它作为命令行工具的 `--model` 传入；在 Claude Code 里取决于它的 Workflow 工具） |
| `max_fix_rounds`、`max_investigate_rounds`、`max_review_rounds`、`max_work_packages` | 预算 |
| `agent_retries`、`agent_timeout_ms` | agent 调用出错时会重试（重试前先检查失败的那次留下了什么），超时则不重试：超时的 agent 可能还在运行，会被记入报告里的待人工处理清单，它所在的工作包也不再开新一轮、不做 rollback。默认超时 3 小时，verify engineer、performance engineer、investigator 和 issue 的复现为 6 小时；Verify 一轮或一次复现需要更长时间时，会说明需要多久并获得一次延长，最多到上限的 2 倍，除非给了 `agent_timeout_ms` |

除 `inferpilot_dir`、`request`、`answers`、`exp_root`、`review_model` 和各项预算外，其余参数都属于任务定义；每次已启动的运行，其结果都会记录所用的任务定义（`task_def`）、每个字段的来源（`provenance`）以及 intake 的假设（`assumptions`）。

人工检查点：Intake 的提问（`needs_input`），以及移植任务的 client 跑不了源 PR 的场景时 Spec 的提问；issue 或 ci 的运行走到修复却还没有模型或 client 时，会先问要用哪个，用的是 InferPilot 的 client 而 `workload` 只给了一部分时也会先问；任何 `stop_after`；`auto_approve_design` 设为 `false` 时，L3 任务和 architect 标记的设计会停在 Design（`awaiting_design_review`；默认直接继续，报告会写明设计没有经过人审）；被 reviewer 标为 `blocked` 的审阅会在 PR 之前停下；PR engineer 只返回确切的 `git push` 而不执行；issue 回复是运行目录里的草稿。只要建好了运行目录，无论结局如何，运行都会以一份报告结束；停在 Intake（提问或拒绝），或在 Setup 建目录之前就结束的运行（`busy`），只返回运行结果。

每个结果最后都有 `next`，说明运行结束后接下来做什么，对 Claude Code、Cursor 的 agent 和 runner 的日志都一样：`say` 是写给你的一句话；`ask` 是只有你能回答的问题；`rerun_with` 是再跑一次之前要设置的参数，其余参数不变（answers 会和已有的回答合并）；`start` 是它建议发起的新运行及其参数：scan 排在前面的任务，issue 报告里的其他问题；`user` 是只有你能做的事：推送、发出回复、分享 director 没能分享的日报。

**一台机器一个任务。** 运行在创建任何东西之前先占住这台机器：锁放在 `/dev/shm`，所有用 `--ipc=host` 启动的容器都和宿主机共用它，其他机器看不到；运行在写完报告后释放。另一个任务占着机器时，新的运行会立即以 `busy` 结束，并说明是谁占着。除 scan 外，每种模式还要求机器上的 GPU 都没有被别的任务占用（`scripts/gpu_status.py --check all`）：Setup 时发现有别的任务占着卡，运行同样以 `busy` 结束；之后的性能测量前发现，这次测量就报告为没跑成。如果持有锁的运行已经结束，或者它的 runner（`runtime/run.mjs`，从同一个容器里看）已经不在，或者在两倍于最长 agent 调用（按它能拿到的最长加时算）的时间里（默认 24 小时）没往运行目录写过任何东西，下一个运行会直接接管。另一个容器里的运行不会因为没动静而被接管，因为这里看不到它的运行目录；确认那次运行已经不在之后，用第二条命令释放机器，和释放意外中断的运行留下的锁一样：

```bash
python3 scripts/run_lock.py status            # 谁占着这台机器
python3 scripts/run_lock.py release --force   # 那个运行已经中断时，强制释放
```

框架自带的 skill（sglang 的 `.agents/skills/`）以引用方式使用，从不复制：每个角色从任务 worktree 中读取 [`knowledge/frameworks/sglang/skills.md`](knowledge/frameworks/sglang/skills.md) 为它列出的 skill，因此与它正在修改的代码版本一致；ROCm 上的差异见 [`knowledge/platforms/rocm_skills.md`](knowledge/platforms/rocm_skills.md)。

## 脚本

全部是只用标准库的 Python；访问 GitHub 的脚本读取 `GITHUB_TOKEN`（见第 1 步；没有 token 时，GitHub 对每个 IP 每小时只允许 60 次请求、每分钟 10 次搜索）。

| 脚本 | 任务来源 |
|---|---|
| `scripts/scan_issues.py` | 开放的 AMD issue，附关联 PR（以及其中哪些会关闭这个 issue）、回复情况和预计的 60 天自动关闭日期 |
| `scripts/scan_ci.py` | 从 main 上 AMD CI 自己的运行和日志读出现状（每个失败 job：从何时开始失败、上次通过的时间、失败的测试、第一条错误），以及 Failure Tracker 的失败簇 |
| `scripts/eval_accuracy.sh` | 用 evalscope（bootstrap 会安装）测一个正在运行的 server 的精度，即 G2 门禁（每个测试集比基线低不超过 1 个点）：默认跑 gsm8k 全部 1319 题；任务说明改动可能有损时，再加跑 gpqa_diamond 和 simple_qa（LLM judge 打分）；其他测试集按名字指定；每次都重新采样，从不用 evalscope 的缓存；有题没拿到答案的测试集不出成绩；key 只从环境变量读取 |
| `scripts/stability.sh` | G4 的负载，在 `stability` 打开时使用：对同一个 server 把这次运行的 `client_script` 一遍接一遍地运行，直到过了 `HOURS`（G4 用 48；正在跑的那一遍会跑完），每一遍在 `OUT_DIR/stability.jsonl` 里记一行：client 的退出状态和结果，以及每张 GPU 和主机已用的内存。第一遍失败时就结束 |
| `scripts/harness.py` | InferPilot 的模板（`templates/harness/`）：`show` 列出 server、性能压测 client 和精度三个模板的参数和默认值；`render` 生成一次运行要用的脚本（没有自己脚本的用户选了模板的 server 或 client、任务没带精度脚本时的精度脚本、Spec 的备用 client），参数先取任务给的值，再取 feature 源头测试用的值，server 的参数再取模型的 sglang cookbook 配方（`--cookbook`），最后用默认值，并写出 `harness.json`，记录每个值的来源 |
| `scripts/scan_upstream.py` | 已合入的上游提交：新增的 CUDA 门控、CUDA 优先的特性、新模型 |
| `scripts/audit_platform_gates.py` | 换到新架构时需要重新检查的平台门控（在 gfx1250 上为真的能力判断、wave32 假设） |
| `scripts/audit_host_syncs.py` | 热路径上的 host-device 同步和拷贝候选点（供 profile 参考的线索） |
| `scripts/baseline_cache.py` | 把基线测量结果保存在 `<exp_root>/baseline_cache` 里复用，键是框架 commit、模型文件、server、client 和精度脚本的内容、负载、精度测试集连同 `scripts/eval_accuracy.sh` 的内容、它读取的设置和 evalscope 版本、GPU、正在运行的镜像，以及测量时遵循的规则（所有 GPU 空闲）；Setup 命中 7 天内的结果，精度和性能都不再重测，只保存通过了的基线，命中时仍会用几道题检查精度评测环境 |
| `scripts/gpu_status.py` | 按 HIP 编号列出 GPU（也就是 `HIP_VISIBLE_DEVICES` 用的编号，即 KFD 节点的顺序，rocm-smi 的 Device 编号可能与之不同），以及每张卡的 VRAM% 和 GPU%（与 rocm-smi 显示的一致）、任何容器里在卡上占用显存的进程；只要有卡在用（任一项达到 5%），`--check all` 就失败，除 scan 外的每种模式在开始时和每次性能测量前都会运行它 |
| `scripts/leak_scan.py` | 一个分支会把这台机器的哪些信息公开出去（上游代码里没有出现过的本地路径、主机名、内部站点、`root` 提交），检查范围是每个提交新增的行（后来的提交删掉的也算，推送会把历史一起公开）、提交信息和 PR 描述；PR engineer 交出分支前会运行它 |

`scripts/restore_owner.sh`（POSIX sh）把 root 在容器里写到宿主机挂载目录中的文件还给属主（`/workspace` 里的不用还）；每个 agent 在 git 写操作之后都会运行它（[`knowledge/env_hygiene.md`](knowledge/env_hygiene.md)）。`scripts/check_framework_skills.py --framework-dir <dir>` 拿 skill 索引和框架 checkout 对照：索引里提到但已经不存在的 skill，以及索引还没收录的新 skill（scout 每次 scan 都会运行它）。`scripts/find_skill.py "<query>"` 按匹配程度列出标签、id 或特性名出现在请求或任务标题里的 feature skill（intake 和 director 会运行它）。`scripts/latest_image.py [--variant rocm10-mi30x]` 输出最新的每日 sglang 镜像（`run_docker.sh` 从它启动；director 拿运行所用的镜像和它比较）。`scripts/run_lock.py` 保证一台机器同时只跑一个 InferPilot 任务（`acquire`、`release`、`status`）。`scripts/daily_report.py check` 检查日报能否公开（没有密钥、本地路径、主机名或内部站点）、每个 PR 和 issue 是否除了编号还写了标题、是否放得进一个 issue；`latest` 找出在 InferPilot 仓库里分享过的最新一份日报，下一次 scan 不管在哪个容器里，都从它接着扫。两者都只读。`scripts/share_daily_report.py` 用 `GITHUB_TOKEN` 把通过检查的日报作为 issue 分享到 InferPilot 仓库，以日报的日期为标题，当天的日报已经在那里时不再重复发（Dispatch 成功的 scan 结束时，director 运行它）。`scripts/propose_knowledge.py` 把 curator 在 `knowledge/` 下的改动，放在一个临时 worktree 里基于当前的 main 做成从 `inferpilot/knowledge/<run>` 到 main 的 PR，然后把 checkout 里的 `knowledge/` 恢复原样，这样之后的运行只会读到合并过的知识。`scripts/weekly_report.py` 收集周报（`templates/weekly_release.md`）要统计的内容：一个时间范围里 InferPilot 合入、关闭或仍开着的 PR，范围默认从 7 天前的 00:00 到现在（UTC+8），也可以用 `--since` 和 `--until` 指定，范围起止时的 main 和两者之间的改动，以及来自 InferPilot 工作的 sglang PR。它只读。

## 测试

提交前运行 `tests/run_all.sh`：离线、不需要 GPU，十秒左右跑完（需要 Python 3.10+ 和 Node.js 20+；装了 shellcheck 就用上）。CI 在 GitHub 的机器上运行同样的测试（[`.github/workflows/tests.yml`](.github/workflows/tests.yml)），在 pull request 页面上每个部分各一项检查：`changes`（这个 pull request 涉及哪些任务组，摘要里写明每个文件的原因）、基础测试的五个步骤、每个任务组一项（改动没涉及时显示为 skipped）、在 Python 3.10 和 Node.js 20 上完整运行一遍 `tests/run_all.sh`，以及 `tests-finish`：所有实际运行的 job 都通过时才变绿。推到 main、每天夜里和手动触发时，每个组都跑。

基础测试每次改动都跑：每个脚本的语法、shellcheck、runner、两个 backend 的对照，以及下表中保持各部分一致的检查。任务组（`feature`、`issue`、`ci`、`perf`、`scan`、`kernel`）加上该任务的 dry-run 场景和它所用脚本的单元测试，只在改动涉及该任务时才跑：该任务会执行的 `inferpilot.js` 代码、它调用的角色，或这些角色读的文件（[`tests/groups.json`](tests/groups.json)）。改动所有任务共用的部分（`runtime/`、dry run 和它的 fixtures、CI、`inferpilot.js` 主流程执行的代码）时，每个组都跑。

```bash
tests/run_all.sh                                  # 基础测试和每个组；失败时会说明是哪一步、怎么失败的
tests/run_all.sh --changed                        # 基础测试和你自 origin/main 以来的改动涉及的组，并说明原因
tests/run_all.sh --group perf,kernel              # 基础测试和这几个组
node tests/dry_run.mjs --only scan,roles          # 只跑名字以 scan: 和 roles: 开头的 dry-run 场景
```

| 保持一致的内容 | 由谁检查 |
|---|---|
| 两个 backend：`claude` 和 `cursor` 上的提示词、阶段、重试、超时、模型和结果都相同 | `tests/backend_parity.mjs` |
| 两份 README：逐节对照标题、命令、行内代码、数字、链接和表格 | `tests/test_readme.py` |
| workflow 与角色：进入的每个阶段都在 `meta.phases` 里；每种模式的路线就是它实际走的路线，也是两份 README 列出的路线；回答必须有的每个字段都写在对应角色的 PHASE 一节里 | `tests/dry_run.mjs` |
| 代码与文档：setup、`run_docker.sh` 和 runner 读的每个变量都写在 README 里，其他脚本在自己开头写明；README 和 `AGENTS.md` 给出的每个参数都存在 | `tests/test_docs.py` |
| 公开仓库：没有 token 或密钥、内部主机名、私有网段地址、真实的 home 或存储目录，也没有本机及同集群机器的名字 | `tests/test_hygiene.py` |
| 测试划分：每个 dry-run 场景和单元测试都属于基础测试或某个组，CI 里每个组都有一项检查 | `tests/test_groups.py` |

要让合并等测试通过：在仓库的 Settings → Branches 给 `main` 添加分支保护规则（或在 Settings → Rules 里建 ruleset），打开 *Require status checks to pass before merging*，加上 `tests-finish`；它在某个 pull request 上运行过之后才会出现在可选列表里。

## 目录结构

```
inferpilot/
├── inferpilot.js            # 编排：五种模式、门禁、预算、回滚、kernel 后端
├── runtime/                 # 在 Claude Code 之外执行 inferpilot.js 的 runner：引擎和命令行 backend
├── AGENTS.md                # Claude Code 之外的 agent（Cursor app 或 CLI）怎样启动和查看一次运行
├── roles/                   # intake, director, scout, dispatcher, analyst, architect, implementer,
│                            # test_engineer, verify_engineer, investigator, perf_engineer, issue_triager,
│                            # ci_guardian, kernel_liaison, reviewer, pr_engineer, curator
├── knowledge/
│   ├── dev_methodology.md   # 阶段、产物、门禁（契约）
│   ├── validation_doctrine.md, debugging_playbook.md, perf_evaluation.md, env_hygiene.md, pr_playbook.md
│   ├── acceptance.md        # 每类任务要验收什么、由谁确认
│   ├── guardrails.md        # 安全约束与社区行为准则
│   ├── reward_hacks.md      # 让数字变好却违背其本意的改动：怎么识别、用什么堵住
│   ├── dispatch.md, issue_triage.md, ci_triage.md, system_perf.md, composability.md, kernel_backends.md
│   ├── frameworks/sglang/feature_dev_map.md, platforms/rocm.md, known_issues.md
│   ├── frameworks/sglang/skills.md, platforms/rocm_skills.md   # sglang 自带 skill 的索引 + ROCm 说明
│   ├── kernels/aklr/skills.md, playbook.md   # AKLR 自带文档的索引；kernel 收益何时落到 AKLR
│   └── feature_skills/      # 按特性整理的已验证方案（首个是 hisparse-v2）
├── templates/               # task_definition, feature_spec, design, experiment_ledger, validation_report,
│                            # pr_body, issue_triage, ci_triage, perf_analysis, task_queue, daily_report,
│                            # weekly_release, acceptance
├── scripts/                 # 任务收集、审计、run_docker.sh、latest_image.py、run_lock.py、
│                            # restore_owner.sh、run_in_tmux.sh
├── inferpilot/bootstrap.py  # 第 3 步的配置脚本，在容器内运行；可以重复执行
├── setup.py, pyproject.toml # 打包；pip 构建 wheel 时 setup.py 会运行 bootstrap
├── tests/                   # run_all.sh、groups.json；inferpilot.js 的 dry run，runner 的自测，两个 backend 的对照，脚本、bootstrap 和文档的单元测试
├── .github/                 # CI（workflows/tests.yml 运行 tests/run_all.sh）和 pull request 模板
├── README.md, README.zh-CN.md
└── docs/design.md
```

## 与 kernel 工作流和 Hyperloom 的关系

- kernel 的速度是 kernel 工作流的事，无论选的是哪个后端：InferPilot 写出带单元测试的功能性 kernel，把算子规格连同作为判定标准的参考实现一起移交，再集成验证过的收益。
- 针对某个负载搜索 flag 和 backend 属于 Hyperloom 的工作；它发现的上游候选是 InferPilot 的输入，InferPilot 落地的特性又成为它的可选项。

## 来源

方法论、验证准则、已知问题登记表、模板和 HiSparse V2 特性 skill 来自 GEAK 的 `framework_workflow`（分支 `fangyuan/framework_agent_init`，commit `fc857c9`），当时它处在以知识库为主的阶段。InferPilot 在此基础上补上了当时计划中的编排、各个角色、设计中的 issue / CI / 性能 / scan 入口，以及任务收集脚本。它的运行不依赖 GEAK：由 Claude Code 的 Workflow 工具或 InferPilot 自带的 runner（`runtime/`）执行。
