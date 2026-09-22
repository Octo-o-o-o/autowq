# 全历史 Auto Research / All-cycle research review

Auto Research 为既有研究队列增加跨轮次复盘，不需要 Codex 保持在线。它不会训练模型权重；通过有证据的重点指导后续 Prompt，也不能保证 Alpha 质量提高。

## 中文

1. 汇总**全部已结束的自动研究轮次**，不再限于最近40轮。仍在运行的轮次在结束后计入。直接/人工模拟没有自动轮次编号，继续由 `autopilot feedback` 展示，不能冒充自动轮次覆盖。
2. 程序读取本地真实反馈，将其分类为重复机制、测量检查、收益效率、换手、时间分段、互补性、证据缺失。分类是诊断线索，不是因果裁决；审查拒绝不必然意味着测量错误。
3. 按当前 research 路由（本机为Grok）选择最多3项重点，每项必须引用确实含对应标签的历史轮次，并说明理由及反例。
4. 按 review 路由由不同渠道复核（本机为Devin），校验结果绑定同一份建议。拒绝、失败、缺证据均不生效。
5. 通过后，后续 research/review Prompt 注入固定指导语、证据轮次和快照hash。模型自由文本不会变成系统命令、参数、门槛或新权限。当前已经冻结的任务不回溯修改。

平台数值、字段名称、表达式、PnL序列、任意平台错误文本不外发；模型收到所有历史轮次的程序分类和汇总。这里的“全历史”指全轮次覆盖，不是把原始数据库全部发给模型。算法只允许固定的研究指导方向，不自动提出或执行任意流程改写。

```bash
./wq auto-research report                         # 本地全历史汇总，零模型调用
./wq auto-research run                            # 当前快照复盘入队
./wq auto-research enable --every-cycles 5 --min-hours 1
./wq auto-research disable                        # 停止自动创建复盘
./wq tasks                                       # 查看两步复盘任务
```

自动触发需同时满足：新增结束轮次达到阈值、距离上次复盘达到时间间隔、队列空闲、没有未结束轮次、有两个可用且预算允许的渠道。首次在已有轮数达到阈值时触发。复用现有runner/launchd，禁止同一快照重复派发或同时启动第二份复盘；无新数据不会按钟点空跑。

每次复盘通常2次模型调用，失败重试沿用既有路由上限，可能更多；使用原预算与授权期限，不增加29轮等累计研究上限。耗尽、过期、UNKNOWN、全局暂停依然有效。手动 `run` 只入队；若有在途任务，由原队列顺序执行。`disable` 停止未来自动创建，已入队复盘继续；设置 `history_research.use_priorities=false` 可停用已接受的指导语。

状态持久化在 `history_research` 表；只保存当前建议和快照，拒绝/失败历史保留。已有接受版本在新版本待审或失败时继续可见，`report` 显示覆盖数量，避免误以为复盘已经覆盖更新数据。

质量评估：比较使用指导前后新增轮次的重复/审查拒绝率、真实模拟通过比例、训练/测试分段表现与全部成本；必须标明样本数和自适应选择偏差。现在只验证了软件链路，不能声称复盘已经提高收益或提交成功率。质量门槛、平台限流、提交前验收以及原有有限组合实验约束均不变。

## English

`auto-research` reviews all closed automatic research cycles, with no 40-cycle limit. Active cycles enter after closure. Direct/manual simulations remain in `autopilot feedback` and are not counted as automatic cycles. This is evidence-informed prompting, not model training or a proven performance improvement.

The program categorizes local results; no raw platform metrics, expressions, field names, PnL or arbitrary platform messages enter the model packet. The research route proposes up to three fixed priorities with valid cycle citations. A different review provider must accept a hash-bound recommendation. Only program-owned guidance text and evidence IDs enter future research/review prompts; model prose never changes thresholds, code, budgets or permissions. Existing task snapshots are preserved.

Use `report` for a free local summary, `run` to enqueue a review, `enable --every-cycles 5 --min-hours 1` for event-and-time cadence, and `disable` to stop future automatic reviews. Automatic reviews require enough new closed cycles, minimum elapsed time, an idle/reconciled queue, no active cycle and available budgeted providers. Existing runner scheduling performs the work; no extra Codex automation is installed. The same snapshot is not rerun. Usually two model calls are required, plus bounded retries if needed. Existing budgets, authorization deadlines, pause controls and research-cycle limits still apply.

Disabling scheduling does not cancel already queued work or erase accepted guidance. Set `history_research.use_priorities=false` to stop using accepted guidance. Reports expose snapshot coverage and latest status. A previous accepted version remains active while a new one is pending/rejected. Track subsequent rejection, valid simulation and temporal-performance rates with sample sizes and full cost; adaptive improvements are not untouched out-of-sample proof.
