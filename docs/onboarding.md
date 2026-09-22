# 首次使用 / First-run onboarding

[中文 README](../README.md) · [English README](../README.en.md)

## 1. 检测并选择 / Detect and choose

从源码仓库根运行。Python需要3.11+。先通过供应商官方方式安装CLI并登录自己的账号；向导不安装软件、不读取凭证、不探测收费接口。

Run from the source checkout with Python 3.11+. Install CLIs through their vendors and use your own accounts. The wizard does not install software, read credentials or probe paid endpoints.

```sh
./wq onboard --list
./wq onboard --lang zh
# or:
./wq onboard --lang en
```

- macOS：检测 PATH 和常见安装路径，可输入自定义绝对路径；生成 sandbox-exec 入口。沙箱限制特定本地目录，非全机隔离。
- Linux/WSL2：宿主检测结果仅作参考，实际运行隔离Docker镜像。向导可选择尚未构建的渠道，后续必须构建镜像并登录；不把宿主登录复制到容器。
- Grok/Devin/Cursor要求填写本人CLI支持的模型ID。Grok可选思考强度；Devin的Max等规格使用完整模型ID。ZCode只支持Mac应用现有模型配置，不伪造模型覆盖能力。
- 一个渠道可以初始化，但不满足自动研究的不同渠道审查要求。两个及以上渠道需选择不同research/review角色。向导不添加隐式备用供应商。

On macOS, the wizard detects PATH/common install locations, accepts custom executable paths and generates targeted sandbox launchers. On Linux/WSL2, it configures Docker images instead of running host CLIs; build and authenticate those images separately. Grok, Devin and Cursor need explicit model IDs supported by your account. Grok has an optional effort setting. ZCode inherits its Mac app configuration. One provider is allowed for setup, but autopilot needs distinct research and review providers. There is no implicit provider fallback.

无人交互示例（模型ID是示例，须先确认本人可用；不运行模型）：
Non-interactive example (verify these example IDs with your account first; this does not call models):

```sh
./wq onboard --lang en --non-interactive \
  --providers grok,devin \
  --model grok=grok-4.7 --reasoning-effort grok=xhigh \
  --model devin=swe-2-max \
  --research grok --review devin --engineering devin
```

可加 `--binary grok=/absolute/path/to/grok` 和 `--runtime /absolute/path/outside/repo`。已有任一本机配置或runtime时拒绝覆盖。不要为了重跑向导删除正在运行的实例。

Use `--binary grok=/absolute/path/to/grok` for a custom executable and `--runtime /absolute/path/outside/repo` for a separate runtime. Existing configuration/runtime is never overwritten. Do not delete a running deployment to rerun the wizard.

## 2. 验证离线管线 / Verify offline operation

```sh
./wq doctor --fix-private
./wq validate result fixtures/synthetic-result-pass.json
./wq import-results fixtures/synthetic-result-pass.json
./wq report
./wq preset show
```

doctor在未配置登录、预算和策略时会显示阻断；这是待办清单，不是已经可以跑真实任务。合成导入不算真实成绩，不需要 `--real`。

Doctor will report missing login, budget and policy prerequisites. Treat them as a checklist, not proof of readiness. Synthetic imports are not real research results and do not need `--real`.

## 3. 登录模型、启用预算 / Sign in and enable bounded model use

macOS在供应商CLI中本人登录。Linux/WSL按[部署指南](linux-windows.md)构建所选镜像，再执行 `python3 scripts/provider_login.py PROVIDER`。新配置位于忽略的 `config/config.json` 与 `config/profiles.json`；Linux另有runtime下的 `containers.json`。

On macOS, sign in using each vendor CLI. On Linux/WSL, build the selected images as described in the deployment guide, then run `python3 scripts/provider_login.py PROVIDER`. Local config files are ignored by Git; Linux also uses `containers.json` in the runtime directory.

逐个所选渠道登记小额预算，例如 / Set a small budget for each selected provider, for example:

```sh
./wq budget grok --enable --remaining 4 --unit calls
./wq budget devin --enable --remaining 4 --unit calls
```

在 `config/config.json` 设置所选 `models.PROVIDER.enabled=true`，并设置 `routing.authorized_until` 为你选择的、含时区的未来截止时间。不要复制其他人的授权。初始累计研究上限为4轮，失败/拒绝也计数；只有有意识调整才扩张。预算是本地控制，不查询供应商真实余额。

