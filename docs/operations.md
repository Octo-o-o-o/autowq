# 运行与恢复

## 已有部署

在项目根运行 `./wq autopilot status` 和 `./wq tasks`。安装调度后，程序由 launchd 每60秒唤起，空闲时只检查本地状态，不需要 Codex、聊天窗口或模型充当调度器。Mac 必须保持登录、联网并处于唤醒状态；注销/休眠期间不运行，重新登录后调度恢复。模型登录、BRAIN 人机验证以及授权续期不能保证无人介入。

`./wq autopilot stop` 停止补充研究轮次；已排队任务可以完成。`./wq pause --reason "暂停"` 阻止队列派发并终止本地在途模型进程。`./wq resume` 恢复队列，UNKNOWN 尚未对账时默认拒绝恢复。

本地状态文件：`var/run/autopilot-status.json`、`var/run/progress.json`。日志：`var/run/launchd.out.log` 和 `launchd.err.log`。没有配置邮件或即时消息推送。长期运行应定期检查磁盘和归档日志，不要删除在途调用证据。

## 新机器初始化

建议先按 [首次使用指南](onboarding.md) 执行 `./wq onboard`，选择本机渠道和模型。低层生成器 `python3 scripts/setup_local.py` 仍可生成全部关闭的通用模板：

- `config/config.json`：模型、预算、BRAIN API、autopilot 全部关闭。
- `config/profiles.json`：Grok、Devin、Cursor、ZCode 命令入口与四套预设。
- `config/autopilot-policy.json`：缺真实证据的占位策略，不能开启研究。
- `~/.local/share/autowq-runtime/`：沙箱、launchers、jobs 和 launchd plist。

可用 `--runtime /绝对路径` 选择项目之外的新目录；不能覆盖已有配置。macOS 启动器依赖 `/usr/bin/sandbox-exec`，Linux/Windows WSL2 使用单独的 Docker Provider 与 systemd 路径，见 [跨平台部署](linux-windows.md)，不复用 macOS 启动器。

分别通过供应商自己的 CLI 完成安装和登录，再核对 `config/profiles.json` 中 argv、模型名称与 timeout。Grok 默认 `~/.grok/bin/grok`，Devin 默认 `~/.local/bin/devin`，Cursor 默认 `~/.local/bin/cursor-agent`，ZCode 默认 `/Applications/ZCode.app`。Cursor 可通过 `WQ_CURSOR_BIN` 指定；ZCode Node 可通过 `WQ_NODE_BIN` 指定。定时器不继承交互 shell 的环境，必要时在自己的 launchd 环境中配置。

不应将订阅网页账号视为通用 API 额度。各 CLI 和套餐的可用权限须在自己的账号核实。自定义渠道应实现退出码、JSON 产物及终态校验；只有端口/进程存在不算调用成功。

## 开启真实研究前的配置

1. `models.<provider>.enabled=true`，为至少两个渠道配置预算。可以用 `./wq budget PROVIDER --enable --remaining N --unit calls` 登记本地调用预算。失败且实际启动的调用也计数。未知余额不会被当成零成本或无限额度。
2. 设置带时区的 `routing.authorized_until`。首周不限额模式必须显式配置 `debug_authorization`：enabled、unlimited、agents、starts_at、expires_at、evidence；窗口不得超过七天。过期不自动延长。
3. 本人在终端执行 `./wq brain login`，再 `./wq brain check`。密码不保存，cookie 仅保存在私有目录。设置 `brain_api.enabled=true` 与带时区的 `brain_api.authorized_until`；旧 `adapters.brain.mode` 不控制新 BRAIN 队列。
   macOS 长期调度可执行一次 `./wq brain keychain-save`，把密码交给 macOS Keychain；然后设置 `brain_api.auto_login=true`、`brain_api.auto_login_email`，或为 LaunchAgent 设置 `WQ_BRAIN_AUTO_LOGIN=1`、`WQ_BRAIN_EMAIL`（自定义条目可用 `WQ_BRAIN_KEYCHAIN_SERVICE`）。不把密码放进 env、plist、config、argv 或日志。认证失败时只自动重做登录与只读 OPTIONS 预检，不自动重发不确定的 POST。
