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

2026-09-26 修复：每个重点允许引用快照内全部有效轮次，不再受隐藏的20条引用上限影响。仍逐项验证轮次所属标签、拒绝重复/不存在的引用并绑定快照，后续独立复核要求不变；旧失败记录不改写，新快照由既有队列继续推进。

## Country 增量研究候选

该功能默认关闭，当前只提供首轮工程能力。`wq research-campaign template` 按当前已核验策略输出一个 **disabled** 合同，保留全部 14 项 blocked 机会；命令不修改策略、不创建模型或模拟任务。`wq research-campaign report` 从事件、任务、模拟和反馈记录生成诊断。把模板存入私有文件后，需逐项准备字段/语义证据，再经当前授权及验收流程接入策略的可选 `campaign` 字段。

合同包含稳定 `id`、递增 `version`、`valid_until`、`baseline`、`pilot_cap=56`、`total_cap=240` 和 14 项 `hypotheses`。`baseline` 绑定源码与除 campaign 外的完整策略，不能在旧版本下静默刷新。每项包含 `claim`、`falsifier`、`control`、`required_roles`、完整 `settings`、状态与原因，以及至多 4 的 `request_cap` 和 `max_cycles`。模型可见文本只能使用抽象角色，禁止平台字段 ID。

`ready` 需要：

- 每个角色的字段快照与 policy 的 region / universe / delay / instrumentType 匹配，类型和已登记摘要一致。
- `data_contract` 有 `measurement`、`availability`、`missing`、`source`，以及 `verified_at`、`valid_until`；`evidence` 是 policy `evidence_files` 中的 `{path, sha256}`。这些是操作者核验的真实语义证据，不可填写空泛内容来代替核验。
- 完整设置与当前执行策略相同。D0 / TOP1000 / TOPSP500 必须单独核验作用域；不能用现有只允许 decay / neutralization / truncation 的 `setting_variants` 偷换作用域。
- H-V2 当前明确不支持逐事件跨 VECTOR 加权，不能设为 ready，也不能以两个日均值的乘积记为完成。

同一活动的请求预约按稳定 `id` 跨版本和周累计，失败、排队和 UNKNOWN 都不退款；变体和符号翻转分别占一份。同一请求复用旧 task 时沿用原预约及状态，不增加独立样本。发送前核验已有预约，不重复扣账。先达到首轮上限或没有可执行机会就停止新增研究调用，仍允许原回执 GET 对账。未用满的 blocked 份额不转给其他方向。当前没有开启剩余 184 份预约的执行路径。

### 单字段 VECTOR 角色

`wq policy add-role` 新增可选 `--vector-contract <私有JSON>`。仅接受 `vec_avg(单字段)` 或 `vec_sum(单字段)`；表达式不能再包其他操作或附加运算。合同包含：

- `reducer`: `vec_avg` 或 `vec_sum`。
- `meaning`、`availability`、`missing`：来自真实文档核验的含义、可得时点和空值规则。
- `verified_at` 与 `valid_until`：时区明确，最长七天；字段和 operator 快照不早于本次核验起点。
- `operator_evidence`: `{path, sha256}`。文件使用本地归一化格式 `wq.operator-snapshot/v1`，包含官方来源 `https://api.worldquantbrain.com/operators`、`queried_at` 和完整相关 `operators` 记录；所选 reducer 的 `scope` 必须含 `REGULAR`。只读官方响应才可归一化，不能手工伪造算子可用性。

登记后角色 `output_type=MATRIX`，reducer 占用现有 AST 节点/深度额度。裸 VECTOR、嵌套 reducer 和跨 VECTOR 运算均拒绝。旧 MATRIX 角色不需迁移。

### 等待提交排序

配置 `brain_submission.standby_order` 为 `evidence` 后，自动候选先进入 waiting，再由原 release 流程统一入队。默认仍为 FIFO。排序只在全局 FIFO 的连续可比区段内进行：完整反馈、相同完整设置与 PnL 日期区间、同质量口径、同相关池成员和数据快照。质量维度为 IS fitness、test Sharpe、test fitness 和较低的本地已提交相关性；采用非支配层，不加权造一个总分。

证据 UNKNOWN 和跨域候选是分隔点。例如 A / UNKNOWN / B，即使 B 支配 A 也不跨过 UNKNOWN。相同非支配层保持首次入队顺序。不会重排 claimed / running，也不会降低远端检查、正式提交权限、滚动 24 小时上限或 UNKNOWN 处理要求。本地排序不是官方 Uniqueness，也不是独立样本外收益验证。
