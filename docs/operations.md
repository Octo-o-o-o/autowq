# 运行与恢复

## 已有部署

在项目根运行 `./wq autopilot status` 和 `./wq tasks`。程序由本机 launchd 每60秒唤起，空闲时只检查本地状态，不需要 Codex、聊天窗口或模型充当调度器。Mac 必须保持登录、联网并处于唤醒状态；注销/休眠期间不运行，重新登录后调度恢复。模型登录、BRAIN 人机验证以及授权续期不能保证无人介入。

`./wq autopilot stop` 停止补充研究轮次；已排队任务可以完成。`./wq pause --reason "暂停"` 阻止队列派发并终止本地在途模型进程。`./wq resume` 恢复队列，UNKNOWN 尚未对账时默认拒绝恢复。

本地状态文件：`var/run/autopilot-status.json`、`var/run/progress.json`。日志：`var/run/launchd.out.log` 和 `launchd.err.log`。没有配置邮件或即时消息推送。长期运行应定期检查磁盘和归档日志，不要删除在途调用证据。

## 新机器初始化

在项目根执行 `python3 scripts/setup_local.py`，默认生成：

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
4. 对自己的平台字段和运算符核验后，填 `autopilot-policy.json` 的三个抽象角色绑定、完整 settings、来源、verified_at、valid_until、证据文件绝对路径与 canonical JSON SHA256。真实证据在私有目录，不能交给研究模型。canonical hash 使用 `wq.util.sha256_json`，不是原文件字节哈希。占位模板故意不能通过。
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

`steady`：Grok 研究、Devin 工程/审查优先；`core-only`：只用 Grok/Devin；`cursor-rich` 和 `zcode-rich`：优先使用对应富余额渠道。`wq provider enable` 只取消停用标记，不会绕过模型开关、授权或预算。

`config/config.json` 的 `autopilot.interval_s`、`max_cycles_per_day`、`max_simulations_per_week` 与 `limits.sims_per_week` 控制速度。修改间隔适用于随后结束的轮次，不重写已保存的 next_cycle_at。每日按 UTC、每周按 ISO 周计数；达到上限后自动等待新周期，授权未过期才继续。每个新轮次最多一个候选，不做参数网格救活失败策略。

## 故障处理

| 现象 | 行为 / 恢复 |
|---|---|
| Provider 临时失败 | 首次 + 最多三次重试，每次保留独立副本；Retry-After 不缩短 |
| 明确额度/容量耗尽 | 重试耗尽后冷却该渠道并切备用；普通配置/产物错误不触发回落 |
| BRAIN 401/403 或本地会话过期 | 自动暂停；本人 `wq brain login` 后仅恢复认证失败的已有 GET 查询；不覆盖用户主动暂停 |
| POST 超时/回执丢失 | UNKNOWN，停止重复派发；人工对账 |
| 已保存 Location 后崩溃 | 租约过期后恢复 GET，不重发 POST；最多360次队列尝试后要求检查 |
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