4. 对自己的平台字段和运算符核验后，填 `autopilot-policy.json` 的抽象角色绑定、完整 settings、来源、verified_at、valid_until、证据文件绝对路径与 canonical JSON SHA256。真实证据在私有目录，不能交给研究模型。canonical hash 使用 `wq.util.sha256_json`，不是原文件字节哈希。占位模板故意不能通过。
5. `./wq autopilot start`。CLI 检查策略文件，runner 在每次新动作前检查预算、期限、证据和任务状态。start 成功仅表示启用开关，实际是否开跑以 status/tasks 为准。

安装生成的 launchd 文件前先确认本机没有同名运行实例，避免替换已有部署：

```sh
mkdir -p ~/Library/LaunchAgents
cp ~/.local/share/autowq-runtime/com.worldquant.wq-runner.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.worldquant.wq-runner.plist
launchctl print gui/$(id -u)/com.worldquant.wq-runner
```

退出已有调度器用 `launchctl bootout gui/$(id -u)/com.worldquant.wq-runner`；执行前先 `wq pause`，不要中断正在派发的远端请求。生成器本身不安装或启动服务。

## 预设与速度

首次使用 `./wq onboard` 自选渠道、模型与角色。模型ID与思考强度使用本人CLI实际支持的值，配置生成不证明订阅权限。既有任务保留冻结快照，配置修改仅影响新任务。

`steady`：Grok 研究、Devin 工程/审查优先；`core-only`：只用 Grok/Devin；`cursor-rich` 和 `zcode-rich`：优先使用对应富余额渠道。`wq provider enable` 只取消停用标记，不会绕过模型开关、授权或预算。

`config/config.json` 的 `autopilot.interval_s`、`max_cycles_per_day`、`max_simulations_per_week` 与 `limits.sims_per_week` 控制速度。修改间隔适用于随后结束的轮次，不重写已保存的 next_cycle_at。每日按 UTC、每周按 ISO 周计数；达到上限后自动等待新周期，授权未过期才继续。每个新轮次最多一个候选，不做参数网格救活失败策略。

## 故障处理

| 现象 | 行为 / 恢复 |
|---|---|
| Provider 临时失败 | 首次 + 最多三次重试，每次保留独立副本；Retry-After 不缩短 |
| 明确额度/容量耗尽 | 重试耗尽后冷却该渠道并切备用；普通配置/产物错误不触发回落 |
| BRAIN 401/403 或本地会话过期 | 若已配置 macOS Keychain，先自动读取凭据并重做登录/只读预检；成功会自动解除明确标记为认证故障的暂停，并仅恢复已有 GET/尚未发送 POST 的模拟；失败后自动退避5分钟，或需要人机验证才暂停。本人 `wq brain login` 后仍可恢复认证失败的已有 GET 查询；人工暂停始终保留 |
| POST 超时/回执丢失 | UNKNOWN，停止重复派发；人工对账 |
| 已保存 Location 后崩溃 | 租约过期后恢复 GET，不重发 POST；最多360次队列尝试后要求检查 |
| 模拟长时间没有结果 | 显示平台进度及等待分钟数；1小时后至少每5分钟GET，24小时仍未完成则阻断并保留回执待核对，不重新POST |
| 授权到期 | 不开新模型调用或模拟；已有回执允许继续查询 |
| 模型拒绝/重复机制/回测失败 | 归档并按间隔开下一轮；不把拒绝改成通过 |

UNKNOWN 对账先查官方历史，并保留核实依据。以下命令中的 ID 必须替换为真实核验值：

```sh
./wq reconcile
./wq reconcile --resolve TASK_ID --outcome accepted --alpha-id VERIFIED_ID --note "官方历史核验依据"
./wq resume
```

`accepted` 仅恢复 GET，程序仍核验 Alpha ID、表达式、全部设置和真实结果，之后才入账。已有 Location 时可以不填 `--alpha-id`。明确没有请求或被拒绝才用 `--outcome lost` / `rejected`，并填写 note；这会释放远端占位，但不会重放旧 POST。

## 备份与恢复

