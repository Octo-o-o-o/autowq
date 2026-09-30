# 持续研究：运行与观察

本功能把本地历史变成下一轮可检验的研究建议。自动学习指改进检索、计划选择和有界实验，不是自动修改程序、放宽平台门槛或保证收益。所有原有研究审查、渠道隔离、授权有效期、预算、UNKNOWN 对账和提交门禁继续生效。

## 自动运行的闭环

1. 每轮结束和反馈收集后，追加 Trial、实际父子编辑和 OutcomeVersion。技术失败、未知、未模拟、质量失败分别记账。导入历史只从导入时刻可得，不伪造过去。
2. 在相同字段绑定及模拟设置下，检索结构相近的实验、实际编辑和反例。传给模型的仅为抽象 AST、分类结果和有界建议，不含私有字段、表达式、原始 PnL 或凭据。
3. 学习臂的一次研究调用可提出 1–3 个计划，只选择一个进入原有审查。程序提供少量合法单点编辑和测量模板，不派发笛卡尔网格。覆盖选择是基线，规则有支持/反例时可在试验中调整排序；每四轮保留纯覆盖探索位。
4. 规则只能来自真实编辑前后的可比结果。至少三个机制族才产生排序建议；同父窗口变体不会增加独立证据。规则有 30 天有效期，没有新证据不续期，不输出虚假的正确率。
5. runner 在空闲轮次边界、每六小时至多维护一次。同步历史，检查漂移、评价实验、冻结候选和池权重；启用刷新时每天最多读取配置数量的 Alpha（0–5，建议 2），沿原队列 GET，不增加模拟 POST。
6. 可选自动对照每七天至多新建一次，每次轮数和每臂请求数预先封顶。所有上限均叠加于原预算，不能提高它。显式停止或冻结基线漂移后不自动重启该实验。
7. 成本完整、每臂至少 20 个机制族、完成预登记分配、描述性区间支持改进，并且本实验学习候选的冻结池新增区间也有正贡献时，才允许启用试验过的规则排序。反向证据或依据不足回到基线/影子状态。这里的统计仍非随机因果证明，也不是美元回报证明。

`baseline`：保持基线；`shadow`：积累和比较；`active`：在已验证范围试用学习排序。实验中的 `learning` 臂可以试用规则，不等于已成为全局默认。

## 启用配置

在现有 `config.json` 中添加，保留原有授权和预算。默认不启用后台维护或网络刷新。

```json
{
  "research_learning": {
    "enabled": true,
    "maintenance_enabled": true,
    "structural_diversity": true,
    "refresh_enabled": true,
    "refresh_per_day": 2,
    "auto_experiments": true,
    "experiment_cycles": 80,
    "requests_per_arm": 40
  }
}
```

结构多样性仍归入旧 family 预算桶，每个旧 family 最多两种有序拓扑；窗口、排名和符号变体不会获得新机制资格。没有结构证据的外部已知 family 仍拒绝。

停止新学习试验：关闭 `auto_experiments`；停止某个已有实验用下方 `stop`。关闭 `enabled` 停用新提示与排序；关闭 `maintenance_enabled` 停用维护。现有在途平台请求仍对账，历史不删除。

## 日常关注

```sh
wq research-learning report
wq research-learning report --experiment EXPERIMENT_ID
wq research-learning evaluate --experiment EXPERIMENT_ID
wq research-learning measurement-policy
```

优先看：

- `coverage`：实际任务/轮次是否完整纳入；非法旧 AST 单列，不重写为合法。
- `usable_per_100_requests`：每百次请求的非重复可用族，分母包括失败尝试；同时看质量、技术失败、UNKNOWN。
- `model_cost`：已知美元、缺费用调用数、token。未知不是零费用；个人订阅现金和分摊成本仍需真实费用凭证。
- `comparison` / `learning_state`：当前试验、尚缺什么证据、是否回退。每臂 20 族只是最低条件，不是充分样本保证。
- `forward_retention`：新增数据后的全历史质量保持；`pool_contribution` 才是冻结后独立区间、固定历史风险标定下的本地 PnL 比较。两者均不是官方 VF、SELF/PPA/PROD 或现金收益。
- `cash`：既有 payment/expense 账本，按币种/单位分开；应收不当到账，无记录不代表历史总收入为零。

