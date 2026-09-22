# 私有仓库交付检查 — 2026-09-21

结论：可作为有限范围的自动研究试点继续运行，并进入私有 Git 维护；不能宣称没有任何缺陷、达到全局最优、完成原创机制研究或具备稳定收益。

## 检查范围

覆盖仓库程序、全部离线测试、配置模板、CLI 入口、构建元数据、部署生成器、文档与忽略规则。重点人工核验调度/路由/进程生命周期、预算授权、BRAIN 单次派发和恢复、研究门禁、数据隔离、去重与账本。对全部提交文件进行了路径/账号/常见凭证模式扫描和 diff whitespace 检查。该扫描不是独立安全审计，无法证明任意隐私信息都不存在。

## 修复

- UNKNOWN BRAIN 对账同步更新远端占位；lost/rejected 不再永久阻塞后续请求。
- accepted 不直接标完成：恢复已知 Location 的查询，或接收本人核实的 Alpha ID，仅 GET 后核验表达式与设置再入账。错误匹配仍保持 UNKNOWN。
- 租约恢复时将未完成 POST 意图转 UNKNOWN，已有回执 GET 可恢复；不因为队列耗尽而丢失未知 POST 标识。
- 本地 cookie 过期也走认证暂停；重新登录后恢复此前被阻断的 GET。
- 普通路由与 autopilot 统一：非明确额度/容量错误不触发自动 Provider 回落。
- 非有限 Retry-After、非对象证据声明、非法卡片 config、非有限或布尔 PnL 被拒绝/安全处理；移除过时账号诊断。
- 新 clone 有全部关闭的初始化配置、可重定位 launchers 和 launchd 生成器；不依赖原机器的调试目录。
- 分离个人账号/真实研究证据与可版本化代码，加入离线 CI、包安装入口与运行恢复手册。

## 验证

- 本地全量：197 个 unittest，全部通过，exit 0。
- 从暂存区完整导出的干净副本：197 个测试通过，exit 0；不使用本机运行配置或私有证据。
- 干净副本 wheel 构建、安装到临时目录、`python -m wq --help` 均成功。
- macOS sandbox 实际子进程验证：项目文件读取/写入被拒绝，隔离任务目录写入成功。
- 新增故障测试覆盖对账释放占位、接受回执后 GET 验证、错误 Alpha 拒绝、过期会话恢复、POST/GET 崩溃恢复及非容量失败不换渠道。
- 运行实例检查：持续研究开关启用、launchd 在正常空闲间隔，已有两轮真实自动闭环归档；两轮平台质量均未通过。此次修复没有冒充新一轮真实策略验收。
- GitHub CI 配置覆盖 Linux/macOS 与 Python 3.11/3.12/3.13，最终结果以相应提交的 Actions 运行记录为准。

## 明确保留的边界

- 首次财报机制研究缺少充分时点/原始版本/历史样本证据；平台内探索不替代该协议。
- 自动化没有提交 Alpha、账号签约或收入保证；通过筛选后仍需后续研究验收。
- 登录、人机校验、授权到期、UNKNOWN 和耗尽的 GET 尝试可能需要本人处理。
- 机器睡眠/注销时不执行；没有外部推送告警、整机恢复演练或长周期稳定性证据。
- Provider CLI 的命令形态和服务条款可能变化；新增机器仍需验证真实调用。macOS 定向路径沙箱不是全机隔离。
- 真实数据库、cookie、策略授权、字段证据和历史研究材料仅在本地，Git 是源码备份，不能单独恢复生产状态。
- 保持 private，尚未选择开源 LICENSE；公开前须重新检查完整历史、权限和材料。

## 首次使用与反馈流程更新 — 2026-09-22

新增中英文 onboarding 向导、中文/英文 README、双语首轮操作单。向导检测宿主CLI并让部署者显式选择模型与角色；Mac生成定向沙箱入口，Linux/WSL生成Docker入口。配置、登录可用性、真实模型调用分别记录，不将检测到可执行文件视为账号就绪。初始化拒绝覆盖已有配置/runtime，所有执行开关和授权默认关闭。

本次同时版本化此前研究反馈、分段资料、有限组合、正式提交队列、认证恢复与中文任务计量更新。个人运行记录移到忽略的本地文档，公共文档面向新部署。上传现有私有仓库，不改变可见性或授予新的开源许可。