停用新轮次并等待在途结束后，使用 Python `sqlite3.Connection.backup` 或 SQLite `.backup` 备份数据库；WAL 活跃时不要只复制 `.db`。私下备份 `config/`、私有目录和 runtime；cookie、模型登录文件不得上传。恢复到另一机器必须重新核验绝对路径、权限、登录、授权期限、Provider 命令与全部 UNKNOWN，不能只恢复 Git 就宣称恢复运行。

## 正式提交（逐个候选，默认关闭）

`brain_submission` 是独立提交队列；现有 autopilot 的探索授权不自动变成批量提交授权。研究、回测成功和平台接收分别验收。当前提交模块离线测试通过不代表已有真实提交；首次真实接收仍需合格候选和可用会话。

1. `./wq brain submit-check ALPHA_ID`：仅检查本地真实 API 研究快照，输出绑定完整设置的 `request_hash` 及具体阻断项；没有网络请求。教学 Alpha 不能进入正式提交。当前已有 FAIL 的候选应归档，不用重跑来验证提交功能。
2. 在私有目录复制 `templates/submission-review.json`，完成 originality / robustness / data_timing 的证据引用和具体解释，填验收人、带时区的验收时间与 `approved_for_submission`。模板默认拒绝；不能为跑通流程把空证据改成 true。研究验收有效七天，必须匹配目标 Alpha 与完整设置。该文档表示实际研究裁决，程序只能验证其完整性，不能替代人的判断或证明真实性。
3. 配置 `brain_submission.enabled=true`、`brain_submission.authorized_until` 为明确期限，再执行 `./wq brain submit ALPHA_ID --review /私有路径/review.json`。这里只入队，由既有 `run-once` 定时器继续；切换开关不主动提交其他 Alpha。
4. 队列重新 GET Alpha 与 `/alphas/ID/check`，完整检查全 PASS 才 POST 一次 `/alphas/ID/submit`。缺失、FAIL、PENDING、未识别响应均不视为合格；只有PENDING时继续有限轮询。尚未POST且预检阻断的任务，可补齐证据后重执行submit命令恢复检查；已POST任务永不因此重发。检查响应格式或权限与预期不一致时阻断，不能通过猜测响应放行。平台允许的例外目前不自动利用。
5. 提交后先轮询再 GET Alpha；只有匹配身份/设置、平台 status 为 ACTIVE/INACTIVE、stage 为 OS 且有 dateSubmitted 才登记 accepted。HTTP 201、空200、204或队列成功不能单独证明接收。accepted 不记为现金收入，不等于最终有效。
6. `./wq brain reconcile-submit ALPHA_ID`：只读官方状态并保存证据，用于 UNKNOWN 对账及后续状态核验。不能用通用 `reconcile --resolve accepted` 手填提交成功。对账未证明接收就保留原状态；永不借对账重发 POST。此命令与 runner 互斥。

提交本地限制：单在途、滚动24小时最多一次POST，拒绝/未知也计入；非官方额度。POST不自动重试。轮询最多180次队列领取或24小时，超过后保留记录等待对账。登录恢复仅续接 preflight/已有GET，不恢复拒绝过的POST。新提交开关关闭/授权到期不妨碍已有回执GET对账。

私有证据在 `private_dir/brain-submissions/ALPHA_ID/`；SQLite保存请求状态和提交账本。提交后主动状态复核当前由 `reconcile-submit` 命令提供，尚无长期定期跟踪/通知服务。首次真实提交接口与账户响应仍须现场验收；会话失败时执行本人登录，不能用离线fixture证明线上接收。

公共模拟入口新增 `brain_api.max_posts_per_24h`（默认4）与 `brain_api.min_post_interval_s`（默认60秒），覆盖手工入队及自动研究，拒绝和未知尝试同样计数。前者按滚动24小时计，区别于 autopilot 的 UTC 日研究轮数。模拟与提交共享BRAIN限流冷却；模型Provider回落不改变平台等待要求。同账号只能一台主机调度。

### 有限轮次探索