每次维护写入现有运行目录的 `research-learning-status.json`。没有新增市场日期就会保持等待；BRAIN 若只提供固定历史区间，单纯反复刷新无法完成前瞻验收。

建议每天看阻塞/成本/覆盖，每周看同预算有效产出和规则反例，每个成熟账期核实际净现金。不要用某一条最高 Sharpe 判断系统进步。

## 复盘与有界实验

```sh
wq research-learning sync
wq research-learning replay --at 2026-09-30T00:00:00Z
wq research-learning freeze --experiment learning-01 --max-cycles 80 --max-requests-per-arm 40
wq research-learning stop --experiment learning-01 --reason "Stop this comparison and retain all evidence"
wq research-learning refresh --alpha-id EXISTING_ALPHA_ID
wq research-learning freeze-pool --pool retained-01 --trials task:EXISTING_TASK_ID
wq research-learning freeze-contribution --pool contribution-01 --trials task:REFERENCE_TASK --candidate-trial task:CANDIDATE_TASK
wq research-learning effort --entry-id unique-entry --cycle 12 --seconds 180 --reason "Actual manual inspection"
```

`freeze` 固定源文件、策略、模型/配置和预算指纹。交替分配在新轮次调用之前完成；旧结果不得追认进实验。可以用 `--research-provider NAME` 单独评估当前已授权研究路由中的模型；同一次实验不要同时比较路由与其他改进再归因给单个因素。

`freeze-contribution` 在共同历史区间拟合倒波动权重，并分别固定基准池与加入候选后的历史单位风险尺度。新增区间必须晚于冻结日和历史截止日，日期区间严格对齐，不将跨日缺口充作一天；文件内容改变会拒算。差值给出五区间循环块 bootstrap 描述性区间，不能消除选择偏差或制度变化。

旧反馈只有最新文件时，过去时间点不可恢复。供应商版本未知就保持未知。`replay` 不逆向补费用或当时未取得的数据。

## 测量和门禁研究

`measurement-policy` 读取匹配当前 region/universe/delay 的本地字段目录，报告输入覆盖、交集下界及未知更新频率；字段边际覆盖不等于表达式覆盖。`catalog.search_scoped` 返回范围、快照 hash、是否完整、截断情况和限定数量的结果。

需要研究缺失值时，可用有合法来源的本地点时面板：

```sh
wq research-learning measurement-audit --input panel.json
wq research-learning gate-audit --input proposed-segment-rules.json
```

面板严格含 `scope`（region/universe/delay）、`source` 和 `rows`。每行含有时区的 `date`、`available_at`、`asset`、`left`、`right`，缺失用 null。程序检查 available_at 不晚于决策时间，报告交集/并集覆盖与实际变化频率；不会把“缺失视为中性”假设宣称为成立，也不擅自开放 NaN 填充。

门禁影子输入是现有 `segment_rules` 格式。结果只展示历史反事实，不修改平台/相关性/提交门槛。DSR/PBO 缺少全策略矩阵、频率或独立试验信息时报告 unavailable，不以一个尝试数制造统计保证。

## 研究建议与交付对应

| 采纳内容 | 实现入口 | 生效所需证据 |
|---|---|---|
| Trial/Edit/版本/时间/反例 | research_learning | 本地历史；不恢复已覆盖旧文件 |
| 精准检索、多计划、编辑模板、规则比较 | research_strategy / autopilot | 现有审查及同预算实验 |
| 类型/量纲/中性化解释 | research_dsl | 已声明元数据；未知保持未知 |
| 字段范围、覆盖、NaN 与更新频率审计 | catalog / research_measurement | 匹配范围快照或点时面板 |
| 真实翻转与参数敏感性 | 原预登记变体 + sensitivity | 每个变体自身平台结果 |
| 相关版本与冻结池增量 | feedback / research_metrics | 完整且匹配的新增 PnL 区间 |
| 家族误拒/规则误拒检查 | family_decision / gate-audit | 有序结构与影子反事实；不越过 required gate |
| 成本、人时、现金与模型对照 | model_cost / economics / effort / freeze | 实际调用费用/凭证/人时；不归因老池收入 |
| 自动维护、实验启停与回退 | research_maintenance / runner | 有效授权、原预算、空闲边界 |
| 阶段目标 | maintenance stage advice | 当前账户阶段；超过七天未核验则标未知，不自动扩预算 |