Set `models.PROVIDER.enabled=true` for each selected provider and choose a future timezone-aware `routing.authorized_until` in `config/config.json`. Do not reuse another person's authorization. The initial cumulative cap is four cycles, including failures/rejections. Budgets are local controls, not a verified provider balance.

## 4. BRAIN与证据 / BRAIN and evidence

```sh
./wq brain login
./wq brain check
```

本人完成必要人机校验；密码不写入配置。Mac可选 `./wq brain keychain-save`，然后按[运行手册](operations.md)启用Keychain自动登录。Linux没有本项目的Keychain自动刷新实现。

Complete identity challenges yourself. Passwords are not stored in project configuration. macOS optionally supports `./wq brain keychain-save` and Keychain refresh as documented in operations. That refresh mechanism is not implemented on Linux.

在本机私有目录核验并保存字段、运算符和设置证据，填 `config/autopilot-policy.json` 的 bindings、settings、source、verified_at、valid_until和evidence_files。每份JSON证据的摘要使用：

Keep verified field/operator/settings evidence in your private directory. Fill policy bindings, settings, sources, timestamps and evidence_files. Generate canonical JSON evidence hashes with:

```sh
PYTHONPATH=src python3 -c 'import sys; from wq.util import read_json,sha256_json; print(sha256_json(read_json(sys.argv[1])))' /absolute/path/evidence.json
```

设置 `brain_api.enabled=true`、`brain_api.authorized_until`。反馈功能需要 `research_feedback.enabled=true`；需要分段核验时，确认账号支持相应设置再固定 `settings.testPeriod`。模板字段不代表你的账号拥有数据权限。登录、启用开关或HTTP成功均不能替代实际证据。

Set `brain_api.enabled=true` and an expiration in `brain_api.authorized_until`. Enable result feedback with `research_feedback.enabled=true`. Verify supported settings before selecting a fixed test period. Template fields do not establish your account's data entitlement. Login, switches and HTTP success are not substitutes for evidence.

## 5. 先验证一轮，再安装调度 / Validate one cycle before scheduling

```sh
./wq autopilot start
./wq run-once --lease 3600
./wq tasks
./wq autopilot status
```

`run-once`只推进一个到期队列步骤，不会一次完成整轮。待步骤完成后再运行，遵守显示的not-before/冷却，直到有真实模拟、诊断和账本。UNKNOWN先对账，不重发POST。若缺证据/权限，保留阻断，不为了演示清除门禁。

Each `run-once` processes one due queue step, not a whole cycle. Run again after completion, respecting not-before/cooldown, until you have real simulation evidence and accounting. Reconcile UNKNOWN requests without repeating POSTs. Keep missing-evidence/access blockers intact.

确认闭环后按[macOS调度操作](operations.md)或[Linux/WSL调度操作](linux-windows.md)安装生成的服务。同账号只由一个实例调度。机器需保持运行；向导本身不安装服务。

Once verified, install the generated scheduler using the linked platform instructions. Use one scheduler per BRAIN account. The machine must remain awake/running; onboarding itself installs no service.

## 6. 提交、恢复和已有部署 / Submission, recovery and existing deployments

正式提交独立于研究：保持默认关闭，先准备候选验收文档，再按运行手册的 `brain submit-check`、`brain submit --review`、`brain reconcile-submit` 操作。系统会重新核对官方门槛与真实接收状态，不把一次HTTP返回当作成功。通过或接收不代表收入。

Submission is separately gated and disabled by default. Prepare a per-candidate review, then use `brain submit-check`, `brain submit --review` and `brain reconcile-submit` as documented. Platform checks and actual acceptance are verified separately; neither implies income.

已有部署不要重跑向导。先停止新增轮次、等待在途结束，私下备份配置与数据库，再修改profiles中显式模型和角色；Linux还要同步containers的CLI argv。冻结任务不会追溯更改。故障定位用 `doctor`、`tasks`、`autopilot status`；停止新增用 `autopilot stop`，暂停队列用 `pause`。源码、配置与运行状态分别备份，真实资料不得公开。

Do not rerun onboarding over an existing deployment. Stop new cycles, let active work finish, privately back up configuration/database, then edit explicit model/role settings. On Linux, also update container CLI arguments. Frozen tasks retain their original configuration. Use doctor/tasks/status for diagnosis, autopilot stop to stop new cycles, and pause to pause the queue. Back up source, configuration and runtime state separately; keep real data private.