`autopilot.max_cycles_total` 为数据库累计研究轮数的硬上限（默认 null，不限制累计轮次）；失败、拒绝和重复也计数。达到上限时完成已有轮次后停止新建，跨日或重启不重置。`max_cycles_per_day` 是另一独立上限。`interval_s` 控制正常轮次结束后的间隔，错误冷却、平台 Retry-After、单并发和模拟派发限额仍生效。配置下次调度读取。`./wq autopilot status` 显示累计进度；到上限后下一轮没有排期。

### 按轮次查看任务

`./wq tasks` 默认按研究轮次分组，最新轮次在前，初始化/教学/独立任务单列。每组显示模型成本（含关联重试）、轮次状态、真实回测的 Sharpe/Fitness 和 FAIL/PENDING 数量。未知成本不记零，CLI折合不是账单；平台费用、订阅费与收入不包含在模型成本中。使用 `--status` 筛选时仅过滤任务明细，组汇总仍覆盖整轮。`--json` 保持原始任务数组格式。

### 控制提示词消耗与调优复核

当前后续研究使用 `research-v5-quality-first`：用户明确质量优先于token成本，生成与审查均提供完整历史候选、机制与反例，取消简短输出建议，保留原有JSON字段长度上限。继续使用不同Provider审查、测量一致性检查、拒绝历史和平台质量门槛。不以token下降、篇幅增加或审查通过率代替真实回测及稳健性证据。

`./wq autopilot refine-check [--json]` 只读本地真实研究结果，不派发。当前分诊启发式：Sharpe和Fitness分别至少达到官方快照门槛85%，只允许这两项出现FAIL，其余检查必须PASS（SELF_CORRELATION PENDING单列待核实）；缺失、重复、异常数值不放行。85%是本地资源分配启发式，不是平台规则或统计显著性。进入复核仍不代表可以直接调参。

调优设计（尚未启用自动派发）：父候选先过测量机制审查；预登记一个具体可诊断的问题和最多2个单因素变体，变体保留父子关联、计入累计20轮和同族总尝试数，不能变体再无限生变体。保留所有失败，禁止反复查看测试期再修参数。自相关等检查PENDING先补查询，不能靠改表达式绕过；集中度/数据时点等问题先解决其原因。调优后的样本内PASS只可进入稳定性/独立验证，不声称已完成这些验证。当前受限DSL仍禁止盲目窗口/符号重试，没有因此开启参数搜索。

## 真实结果反馈与有限组合（2026-09-22）

配置完成后的链路为研究渠道提出测量对象、机制和反例 → 不同渠道审查 → BRAIN 回测 → 程序诊断 → 下一轮。模型由部署者选择。`research_feedback.enabled=true` 后，每个真实研究模拟入账时自动生成只读资料任务，收集 Alpha、PnL、yearly-stats，失败与缺失数据不冒充通过。

- `./wq autopilot feedback` 查看所有真实研究 Alpha 的诊断、分段缺口和配对相关性。直接诊断实验也包括在内。
- `./wq autopilot collect-feedback` 幂等回填历史资料，不重发模拟或提交。平台原始数值、字段、序列保留在私有目录，模型仅接收固定诊断标签、轮次和抽象提案。
- PnL 按相邻日期区间差分，再对齐相同区间计算相关性；至少252个共同观测。不能用累计曲线相关代替，也不能当作官方自相关。
- 未达总门槛但正 Sharpe、权重/换手/子股票池通过者可保留互补性研究，不等于可提交。由模型审查过的父提案、相同设置、绝对相关性低于0.3才有资格提出一次固定等权 rank 组合；每父对最多一次，最多2个组合计划，计入现有累计轮数。不优化权重、符号、窗口，组合结果不能继续层层组合。Grok 必须解释固定 AST，Devin 可以否决。
- 已有直接实验的两个父信号和成功组合登记为已知结构，禁止重新包装。新测量角色只使用现有已核验字段，旧角色含义不改变。
- 新模拟固定 `testPeriod=P1Y`；提交额外要求有完整PnL、至少3年年度资料、各年正收益、TRAIN/TEST各自 Sharpe≥1.25且Fitness≥1。这些是本地要求，不冒充平台门槛；选择过程中反复看过的TEST仍不是独立未污染样本外。
- 正式提交仍需要逐候选研究验收文档；自动研究不会自行编造原创性/数据时序验收。已有提交队列仍实时查询全部官方检查（含相关性），仅全部PASS才POST一次并核验ACTIVE/OS。

