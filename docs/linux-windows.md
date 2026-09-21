# Ubuntu/Debian 服务器与 Windows WSL2

项目采用两种部署：macOS 使用既有 launchd + sandbox-exec；Ubuntu/Debian 使用 systemd 用户定时器 + Docker 模型容器。Windows 使用 WSL2 Ubuntu 的同一套 Linux 部署，**不是原生 Windows Python/PowerShell 运行版**。

Grok、Devin 和 Cursor 官方文档均提供 Linux 安装途径。项目此版实际构建并启动了 Linux arm64 的 Grok 1.0.40、Devin 3000.10.31；Cursor 的 Docker 配置已提供，但本轮未构建验证。ZCode 的当前 macOS bundle 不能直接搬到 Linux，Linux 配置暂不包含 ZCode。各 Provider 仍需用户本人登录，并确认自己的订阅可用于该 CLI。

## 架构与条件

宿主运行 Python 队列、SQLite、BRAIN 登录和 API 查询；每次模型调用启动一个短生命周期 Docker 容器。容器仅挂载该次 attempt 目录与该 Provider 的独立 home volume，不挂载源码项目、BRAIN cookie、真实结果库或 Docker socket。容器非 root、根文件系统只读，并有限制 CPU、内存、进程数和超时。

联网用于 Provider 请求，容器网络没有域名白名单，不声称网络完全隔离。Docker CLI 权限本身接近宿主管理员能力，应使用独立普通部署用户，不与不可信用户共享。容器不得运行在可访问敏感内网服务而无网络策略的环境。

建议起步服务器 2 vCPU / 4 GB RAM，磁盘预留镜像与日志空间；这是资源配置建议，尚无压力测试证明。当前是单机 SQLite，不支持多台机器共用运行数据库或同时调度同一个账号。

## Linux 初始化

先准备 Python 3.11+、Git、Docker Engine 和可用的 systemd 用户会话；无需桌面环境。仓库是私有的，拉取需你自己的 GitHub 权限。

```sh
git clone https://github.com/Octo-o-o-o/autowq.git
cd autowq
python3 scripts/setup_local.py
./wq doctor --fix-private
PYTHONPATH=src python3 -m unittest discover -s tests
```

生成器按平台选择 Linux 部署，不安装系统服务、不启用模型、不覆盖已有配置。默认 runtime 在 `~/.local/share/autowq-runtime`。Linux 原有 `chmod 0700/0600`、flock 和进程组逻辑直接复用。

## 构建并登录 Provider

以当前普通用户构建，UID/GID 必须与实际运行 wq 的用户一致：

```sh
docker build --build-arg PROVIDER=grok --build-arg AGENT_UID=$(id -u) --build-arg AGENT_GID=$(id -g) -t autowq-grok:local deploy/docker
docker build --build-arg PROVIDER=devin --build-arg AGENT_UID=$(id -u) --build-arg AGENT_GID=$(id -g) -t autowq-devin:local deploy/docker
python3 scripts/provider_login.py grok
python3 scripts/provider_login.py devin
```

登录助手不挂载任何 BRAIN 文件；按照 CLI 输出用自己的浏览器完成验证。远端服务器的 localhost 浏览器回调可能需要 SSH 隧道或供应商支持的登录方式，不能保证所有套餐无头登录都可用。不要复制朋友或其他人的登录卷。

构建从供应商官方安装地址下载当前 CLI。Devin 安装器最后会自动启动 setup，本 Dockerfile 校验该结尾后仅移除交互 setup 调用，避免构建时登录；若安装器结构变化就构建失败，需重新核对。安装脚本和基础镜像没有固定下载内容，因此首次构建后建议在 `containers.json` 将 image 设为 `docker image inspect --format '{{.Id}}' IMAGE` 返回的 image ID，显式升级时再修改。执行器使用 `--pull=never`，不会运行时悄悄拉取新镜像。

可选 Cursor：同样用 `PROVIDER=cursor` 构建 `autowq-cursor:local`，再 `provider_login.py cursor`。ZCode 暂不开放；不要把 Mac 应用内文件直接当 Linux CLI。