MCTS、BO、复杂 bandit、crossover、整体搬入第三方 miner、未经核验的统计硬门槛，以及 Numerai/质押仍按研究裁决后置或不采用。自我修改代码、推广万能规则、自动增加预算不属于本功能。

## Deployment and rollback

Run the full offline suite and build a clean wheel. Back up the live SQLite database with SQLite backup, config and runtime before deployment; wait for the runner lock and preserve pending remote requests. Install the exact wheel used in verification, compare all package files, run local sync/report, and check the real scheduler. Source tests do not prove market gains. Keep old runtime and database snapshots; disabling learning never deletes historical observations.


## 增量研究与桌面进展（0.2.18）

本次重新核对了本会话三个研究主题：Gold 后持续研究、生态借鉴和 Country 增量方向。上表覆盖已采纳的工程建议；15 条候选规则继续以限定语义、反例及证据资格实现，不把社区阈值升格为硬门禁。MCTS、BO、复杂 bandit、整体 miner 和 Numerai 仍为明确后置项。

新增 `research_campaign` 保留 14 个方向：新闻 N1–N3、分析师 R1–R3、期权 O1–O3、向量 V1–V2、D0、TOP1000 与 TOPSP500。模板默认关闭并逐项列出缺失证据；没有真实语义、独立 scope 和原审查，不自动启动。H-V2 的逐事件加权仍不支持。首阶段最多 56 次预约，跨周总额 240 不代表已授权扩展，第 57 次预约仍被拒绝。

`wq brain operator-evidence` 保存官方当前算子及 REGULAR scope；`field-evidence` 支持 `--region/--universe/--delay`，加 `--no-policy-update` 可仅保存核验材料。VECTOR 只开放有当前字段与算子合同的单叶 vec_avg/vec_sum，原复杂度和 review 仍生效。

`brain_submission.standby_order=evidence` 可在完整且同条件的连续候选间排序；未知项隔断排序区间，已入队任务保持原状，默认仍为 FIFO。它不估计官方 Uniqueness。

macOS 与 Windows 托盘新增「研究进展」；macOS 另有可刷新、选择文字的独立窗口，再次打开 App 可显示：读取账本展示分别生效的授权期限、最新冻结实验、已知/未知成本、Campaign 预约及不明 POST、14 个方向的缺口。UI 及正式提交设置不再单独触发研究模型基线漂移；旧停止实验不会因此复活。

尚需真实外部证据的部分：新闻时点/新鲜度方向、固定预测财期、期权聚合与缺失语义、VECTOR 事件单位，以及新市场日期的前瞻表现。采集元数据不等于完成真实模拟，离线测试不证明收益改善。用户侧仍需本人处理邀请、签约和实际现金/人工时间凭证；系统不能替代这些步骤。

## Adaptive Campaign V2（源码候选，尚未发布）

V1 的阶段标签仍按原合同解释，不冒充严格配对。V2 将数据资格、执行状态和方向评估分开记录；模板保留全部 14 个方向，默认关闭。当前生产反馈采集**尚无经过核验的 capital、units、cost basis、timezone 与 data revision 映射**，因此严格收益配对在研究模型调用和模拟预约前返回 `OBSERVATION_CAPABILITY_UNSUPPORTED`。手填 `synthetic=false` 或 `measurement_conventions` 不能解锁。资料合同与状态机已实现，不代表真实方向可执行或金融效果成立。

```sh
wq research-campaign template-v2   # 只输出草稿，不启用
wq research-campaign report       # 14 项资料缺口、执行、配对和评估
wq research-campaign combinations # 本地私有父对资格及全部阻断原因，不创建计划
wq research-campaign replay --pair-id ID --observation-hash HASH
```