本地全套267项：265通过，2项需要显式Docker fixture环境的集成测试跳过。新增6项覆盖模型选择、Mac/Linux命令传递、单渠道限制、无效输入不写入、覆盖保护和CLI入口。首次使用真实服务登录/模型请求仍由新部署者验证；本轮未把本机凭据或运行库带入初始化测试。GitHub各平台结果以本次提交的Actions为准。

暂存区导出的干净源码副本再次运行同样267项检查，结果265通过/2跳过；随后在独立runtime实测英文非交互onboard、doctor、合成结果校验/导入、preset show和tasks，全部退出0。README与onboarding相对链接均指向导出副本内文件。暂存的89个文件通过本机路径、账号/真实Alpha标识、常见凭证模式和运行文件路径扫描；该有限扫描不等同于独立安全审计或完整历史审计。

## 注册登录、语言和通用命令更新 — 2026-09-22

初始化新增语言选择：按系统locale默认中文或英文，支持显式覆盖并保存本机偏好；向导各步骤含英文，CLI命令帮助可显示英文。初始化包含供应商CLI与BRAIN交互登录、官方注册链接和可恢复的登录待办；不把登录命令退出0当作模型权限验证。提供`--login-only`和`--skip-login`，非交互初始化不读取密码，登录不启用研究或提交。

新增`help [command ...]`、`--version`、`login`和`export`；补齐Cursor/ZCode预算设置。导出仅允许指定状态/指标列，支持JSON/CSV、0600权限、拒绝覆盖和CSV公式字符串转义；不导出凭据、任务payload、提示词及证据路径。默认过滤模拟/提交中的合成记录，任务状态按全部任务展示。

验证：274项离线测试，272通过、2项Docker集成跳过。新增用例覆盖locale优先级、英文嵌套帮助、导出字段/权限/覆盖保护、其他Provider预算和交互语言/登录状态恢复。登录流程用替代服务返回验证，不声称替新用户完成真实注册或登录。上一提交的GitHub全部检查已通过；本次远端结果以对应Actions为准。

同一暂存内容在无本机配置的干净副本再次通过274项检查（272通过、2跳过）。另实测中文locale下非交互初始化默认中文、显式英文帮助、合成结果导入、默认真实结果JSON导出为空以及显式包含合成结果的CSV导出。测试全程使用临时配置/运行目录，没有重新登录现有生产账号。

## Provider and advanced-workflow extension (2026-09-22)

- Added six macOS CLI JSON transports (Claude Code, Codex, Gemini, Copilot, Qwen Code, OpenCode), installation discovery, vendor model listing where supported, and explicit model selection. Existing CLI routes remain unchanged. Devin's installed CLI requires `models list`; that read-only listing succeeded on this host.
- Added OpenAI Chat Completions, OpenAI Responses and Anthropic Messages transports with configurable endpoints/model IDs, private key references, no credential-forwarding redirects, result validation, queue budgets and token accounting. Dollar cost stays unknown when not supplied.
- Added a versioned workflow JSON/schema, manual and interactive editing, queued AI draft editing, validation/diff/apply, idle-queue/runner-lock checks and real routing/prompt/stage/combination integration. Required review and submission gates stay enforced.
- Updated both READMEs and the bilingual provider/workflow guide, including platform limits and configuration versus account verification boundaries.
- Focused integration: 15 tests passed, including local HTTP wire requests for all three APIs, actual subprocess CLI fixtures, actual macOS sandbox execution with denied project reads, no-secret HTTP error output, budget enforcement, usage rendering and workflow application. No production API inference or newly supported CLI inference was executed. Existing live account/configuration was not edited.

- Final clean-source full suite: 289 discovered, 287 passed, 2 optional Docker tests skipped. Wheel build passed using isolated build dependencies. The first no-build-isolation attempt failed because the host Python lacks setuptools; no global dependency was installed. macOS sandbox integration passed; Linux CI skips that one host-specific test.

## All-cycle research review (2026-09-23)

Added all-closed-cycle diagnostic snapshots, manual/idle periodic review, research plus distinct-provider verification, hash/citation validation and fixed guidance injection into future prompts. Eight focused tests passed; full suite 297 discovered, 295 passed, two optional Docker tests skipped. This verifies software behavior, not improved Alpha quality. Raw platform data is not copied to model packets.