`containers.json` 保存 image、argv、home_volume 与容器 timeout，`config/profiles.json` 保存路由与外层 timeout。内部 timeout 应至少比外层短45秒，为结果回传和清理预留时间。普通进程退出/信号会清理其自有容器；宿主被 SIGKILL 时依靠容器内 timeout 截止。Docker daemon 故障或整机宕机仍需恢复核验，不宣称恰好一次执行。

## BRAIN 与调度

按 [运行手册](operations.md) 完成本机 BRAIN 登录、真实字段证据、策略许可、Provider 预算和有效期配置，再启动自动研究。不能沿用别人的账号或把旧授权期限改成无限期。

```sh
./wq brain login
./wq brain check
# 完成证据、预算和配置核验后：
./wq autopilot start
mkdir -p ~/.config/systemd/user
cp ~/.local/share/autowq-runtime/autowq.service ~/.local/share/autowq-runtime/autowq.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now autowq.timer
systemctl --user list-timers autowq.timer
journalctl --user -u autowq.service -n 50
```

Linux 服务器需要管理员为部署用户启用 linger，用户退出 SSH 后用户定时器才可持续运行：`sudo loginctl enable-linger USERNAME`。实际效果用注销重连与重启演练验证。定时器在上一 tick 退出60秒后再触发，不并发派发；空闲时不调用模型。

暂停：`./wq pause --reason "暂停"`；停定时器：`systemctl --user disable --now autowq.timer`。不要把 systemd 的 inactive 当成故障：oneshot 两次触发之间正常为 inactive。

## Windows 朋友

1. 管理员 PowerShell 执行 `wsl --install -d Ubuntu`，按提示重启并创建自己的 Linux 用户。
2. 安装 Docker Desktop 并启用该 Ubuntu 的 WSL integration，或在 WSL 内独立安装 Docker Engine（二选一）。
3. 在 Ubuntu 终端用 `docker info` 确認连通，在 Linux home（如 `~/autowq`，不要 `/mnt/c`）clone 仓库，执行上述 Linux 步骤。
4. 检查 PID 1 是否为 systemd：`ps -p 1 -o comm=`。需要时按 Microsoft 文档启用，修改后在 PowerShell 执行 `wsl --shutdown` 再进入 Ubuntu。

Windows 休眠、关机、WSL 停止或 Docker Desktop 未启动时不能持续运行。WSL2 适合朋友在本机使用；无人值守24小时运行优先放独立 Linux 服务器。本轮没有 Windows/WSL2 真机可用，因此不能称为 Windows 实机验收通过。

## 从 Mac 切换到服务器

先在服务器完成镜像、本人登录和单次受限任务验收，再停 Mac 的 autopilot，等待在途任务完成并核对 UNKNOWN，之后才启用服务器调度。不要两台机器同时跑同一账号。

源码可以直接 clone；运行状态不能只复制 Git。旧数据库、策略/审查文件、cookie 和任务快照包含绝对路径及证据哈希，没有实现一键跨机迁移。应保留 Mac 的完整私有备份，重新核验目标机的路径、权限和授权，迁移历史去重记录后再连续运行。未经核验不要复制旧 DB 直接启动；当前生成器面向新部署，不宣称自动恢复原会话。

## 本轮验证

本地完整测试203项通过（启用真实 Docker fixture 测试，无跳过）；并非203个真实模型请求。

- macOS 上现有调度继续运行，核心队列未改动。
- Docker Desktop 的真实 Linux 容器验证任务产物回传、宿主项目不可见、Docker socket 不可见、超时退出和自有容器清理；fixture 不调用模型。
- Grok/Devin Linux arm64 镜像真实构建、`--version` / `--help` 启动成功；没有将此当成已完成账号登录或真实模型请求。
- Linux CI 增加 systemd unit 语法验证和真实容器集成测试；结果以提交的 Actions 为准。

官方参考：[Docker 容器运行](https://docs.docker.com/engine/containers/run/)、[Microsoft WSL systemd](https://learn.microsoft.com/en-us/windows/wsl/systemd)、[Grok Build](https://docs.x.ai/build/overview)、[Devin CLI](https://docs.devin.ai/cli)、[Cursor CLI](https://cursor.com/docs/cli/overview)。