数据合同将原始材料与语义断言分开：原始 bytes hash、来源/产品版本、精确引文和定位、核验主体/方法/有效期，以及 account、REGULAR、region/universe/delay、bindings 和干预指纹均须对应。完整字段目录或 MATRIX 类型不证明历史可得时点、聚合窗口和缺失语义。机器核完整性与绑定；owner 引证声明不等于独立证明历史数据未回填。H-V2 逐事件加权仍为 unsupported。

每个 recipe 必须明确被测量的差异、合同规定的同一有效样本、允许变化、零值/缺失/预热与前后算子语义。剥离乘法只作为注册的权重/强度测量，不能称为删除事件；`vec_avg/vec_sum` 的差异包含事件数量缩放，不能自动称为 novelty 隔离。仅改一个 AST 节点、相同 PnL 日期或事后取交集均不够。VECTOR 原字段/算子七天以内的核验证据仍单独生效。

首组由一个真实研究作者提出处理候选，程序按注册干预构造控制；两臂分别走原 review，双臂通过才整组预约。第二组仅执行首 POST 前冻结的共同转换或另一个 scope，继承真实作者任务，不新增 generation；两臂 review、原渠道不同/显式 solo、每 cycle 一次 fallback 和复杂度门禁保留。同父测量不能变成多个独立机制支持。

`EvaluationSpec` 固定 `decision_unit`、`reuse_policy`、日期网格、预测方向和同单位阈值。全期与三个固定分段均使用 signed mean daily return，至少 180 个区间、每段至少 60 个区间；累计 PnL 必须保留窗口开始前一点，不能补零或事后删日期。全期和至少两段 `> floor+tolerance` 才继续；全期及三段均 `<= floor-tolerance` 才表示本次操作性预测未满足条件。其余保持 inconclusive，均不是统计或未来收益证明。

D0/U 使用四个不同 execution 的整体 `both_scopes` 函数：两 scope 均支持才支持；四臂完整可计量后任一 scope 为 falsified，整体才为 falsified；其余为 inconclusive。缺任一回执或计量字段不能做整体动作。第一 scope 的效果不会取消已登记的第二 scope，技术失败或已知无法计量则不补第五次。`new_executions_only` 拒绝把去重旧任务当新试验；显式 `descriptive_reuse` 允许描述性历史复用，但不增加盲测/独立样本身份。

观察由 task → brain_run/原始 Alpha → simulation → OutcomeVersion → observation → 冻结 request 联结。实际读入的 PnL bytes、Alpha bytes 和必要身份/report 一并保存在内容寻址快照；回放只读快照，源文件被覆盖或删除也不回退到新文件。内容 hash、Outcome revision 和供应商 data revision 分开。新观察/评估只追加；首次完整动作已停止或已派发时，后续修订不能复活动作。

预算由原 task owner 计一次，整组去重、任务、request refs 与预算写入在同一 SQLite 写事务内；失败整组回滚。跨 V1/V2 稳定方向累计最多 4 次预约，pilot 最多 56，240 不提供额外授权。确认未发出的 `not_sent`、失败、UNKNOWN 和 queued 都保留预约。POST 顺序为完整 preflight → 持久化意图并 commit → 同一数据库写锁内重读停止/迁移和门禁 → 最后有效期检查 → 一次 transport；确定未进入 transport 才可标 not_sent，崩溃不明一律不重发。

```sh
wq research-campaign migrate --reason '具体的已有版本迁移依据'
wq research-campaign stop --reason '具体的停止原因'
```

迁移只登记新版本与实际剩余额度，要求无 claimed/running/UNKNOWN；旧 queued owner、累计预约和显式停止不改。迁移不是续期或恢复授权。改源/策略后仍需按原部署程序在空闲边界处理冻结 experiment 基线。

普通研究与 Campaign 混合调度；只有方向自身缺证据时可回到原授权下的普通研究，暂停、UNKNOWN 和共享资源门禁仍阻断。全局资源账记录全部真实消耗，ordinary 效果比较的分子/分母仅使用同一 ordinary 集合，Campaign 单列；多次引用不重复计费。Campaign 不自动提交，不自动晋升 ordinary 规则，也不自动扩大 pilot。

所有离线 transport/model fixture、纯统计函数测试、打包一致性和桌面展示检查均属于工程证据；真实金融效果须另由完整合同及真实平台观测证明。

