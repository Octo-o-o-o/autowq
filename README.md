# autowq

[English](README.en.md) | 简体中文

**官网：[autowq.octoooo.com](https://autowq.octoooo.com)** —— 产品介绍、macOS DMG 下载与快速上手指南。

本地运行的 WorldQuant BRAIN 研究工作流：模型提出假设 → 不同渠道审查 → 有限回测 → 真实结果诊断 → 互补信号研究 → 提交前验收。模型、预算、证据和队列由你控制，不依赖聊天窗口持续在线。

**研究工具，不是收益承诺。** 平台筛选通过、正式提交接收、顾问资格和实际到账是不同状态。自动研究不自动授予正式提交权限。

## 怎么用：同一个工具，两个阶段

**阶段一 · [快速到达金牌](https://autowq.octoooo.com/#reach-gold)** —— 面向刚开始 BRAIN 研究、或还没到 Gold 的你。首次向导以三屏引导开场（算力从哪来、旅程怎么走、你从哪里开始），随后检测渠道、生成配置、调起登录；在你设定的预算与授权期限内，受控循环自动推进「提出假设 → 异渠道审查 → 有限回测」，真实结果、失败原因与平台回执全部如实入账；合格候选过提交前验收、核对回执后进入下一轮。研究用量可以全部落在已订阅渠道的 token 富余量里（免费 API 也能起步），不产生额外按量开销。Gold 由平台按 Alpha 与 OS 表现累计判定，没有写死的天数——作者个人实测（2026-09 历史案例，不代表人人如此）：装上第 5 天账号升金牌。

**阶段二 · [金牌之后，持续提高](https://autowq.octoooo.com/#after-gold)** —— 不用重装、不用重新登录；向导起点选「我已是金牌或顾问」即可跳过新手引导直达设置。持续研究框架把本地历史变成下一轮可检验的建议：发现研究问题 → 明确资料缺口 → 授权内有界补证 → 可检验的小实验 → 新结果到来时重评 → 经验与反例回流。改进只在同预算对照里说话，失败与未知同样留档；托盘「研究进展」直接读授权期限、学习状态、已知/未知成本、资料缺口与下一触发条件。顾问之路上，Gold、官方邀请、签约/激活、报酬资格、实际到账是不同状态，以所在地区官方通知为准；WorldQuant 官方写明 Grandmaster 级顾问每季度报酬可达 8,000 美元以上——持续提高研究质量，就是朝这条线走。机制细节见 [Research learning](RESEARCH_LEARNING.md)。

## 安装

需要你自己的模型 CLI 订阅账号或 API Key；没有订阅也可以用官方免费 API 起步（`wq providers free` 列出 Gemini、OpenRouter、智谱、硅基流动等免费预设，只需注册一个 Key）。macOS DMG 自带运行时；其余安装方式需要 Python 3.11+。核心运行时只使用 Python 标准库。支持 macOS；Linux/Windows WSL2 使用 Docker 隔离模型进程。原生 Windows 实验性支持，仅 API 渠道（见方式二下方的 Windows 说明）。

```sh
# 方式一：pipx（推荐；pipx 本身见 https://pipx.pypa.io）
pipx install wq-pilot
mkdir ~/autowq && cd ~/autowq   # 任选目录作为工作区
wq onboard
```

```sh
# 方式二：Homebrew（macOS）
brew tap Octo-o-o-o/autowq && brew install wq-pilot
brew install --cask Octo-o-o-o/autowq/worldquant   # 可选：菜单栏 App
```

方式三：macOS DMG，有两个已公证的包，都从 [Releases](https://github.com/Octo-o-o-o/autowq/releases) 下载，拖入"应用程序"。

- **完整包** `WorldQuant-<版本>.dmg`（约 54 MB）：自带 Python 运行时和引擎，不需要系统 Python、Xcode 命令行工具或 pip。菜单栏应用和包内 `WorldQuant.app/Contents/Resources/bin/wq` 都可以直接用。
- **轻量包** `WorldQuant-<版本>-light.dmg`（约 1 MB）：不含 Python。第一次打开时用本机 Python 3.11 及以上完成设置（Homebrew 或 Xcode 命令行工具）。适合已经装过 Python、想少下载一点的人。设置完成前，包内 `bin/wq` 还不能单独运行。

菜单栏用法和命令行用法见 **[macOS 安装包说明](docs/app-bundle.md)**。未公证版本可用 `brew install --cask` 安装（自动去除隔离属性），或首次右键打开。检查更新下载的是完整包。

Windows（实验性）：只用 `WorldQuantTray.exe`（从 [Releases](https://github.com/Octo-o-o-o/autowq/releases) 下载）。单文件自带引擎，同时是托盘、完整命令行（`--engine <wq 参数>`，或 `--install-cli` 生成 `wq.cmd`）和调度入口；双击后没有配置会先开控制台完成向导。原生 Windows **只支持 API 渠道**（免费预设、OpenAI/Anthropic 协议、自建兼容接口），模型 CLI 订阅需走 WSL2。详见[跨平台部署](docs/linux-windows.md)。

方式四：源码（开发）：

```sh
git clone https://github.com/Octo-o-o-o/autowq.git
cd autowq
./wq onboard
./wq doctor --fix-private
./wq validate result fixtures/synthetic-result-pass.json
./wq import-results fixtures/synthetic-result-pass.json
./wq tasks
```


向导检测宿主 CLI，让你选择渠道、模型 ID、研究/审查/工程角色及可选思考强度。它生成本机配置与隔离运行入口，**不覆盖已有部署、不启动付费推理、不安装调度器**。默认语言按环境判断：中文环境用中文，其余用英文；第一步可以切换，也可用 `--lang zh/en/auto` 显式指定，之后用 `wq config language` 持久切换（菜单栏/托盘在「设置 → 界面语言」）。向导包含供应商CLI与BRAIN登录，并提供[官方注册链接](https://platform.worldquantbrain.com/sign-up)。可以跳过登录，随后用 `./wq onboard --login-only` 继续。模型是否能用取决于你自己的账号；登录命令成功不等于验证了模型权限。

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
- 中英双语任务进度、token 及费用来源；未知费用不记作零。
- 受限并发：`autopilot.concurrent_lanes`（1–8，默认 1）控制同时开放的研究泳道数，每条泳道仍是完整的研究→审查→模拟；同一渠道同一时刻只有一个调用（忙时排队等待，不烧重试），平台模拟与提交仍严格串行。预算、授权期限、平台限流、崩溃恢复与去重不变，UNKNOWN 一律全局冻结。泳道渠道默认按预设路由自动错开；`autopilot.lane_pins`（如 `{"2": {"research": "zcode", "review": "grok"}}`，泳道号从 0 起）可把某条泳道固定为指定「研究→审查」对——渠道不可用时该泳道等待而不换对，托盘/菜单栏的「泳道固定」菜单亦可直接设置。
- macOS 菜单栏 / Windows 托盘（实验性）：状态、轮次历史与已提交 Alpha，切换路由预设、渠道与频率上限，绑定/核验 BRAIN 账号（拉起终端完成登录，密码不保存），提交成功或任务失败时系统通知。

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

---

<p align="center">
  <a href="https://www.octoooo.com"><img src="https://octoooo.com/site-assets/favicon-64.png" width="30" alt="OctoLab 五瓣标"></a><br>
  <sub>By <a href="https://www.octoooo.com"><b>OctoLab 千手实验室</b></a> · 那里还有更多有趣的项目，对各种合作保持开放</sub>
</p>

### 历史驱动研究

新增本地实验谱系、追加式反馈、带反例的 shadow 规则和单调用多计划筛选。沿用原预算、审查与提交边界；软件验证不代表 Alpha 质量提升。配置、回放、冻结实验和只读反馈刷新见 [Research learning](RESEARCH_LEARNING.md)。

## 参与共建

欢迎提 [Issue](https://github.com/Octo-o-o-o/autowq/issues) 和 [PR](https://github.com/Octo-o-o-o/autowq/pulls)——Bug、想法、文档勘误、新的研究议题建议都可以，每一个都会认真看。这个项目变得更好，靠的是大家一起动手。

有合作意向？请通过 [OctoLab 千手实验室](https://www.octoooo.com) 或 [GitHub @Octo-o-o-o](https://github.com/Octo-o-o-o) 联系我。