研究累计预算、周派发上限和授权到期时间由部署者设置。Mac需要开机、登录且唤醒，安装后的launchd每60秒检查，不需要模型轮询。

提交节奏与研究节奏分开：代码保留滚动24小时最多一次正式提交POST的本地策略，不是平台规定。官方IQC FAQ说明的是每天最多2000资格分；社区关于非顾问提交次数存在不一致说法，不能据此保证账号额度。收到HTTP429时尊重Retry-After全局冷却，不把冷却到期等同于平台一定解除限流。

来源：[官方积分FAQ](https://support.worldquantbrain.com/hc/en-us/articles/12805645726359-Is-there-a-limit-to-number-of-daily-submissions-or-number-of-daily-points-in-IQC-2026)、[社区讨论（经验非保证）](https://support.worldquantbrain.com/hc/en-us/community/posts/29114870005143-Limit-to-daily-score-of-alphas)、[官方提交门槛](https://platform.worldquantbrain.com/learn/documentation/interpret-results/alpha-submission)。

## 研究范围扩展：字段目录、角色登记与预登记变体（2026-09-23）

模型仍然只看到抽象角色，不看到平台字段名；扩展范围的方法是把已核验字段登记为新角色。

```sh
./wq brain fields --datasets                      # 当前设置下按数据集汇总（只读，私有目录缓存）
./wq brain fields --dataset fundamental6 --min-coverage 0.5 --search margin
./wq brain field-evidence sales enterprise_value  # 生成与既有格式一致的证据快照
./wq policy add-role sales_to_ev --expression '(sales / if_else(enterprise_value > 0, enterprise_value, NaN))' \
  --fields sales,enterprise_value --cluster fundamental --description '过去已知的季度销售额/正企业价值，估值代理；不是增长率或首次披露事件'
./wq policy roles
```

- `add-role` 要求每个字段都有与策略 region/universe/delay 一致的证据快照，写入 `bindings` 与 `evidence_files` 哈希并刷新 `verified_at`；`description` 是模型看到的全部说明，必须写明它不代表什么，且不要写平台字段名。分组字段用 `--group-field`，名字必须是 market/sector/industry/subindustry 之一。
- 策略 `setting_variants`（最多 2 个，只允许 decay/neutralization/truncation）在每轮准入时与基础请求一起预登记进 `allowed_request_hashes`。不带 `when` 的变体与基础同时派发；带 `when`（如 `{"min_turnover":0.2}`，键为 min/max_turnover|sharpe|fitness）的变体在基础结果入账后按条件派发。条件、翻转阈值在准入时冻结进当轮 `protocol.json`，之后改配置不影响已开轮次。基础结果 Sharpe 低于 `autopilot.sign_flip_rescue_sharpe`（默认 -0.8，设 null 关闭）时派发一次预登记的符号翻转复核。基础与全部变体统一判定终态：任一在途、UNKNOWN 或带远端回执的阻断都会让整轮等待或冻结。`autopilot.max_simulations_per_week` 现在按已登记请求数（基础+变体+翻转）计，余额不足时变体记为跳过。每轮最多 `2+变体数` 次平台请求，请相应设置 `limits.sims_per_week` 与 `brain_api.max_posts_per_24h`。
- 模型上下文新增粗档位（收益风险比/收益效率/换手各 5 档）和角色/数据簇使用统计；精确数值、字段名与序列仍不外发。
- `research_feedback.segment_rules` 可覆盖本地分段门槛（`min_sharpe`、`min_fitness`、`min_years`、`max_negative_years`，须为非负有限数，年份为整数）；改动后执行 `./wq autopilot collect-feedback --local-only` 用已有资料本地重算（不入队、不联网；不带该参数会为缺资料的 Alpha 入队只读收集）。报告里 `platform_blockers` 是平台合格状态，`validation_gaps` 是本地稳健性状态，两者分开记账；重算结果带 `recomputed_at`，属于事后裁决。放宽是研究裁决，不改变平台检查与逐候选提交验收。
- `wq policy add-role` 会解析表达式：只允许声明字段与已知算子、不允许负数参数、证据快照类型须为 MATRIX（分组为 GROUP）且含当前设置的覆盖记录；完整策略校验通过后才落盘。角色名、说明、数据簇与变体标签不得含任何已绑定字段 ID，否则策略拒绝加载。`wq brain field-evidence` 重生成已被引用的快照时会同步刷新策略里的摘要。
- 修改策略或角色会改变策略 hash：活动轮次结束，新轮次才使用新范围。复盘与缺口清单见 [2026-09-23 流程复盘](plan/2026-09-23-gap-review.md)。

### 更早规避（2026-09-24）

- **本地自相关预筛**：每个真实结果入账（及 `collect-feedback --local-only` 重算）时，程序用私有目录里的 PnL 计算它与全部已接收提交的日 PnL 相关最大值，写入报告 `submitted_correlation`。≥0.7 打标签"与已提交信号高相关（本地估计）"并不列为提交候选；>0.5 不再作组合父信号。这是本地估计，官方 SELF_CORRELATION 仍是最终裁决（此前实测本地 0.419 对官方 0.414）。
- **排队定时任务不阻塞新轮次**：只有正在跑、待对账或已到期的任务才让自动研究等待；等定时器的排队任务（例如提交在等本地 24 小时频率）不再让研究停摆。
- **周预算预测**：`wq autopilot status` 按最近 24 小时实际登记的请求速率预测周预算耗尽时间，早于本周结束或授权到期时在状态里预警（`budget_forecast`）。
- **提交频率可配置**：`brain_submission.max_posts_per_24h`（默认 1）控制滚动 24 小时 POST 次数；平台自身的提交限额未核验，放大前自行确认。
- **单角色探测汇总进提示词**：每个角色单独回测的最佳档位与最近诊断进入研究提示词，要求模型不再单独重测档位 <1.0 的角色，转向有机制解释的跨簇交互/比率。`research_feedback.max_combination_plans` 现为 12。
- 节奏参数（2026-09-25 复盘后）：`autopilot.max_cycles_per_day` 按 UTC 日计数，设得太低会在北京时间凌晨触顶空转到 08:00；`models.grok.timeout_s` 需覆盖提案的实际耗时（近期 9–13 分钟）；`research_feedback.max_combination_plans` 是终身配额，用完后不再有组合实验，而组合轮与自由探索轮现在自动交替（上一轮是组合则本轮必为探索）。
- 组合父信号配对只比较影响持仓的设置（忽略 `testPeriod`/`visualization`），并要求双方 Sharpe ≥ 0.9（`feedback.MIN_PARENT_SHARPE`；历史 16 次组合中弱父信号从未通过）。2026-09-25 前 `testPeriod` 差异曾让 9 月 22 日之前的强父信号无法参与配对。
- decay 变体触发阈值 2026-09-25 起为基础换手 ≥ 12.5%（Fitness 公式中换手的下限；低于它 decay 不能提高 Fitness）。第 80 轮组合 Sharpe 1.42 / 换手 17.6% 仅 Fitness 未过，就是这类情况。
- 父信号参与 ≥3 次失败组合（组合 Sharpe<1.25，或不高于父信号自身与 0.9 中的较大者，二者满足其一即算失败）后视为"已挖尽"，不再登记组合（`feedback.MAX_FAILED_BLENDS`）。2026-09-25 复盘：同一批 6 个父信号反复配对，第 72–82 轮 6 次组合全部未过；组合枯竭时轮次自动回到自由探索，产出新的强单信号才是根本。
- `focus_roles`（策略）：聚焦角色队列。有未做过单角色基线的聚焦角色时，本轮不登记组合，提示词要求候选只用该角色，提案含其它角色即按输入错误关闭本轮、不回测。用于新角色批次的前 N 轮基线。
- `paused_clusters`（策略）：检查点判定无独立信号的数据簇；自由探索提案含其角色即按输入错误关闭本轮，聚焦基线不受影响。符号翻转复核（`sign_flip`）的结果可作组合父信号。