## 持续研究框架（FR-01–FR-08，0.2.19）

目标是让未来新增的资料、回执和研究问题能够进入原队列，形成可追溯的下一步。框架可在数据不足时持续维护缺口；不要求 14 个方向现在全部可运行，也不把工程闭环当收益改善。

启用 `research_framework.enabled=true` 后，原 `run-once` 在空闲边界选择重评、补证、学习维护或新研究。未显式配置时跟随 `research_learning.maintenance_enabled`。继续使用 `tasks`、`research_events`、原运行锁和原模型/API授权，没有第二套队列。默认每天最多 4 个补证任务，可配置 `research_framework.evidence_tasks_per_day` 为 0–10；每个来源版本最多 1–5 次尝试，读取失败按 1/2/4/8 小时退避。资料未变不创建同类重复任务；普通探索最多连续让位两次，持续可选的其他类型最多让位四次后获得优先机会（探索保留机会仍先满足）。这不是调高模拟或模型预算。

```sh
wq research-framework report  # 只读：缺口、来源状态、选择、知识、资源和议题
wq research-framework tick    # 显式执行一次选择，可能创建原账本任务；不是只读命令
```

桌面「研究进展」增加「持续研究框架」卡片，显示启用状态、当前等待原因、缺口数量、最近选择、前三项待办及触发条件；全部事项由 CLI 报告提供。卡片不负责写入授权。

### 补证来源与数据合同

每个 gap ID 绑定 campaign、hypothesis、step、predicate、scope 和 binding hash。`report.gaps` 给出当前 `gap_id`、`query`、`fields`、`state`、`reason`、`next_trigger`，并在实际执行后附任务、收据、尝试次数与退避时间。仅原合同检查成功才标 `validated`。`collected` 的下一步仍是语义合同核验，不能派模拟；`owner_required`、`unsupported`、`expired`、`rejected`、`exhausted` 均保留明确原因和恢复入口。

来源注册是显式 owner CLI 操作；模型只能提案，不可注册路径或网络端点。以下 JSON 中的 ID、hash、日期和出处必须换成真实输入，不能照抄占位值：

```json
{
  "gap_id": "REPORT_GAP_ID", "revision": 1,
  "adapter": "local_material_v1",
  "source": {"path": "/absolute/provider-document.md", "sha256": "SHA256_OF_BYTES", "source_ref": "PROVIDER_DOCUMENT_REFERENCE"},
  "approved_by": "OWNER", "valid_until": "OWNER_APPROVED_ISO_DEADLINE", "max_attempts": 3
}
```

```sh
wq research-framework source --file /absolute/source-registration.json
```

另有两个内建只读 adapter：`brain_field_metadata_v1` 的 source 必须是 `{"field_id":"EXACT_GAP_FIELD","query":{...}}`，query 与 report 中该 gap 完全相同；`brain_operators_v1` 的 source 为 `{}`，只读固定官方 endpoint。两者要求当前 BRAIN API 授权且遵守共享 cooldown；不会发模拟 POST。远程材料也只证明收集到了资料，不能自行证明 point-in-time 语义。新来源必须递增 revision；改文件会重新唤醒其依赖，但 hash 不符仍拒绝，不自动信任新内容。已验证断言过期或材料失效会重新等待。

### 可注册的观测采集器

生产默认仍未注册可信映射，严格配对继续阻断。现已提供内建 `brain_cumulative_pnl_v1`，不加载模型生成代码或任意插件。注册位置是策略中对应 `execution_profiles.PROFILE.observation_collectors`：

```json
{"provider_daily_v1":{"path":"/absolute/collector-contract.json","sha256":"SHA256_OF_CONTRACT_BYTES"}}
```

对应 step 的 `evaluation.collector_id` 引用该 ID。collector 文件结构：

