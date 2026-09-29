# macOS 安装包：只用 DMG / macOS bundle: the DMG alone

[中文 README](../README.md) · [English README](../README.en.md)

## 结论 / Summary

macOS 只需要 `WorldQuant-<版本>.dmg`。DMG 里带了 Python 3.12 运行时（arm64 与 x86_64 各一份）、预装好的引擎、菜单栏应用和命令行入口。**不需要**系统 Python、Xcode Command Line Tools、pip、Homebrew 或源码。

On macOS, `WorldQuant-<version>.dmg` is all you need. It ships a Python 3.12 runtime (one arm64, one x86_64), the engine preinstalled, the menu-bar app and command-line entry points. You do **not** need system Python, Xcode Command Line Tools, pip, Homebrew or a source checkout.

安装包带不进去、必须由本人准备的东西 / What no installer can bundle:

- 你自己的模型渠道：供应商 CLI（Claude Code、Codex、Grok 等，按供应商官方方式安装并登录）或 API Key。/ Your own model providers: vendor CLIs installed and signed in the vendor's way, or API keys.
- WorldQuant BRAIN 账号。/ A WorldQuant BRAIN account.
- macOS 12 或更新版本。/ macOS 12 or newer.

## 应用内路径 / Paths inside the app

以下假设应用已拖到 `/Applications`。应用拒绝直接从 DMG 或下载目录运行，请先拖进“应用程序”。

The paths below assume the app is in `/Applications`. The app refuses to run from the DMG or a translocated location; move it into Applications first.

| 路径 / Path | 作用 / Purpose |
| --- | --- |
| `WorldQuant.app/Contents/Resources/bin/wq` | 完整 `wq` 命令行，使用应用自带的 Python 与引擎 / Full `wq` CLI on the bundled Python and engine |
| `WorldQuant.app/Contents/Resources/bin/wq-setup` | 安装运行时、注册/移除后台调度，输出单行 JSON / Install runtime, register/remove the scheduler; one-line JSON output |
| `~/Library/Application Support/WorldQuant/` | 首次设置时复制的运行时与 venv，后台调度用它 / Runtime copy and venv created at setup; the scheduler runs from here |

## 方式 A：菜单栏应用 / Option A: menu-bar app

双击应用 → 「首次设置…」→ 选择工作区（建议 `~/autowq`）→ 在弹出的 Terminal 完成 `wq onboard` → 回到菜单「设置 → 启用调度与自启」。

Open the app → “First-time setup…” → choose a workspace (`~/autowq` recommended) → finish `wq onboard` in the Terminal window it opens → back in the menu, “Settings → Enable scheduling & auto-start”.

## 方式 B：不用菜单栏，只用命令行（适合交给 AI 操作）/ Option B: CLI only, no menu bar (suited to AI agents)

菜单栏应用不需要打开。下面每条命令都可以直接复制执行；`wq-setup` 的输出是单行 JSON，失败时带 `error` 字段且退出码非 0。

The menu-bar app never has to be opened. Every command below can be run as-is; `wq-setup` prints one JSON line, and on failure it includes an `error` field and exits non-zero.

```sh
WQBIN=/Applications/WorldQuant.app/Contents/Resources/bin
mkdir -p ~/autowq && cd ~/autowq
"$WQBIN/wq" --version
"$WQBIN/wq" onboard --list
```

可选：把 `wq` 放进 PATH（软链可以正常解析到应用内）。/ Optional: put `wq` on PATH (symlinks resolve back into the app).

```sh
mkdir -p ~/.local/bin && ln -sf /Applications/WorldQuant.app/Contents/Resources/bin/wq ~/.local/bin/wq
```

初始化（无人交互写法；模型 ID 必须是你账号实际可用的）/ Initialize (non-interactive; model IDs must be ones your account can use):

```sh
"$WQBIN/wq" onboard --lang en --non-interactive \
  --providers grok,devin \
  --model grok=grok-4.7 --model devin=swe-2-max \
  --research grok --review devin --engineering devin
"$WQBIN/wq" doctor --fix-private
```

供应商登录和 `wq brain login` 需要本人在交互终端里输入，AI 不应代填密码。完整步骤见 [onboarding 操作单](onboarding.md)。

Vendor sign-in and `wq brain login` need the person at an interactive terminal; an AI agent should not type passwords. Full steps: [onboarding guide](onboarding.md).

需要后台定时运行时（不启用菜单栏）/ Background scheduling without the menu bar:

```sh
"$WQBIN/wq-setup" setup --workspace ~/autowq
"$WQBIN/wq-setup" activate --workspace ~/autowq --runner-only
```

`setup` 把运行时复制到 `~/Library/Application Support/WorldQuant/` 并安装引擎；`activate --runner-only` 只注册 `com.worldquant.wq-runner` LaunchAgent（每 60 秒一次 `wq run-once`），不注册菜单栏自启。移除调度：`"$WQBIN/wq-setup" deactivate`，不动工作区数据。

`setup` copies the runtime into `~/Library/Application Support/WorldQuant/` and installs the engine; `activate --runner-only` registers only the `com.worldquant.wq-runner` LaunchAgent (`wq run-once` every 60 s), not the menu-bar autostart. Remove scheduling with `"$WQBIN/wq-setup" deactivate`; workspace data is untouched.

### 给 AI 代理的约定 / Conventions for AI agents

- 所有 `wq` 命令都在工作区目录里运行（或加 `--config <工作区>/config/config.json`）。/ Run every `wq` command inside the workspace directory (or pass `--config`).
- 读状态优先用机器可读输出：`wq tasks --json`、`wq autopilot status --json`。/ Prefer machine-readable output for state.
- `wq help <命令>` 查看某条命令的参数。/ `wq help <command>` shows a command's options.
- 暂停一切：`wq pause --reason "<原因>"`；不要删除 `var/` 或 `config/` 来“重置”。/ Stop everything with `wq pause`; never delete `var/` or `config/` to “reset”.
- 自动研究默认不自动提交 Alpha；开启正式提交需要本人决定。/ Autopilot never submits alphas by default; enabling submission is the person's decision.

## 升级 / Upgrading

用新 DMG 替换 `/Applications/WorldQuant.app`，再打开一次应用（或运行 `wq-setup setup --workspace <工作区>`）。研究任务正在运行时不会替换引擎，等空闲时再更新。

Replace `/Applications/WorldQuant.app` with the new DMG's copy, then open the app once (or run `wq-setup setup --workspace <workspace>`). The engine is never swapped while research is running; it updates when idle.

## 构建 / Building

`bash packaging/macos/build_dmg.sh`。脚本下载固定版本的 [python-build-standalone](https://github.com/astral-sh/python-build-standalone)（校验 sha256，缓存在 `~/Library/Caches/wq-build`），把引擎 wheel 解包进两份运行时，逐个签名其中的 Mach-O 后再签应用。升级内置 Python 时同时修改脚本里的 `PBS_TAG`、`PBS_PY` 和两个 sha256。

The script downloads a pinned python-build-standalone release (sha256-checked, cached in `~/Library/Caches/wq-build`), unpacks the engine wheel into both runtimes, signs every Mach-O inside them, then signs the app. To bump the bundled Python, change `PBS_TAG`, `PBS_PY` and both sha256 values together.
