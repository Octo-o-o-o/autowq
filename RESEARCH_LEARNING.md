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

macOS 与 Windows 托盘新增「研究进展」：读取账本展示分别生效的授权期限、最新冻结实验、已知/未知成本、Campaign 预约及不明 POST、14 个方向的缺口。UI 及正式提交设置不再单独触发研究模型基线漂移；旧停止实验不会因此复活。

尚需真实外部证据的部分：新闻时点/新鲜度方向、固定预测财期、期权聚合与缺失语义、VECTOR 事件单位，以及新市场日期的前瞻表现。采集元数据不等于完成真实模拟，离线测试不证明收益改善。用户侧仍需本人处理邀请、签约和实际现金/人工时间凭证；系统不能替代这些步骤。