```json
{
  "schema":"wq.observation-contract/v1", "id":"provider_daily_v1", "adapter":"brain_cumulative_pnl_v1",
  "scope":{"region":"USA","universe":"TOP3000","delay":1},
  "verification_method":"owner_attestation", "verified_by":"OWNER",
  "verified_at":"ACTUAL_ISO_TIME", "valid_until":"OWNER_APPROVED_ISO_DEADLINE",
  "materials":[{"id":"provider","path":"/absolute/provider.md","sha256":"SHA256_OF_BYTES",
    "source":"PROVIDER_REFERENCE","source_type":"provider_documentation","product_version":"ACTUAL_VERSION"}],
  "mappings":{
    "frequency":{"source":"contract","value":"daily","evidence_refs":[{"material_id":"provider","quote":"EXACT_SUPPORTING_QUOTE"}]},
    "capital_basis":{"source":"alpha","path":["settings","bookSize"],"evidence_refs":[{"material_id":"provider","quote":"EXACT_SUPPORTING_QUOTE"}]},
    "value_unit":{"source":"contract","value":"USD","evidence_refs":[{"material_id":"provider","quote":"EXACT_SUPPORTING_QUOTE"}]},
    "cost_basis":{"source":"contract","value":"gross","evidence_refs":[{"material_id":"provider","quote":"EXACT_SUPPORTING_QUOTE"}]},
    "timezone":{"source":"contract","value":"UTC","evidence_refs":[{"material_id":"provider","quote":"EXACT_SUPPORTING_QUOTE"}]},
    "data_revision":{"source":"pnl","path":["revision"],"evidence_refs":[{"material_id":"provider","quote":"EXACT_SUPPORTING_QUOTE"}]}
  }
}
```

这是格式示例，不是对 BRAIN 当前响应字段/单位的事实断言。注册人须按供应商材料确定每个路径、常量及其含义；若实际回执没有供应商版本字段，不能把本地 hash 或常量代替 `data_revision`。`capital_basis` 必须读得有限正数，其他约定为非空字符串；data revision 必须来自 Alpha/PnL 回执。支持材料类型为 provider/platform documentation 或 licensed panel contract。每个 mapping 都要求可在对应保留材料中查到的引文。通过合同资格仍须通过实际请求身份、日期网格和配对计量检查。

collector JSON、材料与实际读入的原始回执一并留存为内容寻址快照。只读 replay 使用原快照，不依赖现场文件继续存在。未来补证或新回执触发新的 evaluation，保留旧评价及已决定动作。

### 晚到重评与研究知识

每次空闲 tick 轮转检查最多 16 个已关闭 pair，按观测输入 hash 建 `research_reassess` 任务。一次输入只完成一次重评，数据库重启不改变去重身份；没有新增模型调用或模拟 POST。已停止 campaign 仍可做描述性重评，但不能创建后续动作；原 pair 一旦已有最终动作，后来的修订不复活它。

配对结论进入单独 `research_knowledge`，保留支持、反例、不确定、技术失败及等待另一 scope；下一研究包仅检索相同 scope、binding 且未过期的分类事实，优先保留反例。一个父来源仍只算一个来源，unknown 不扣机制质量分，也不变成普通单节点编辑规则。它是历史预测的证据，不是未来收益承诺。

扩量 proposal 可登记 `accepted`、`rejected` 或 `deferred`。接受只记录规划结论，必须提供零新增请求的授权引用；执行扩量仍需另外的有界授权与原门禁。

```json
{"pair_id":"PAIR_ID","status":"accepted","reason":"OWNER_CONCRETE_PLANNING_REASON","owner":"OWNER",
 "authorization":{"reference":"OWNER_DECISION_REFERENCE","additional_requests":0}}
```

```sh
wq research-framework resolve-proposal --file /absolute/proposal-decision.json
```

### 新议题与注册

现有 14 方向是初始实例，不再限制议题总数；单实例仍采用 `paired_intervention_v1`、最多两组配对与四次请求。研究模型的既有输出可以附最多两项 `research_issues`，不为此另开模型调用。CLI 也可录入同结构提案：

```json
{"id":"H-X15","revision":1,"mechanism":"A concrete abstract mechanism predicts an effect.",
 "measurement":"A concrete registered historical measurement.","falsifier":"A concrete condition under which the prediction fails.",
 "profile":"base","required_assertions":["historical_availability","missing_semantics","measurement_isolation","platform_mapping","measurement_specific"],
 "template":"paired_intervention_v1"}
```

