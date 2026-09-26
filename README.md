# autowq

[English](README.en.md) | 简体中文

本地运行的 WorldQuant BRAIN 研究工作流：模型提出假设 → 不同渠道审查 → 有限回测 → 真实结果诊断 → 互补信号研究 → 提交前验收。模型、预算、证据和队列由你控制，不依赖聊天窗口持续在线。

**研究工具，不是收益承诺。** 平台筛选通过、正式提交接收、顾问资格和实际到账是不同状态。自动研究不自动授予正式提交权限。

## 安装

需要 Python 3.11+，以及你自己的模型 CLI 订阅账号或 API Key。核心运行时只使用 Python 标准库。支持 macOS；Linux/Windows WSL2 使用 Docker 隔离模型进程。原生 Windows 实验性支持托盘与调度控制层（见方式二下方的 Windows 说明）。

```sh
# 方式一：Homebrew（macOS）
brew tap Octo-o-o-o/autowq && brew install wq-pilot
brew install --cask Octo-o-o-o/autowq/worldquant   # 可选：菜单栏 App
```

方式二：macOS 菜单栏 App。从 [Releases](https://github.com/Octo-o-o-o/autowq/releases) 下载 `WorldQuant-<版本>.dmg`，拖入"应用程序"后双击；首启向导引导选择工作区、准备运行时并调起 onboard，完成后菜单栏常驻。未公证版本可用 `brew install --cask` 安装（自动去除隔离属性），或首次右键打开。

Windows（实验性）：托盘与 macOS 菜单栏同功能、同控制层。从 [Releases](https://github.com/Octo-o-o-o/autowq/releases) 下载 `WorldQuantTray.exe`（单文件、内置引擎），在已完成 `wq onboard` 的工作区运行，可用 `--workspace <目录>` 指定；或源码方式 `pip install pystray Pillow` 后运行 `python scripts/setup_windows.py` 注册任务计划调度与登录自启。Provider 不随 exe 发布，需自行配置 profiles.json；完整研究栈仍建议 Linux / Windows WSL2，见[跨平台部署](docs/linux-windows.md)。

方式三：源码（开发）：

```sh
git clone https://github.com/Octo-o-o-o/autowq.git
cd autowq
./wq onboard
./wq doctor --fix-private
./wq validate result fixtures/synthetic-result-pass.json
./wq import-results fixtures/synthetic-result-pass.json
./wq tasks
```

方式四：pipx（`pipx install wq-pilot`，随后 `mkdir ~/autowq && cd ~/autowq && wq onboard`）。**PyPI 发布尚未开通（Trusted Publisher 未配置），此方式暂不可用**；开通后再启用。


向导检测宿主 CLI，让你选择渠道、模型 ID、研究/审查/工程角色及可选思考强度。它生成本机配置与隔离运行入口，**不覆盖已有部署、不启动付费推理、不安装调度器**。默认语言按环境判断：中文环境用中文，其余用英文；第一步可以切换，也可用 `--lang zh/en` 显式指定。向导包含供应商CLI与BRAIN登录，并提供[官方注册链接](https://platform.worldquantbrain.com/sign-up)。可以跳过登录，随后用 `./wq onboard --login-only` 继续。模型是否能用取决于你自己的账号；登录命令成功不等于验证了模型权限。

支持 Grok Build、Devin、Cursor、ZCode，以及新增 Claude Code、Codex、Gemini CLI、GitHub Copilot CLI、Qwen Code、OpenCode。支持 OpenAI Chat Completions/Responses 和 Anthropic Messages API，可手动配置 Base URL、Key 和模型。不同渠道的模型列举、平台支持与核验范围见 **[Provider与高级流程指南（中英）](docs/providers-workflow.md)**。持续自动研究仍要求不同渠道审查。

高级模式提供规范 JSON、CLI 分步骤编辑和自然语言 AI 编辑草稿；可以调整角色路由、补充 Prompt、模拟/反馈开关和有限组合。统一通过 `workflow validate/diff/apply` 应用，预算和提交质量门槛继续生效。

```bash
./wq providers list
./wq providers models devin
./wq workflow init --output config/workflow.draft.json
./wq workflow edit config/workflow.draft.json --output config/workflow.edited.json
```

接下来请按 **[完整 onboarding 操作单（中英双语）](docs/onboarding.md)** 完成：本人登录 → 本地预算/期限 → BRAIN 登录与权限 → 字段证据 → 单轮真实核验 → 安装调度。初始模型、API、自动研究和提交全部关闭，数据证据模板故意保持未验证。

## 能做什么

- 显式选择本地模型和角色；任务冻结自己的路由快照。
- 真实模拟、GET 轮询和结果入账；POST 结果未知时停止重发。
- 失败原因反馈、时间分段资料、日 PnL 相关性与有限组合实验。
- 逐候选正式提交：研究验收、最新平台检查、单次 POST、真实接收状态核验。
- 中文任务进度、token 及费用来源；未知费用不记作零。
- 单并发、预算、授权期限、平台限流、崩溃恢复与去重。
- macOS 菜单栏 / Windows 托盘（实验性）：状态、轮次历史与已提交 Alpha，切换路由预设、渠道与频率上限，提交成功或任务失败时系统通知。

## 常用命令

```sh
./wq help
./wq help export
./wq --version
./wq login                         # BRAIN登录，显示注册链接
./wq onboard --login-only          # 继续初始化登录
./wq export --kind summary --output exports/summary.json
./wq export --kind results --format csv --output exports/results.csv
./wq brain fields --datasets           # 只读字段目录快照，按数据集汇总
./wq policy roles                      # 查看模型可用的抽象角色与预登记变体
```

扩展研究范围不需要改代码：`wq brain field-evidence` 生成字段证据，`wq policy add-role` 把已核验字段登记为模型可见的抽象角色；策略里的 `setting_variants` 让每轮同时回测少量预登记的 decay/中性化变体，全部结果入账。见[运行手册](docs/operations.md)。

导出支持summary/tasks/results和JSON/CSV；结果默认排除合成数据，任务导出仅含允许的状态字段。不会导出Cookie、密码、任务输入或证据文件路径，不覆盖同名文件。导出包含你的研究状态/指标，仅保存在本地；`exports/`被Git忽略。

## 查看与停止

```sh
./wq onboard --list                 # 仅检测宿主CLI，不修改配置
./wq preset show
./wq autopilot status
./wq autopilot feedback             # 中文诊断；--json供程序读取
./wq tasks
./wq autopilot stop                 # 停止补充新轮次
./wq pause --reason "manual pause" # 暂停队列及本地模型调用
```

模型登录失效、人机校验、UNKNOWN 请求和授权到期可能需要本人处理。Mac 休眠或服务器停机期间不会运行。当前没有全自动研究验收、现金交易、签约或长期盈利保证。

## 开发与文档

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
```

- [首次使用与模型选择](docs/onboarding.md)
- [运行、配置、提交与恢复](docs/operations.md)
- [Linux / Windows 部署（含实验性原生 Windows 托盘）](docs/linux-windows.md)
- [架构与数据边界](docs/architecture.md)
- [第三方与许可](NOTICE.md)

配置、Cookie、真实 Alpha、数据库、模型输出、运行日志和个人研究档案不进入 Git。`fixtures/` 仅含合成数据，不能作为真实研究成绩。源码备份不等于运行状态备份。

本项目非 WorldQuant 官方产品。本项目以 Apache License 2.0 开源（见 LICENSE 与 NOTICE.md）；公开发布前需由维护者核查完整 Git 历史与第三方内容。

全历史复盘支持手动与自动触发：`./wq auto-research report`、`./wq auto-research run`；设置周期见[Auto Research说明](docs/auto-research.md)。不同渠道复核后，固定指导语进入后续研究与审查Prompt，预算和质量门槛不变。