`required_assertions` 必须包含实际代码 `research_contracts.COMMON` 列表，并增加该测量需要的谓词；CLI 会拒绝不完整契约。profile 必须已存在，文字只用抽象角色，不携带私有字段。`propose` 返回 issue hash，尚不创建执行任务。

```sh
wq research-framework propose --file /absolute/issue.json
wq research-framework register --file /absolute/issue-approval.json
```

注册文件精确包含 `issue_id`、`issue_revision`、`issue_hash`、`approved_by`、`reason`、`source_policy_hash`、`valid_until`、`hypothesis`。hypothesis 是完整 V2 实例；其 id、claim、falsifier、required_assertions 必须与提案一致，settings/steps 只用提案中已授权的同一个 profile，`request_cap=4`、`max_cycles=4`。注册只在无活动周期和 claimed/running/UNKNOWN 时进行；保留原 14 方向、旧预约 owner、累计 56/240 边界与停止记录。

注册先持久化 intent，再原子写策略并提交数据库；若两者间中断，重新执行完全相同的 approval 可恢复。期间发现不相干的策略改动则拒绝覆盖，由 owner 合并后提供新的明确输入。不能用注册新议题重置旧预算。

### 普通学习、前向贡献与 epoch

ordinary 效果分组与全局资源归属分开：campaign 只记 `learning_resource_cycles`，ordinary 在 settings/bindings stratum 内交替平衡，只有 ordinary 消耗效果实验的 cycle cap。全局资源费用仍包含 campaign。既有混合分配不回填、不重组、不自动得到可归因结论；新版实验使用 `ordinary_only_v2`。跨机制族、共享上下文等现有不可比性原因继续保留。

前向贡献准备不再被 quality retention pool 的“已冻结”状态永久排除。缺参考为 `waiting_reference`，区间不足为 `waiting_calibration`；只有输入版本改变才重试，每次维护最多准备一个改变的候选。成功冻结后权重与校准日期永久不重拟合，后续区间仅用于前瞻观察。

停止类型分 `owner_stop`、`authority_expired`、`budget_exhausted`、`baseline_superseded`、`technical_blocked`；旧未分类停止按 owner_stop 对待。基线更新仅形成新 epoch 提案，默认 `owner_required`；仅明确批准精确目标基线后才可在原有效权限与剩余额度内创建 successor。审批文件：

```json
{"transition_id":"STABLE_OWNER_TRANSITION_ID","from_experiment":"EXPERIMENT_ID","from_baseline_hash":"EXACT_OLD_BASELINE_HASH",
 "to_baseline":{"source":"CURRENT_SOURCE_HASH","policy":"CURRENT_POLICY_HASH","models":"CURRENT_MODELS_HASH","budget":"CURRENT_BUDGET_HASH","usable_definition":"CURRENT_DEFINITION"},
 "valid_until":"OWNER_APPROVED_ISO_DEADLINE","reason":"OWNER_REVIEWED_EXACT_TRANSITION_REASON","approved_by":"OWNER"}
```

```sh
wq research-framework approve-epoch --file /absolute/epoch-approval.json
```

实际 `to_baseline` 必须逐字段等于当前 `research_learning.current_baseline(cfg)`，不能套用示例。审批不加 cycles/requests，不恢复旧实验；任一 lineage 的 owner_stop 会阻止后代分配和 dispatch。剩余请求计入旧队列中失败、UNKNOWN、queued 等已有预约，不因 epoch 更名归零。

### 资源与证据边界

`research_work_selected` 保存可选项、选择原因、策略 hash 和确定性选择版本；`research_work_resources` 保存每次补证/重评的模型调用数、逻辑 API 读取尝试数和单调时钟运行秒数。底层 transport 的重试仍以原 API 日志为准。无辅助模型调用的费用为 0；发生 API 读取但没有价格凭据时 API 费用为 null；运行时货币成本始终未估算为 null。原模型和模拟费用继续由原账本统计。

本候选的验收证据是离线状态转换、真实本地 runner/SQLite 恢复、源码与安装包一致性及隔离桌面检查。模拟 transport 不是 BRAIN 真服务验证；当前未配置的真实资料与采集约定仍保持阻断。没有自动增加额度、提交 Alpha、部署运行时或自改源码。
