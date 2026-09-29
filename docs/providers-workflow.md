# Providers and advanced workflows / 模型渠道与高级流程

## 中文

渠道、模型、账号权限是三个不同概念。检测到 CLI 只证明找到可执行文件；模型列表不证明账号可调用；登录成功也不证明有剩余额度。配置过程不发付费推理请求。

| 渠道 | 自动检测 | 模型选择 | 当前运行方式 |
|---|---|---|---|
| Grok Build、Devin、Cursor | 本地路径 | CLI 动态列举，输入准确 ID | macOS 沙箱；Linux/WSL Docker |
| ZCode | macOS 应用路径 | 使用应用内选择，不支持覆盖 | macOS |
| Claude Code、Codex、Gemini CLI、GitHub Copilot CLI、Qwen Code | 本地路径 | 从供应商 `/model` 菜单查看后手填 ID | 新增 macOS JSON 输出适配器 |
| OpenCode | 本地路径 | `opencode models`，输入 `provider/model` | 新增 macOS JSON 输出适配器 |
| OpenAI Chat Completions | 不要求 CLI | `/models`，也可手填 | macOS、Linux、WSL |
| OpenAI Responses | 不要求 CLI | `/models`，也可手填 | macOS、Linux、WSL |
| Anthropic Messages | 不要求 CLI | `/models`，也可手填 | macOS、Linux、WSL |

新增 CLI 适配器用于研究、审查和 JSON 提案，接收已筛选的文本输入；不是通用工程工具执行器。CLI 仍使用供应商自己的登录与套餐规则；本项目不会导出其登录凭证或把网页订阅转换成 API。新 CLI 的账号实测尚待用户执行；不同版本若改变输出格式会失败停止。Linux 上新增六种 CLI 暂未提供镜像，向导会明确拒绝；可使用标准 API 或已有三种 Docker CLI。原生 Windows 需 WSL。

OpenAI 兼容方式可配置支持该协议的 xAI、DeepSeek、Qwen、OpenRouter、Ollama、LM Studio 等服务；这表示协议适配，不表示逐个服务/模型都已验证。Anthropic 兼容网关也可自定义地址。Azure 的专用部署路径/认证、多模态、工具调用、流式响应不在本次适配范围。

```bash
./wq onboard --list
./wq providers list
./wq providers models devin
./wq providers select devin --model EXACT_MODEL_ID
./wq providers select claude   # 交互模式；模型菜单不能自动获取时明确提示
./wq onboard                 # 新用户：选择CLI/API、模型、角色、注册及登录
```

添加手动 API（已有初始化配置的用户）：

```bash
./wq providers add openai --name my-api --model EXACT_MODEL_ID \
  --base-url https://api.example.com/v1 --key-env MY_MODEL_API_KEY --save-key
./wq providers models my-api
./wq providers add anthropic --name my-review --model EXACT_MODEL_ID --save-key
./wq providers add openai-responses --name responses --model EXACT_MODEL_ID --save-key
```

`--save-key` 在交互终端隐藏输入，保存至 `private_dir/provider-keys/`，权限0600，禁止覆盖已有 Key。也可省略它，自己设置指定的环境变量。环境变量优先于文件；launchd/systemd 不一定继承终端变量，无人值守建议使用私有文件。Key 不进入 Prompt、JSON配置、命令参数或日志；配置仅保存 Key 引用。API Base URL 包含版本前缀（通常 `/v1`），不要填完整 `/chat/completions` 路径。只允许 HTTPS；本机回环地址和私有网段（10/8、172.16/12、192.168/16 等自建服务）可以 HTTP，link-local（169.254/16、fe80::/10，含云主机元数据地址）不允许；不跟随 HTTP 重定向。

模型列表失败不阻断手填。官方 OpenAI 默认发送 `max_completion_tokens`；兼容服务默认 `max_tokens`。可以在私有 `profiles.json` 的 `transport.token_parameter` 中调整这两个值，`max_tokens` 是输出上限。`allow_no_key: true` 仅用于用户明确配置的无需鉴权服务。API token 数来自响应；未提供账单价格时美元成本显示“未知”，不填零。

新渠道默认禁用，预算默认未知；添加、模型选择不会启动研究，也不会增加预算。按现有运行手册设置 `models.<name>.enabled`、预算和研究输入授权，再通过 `job enqueue`/`run-once` 做单轮验证。新增渠道使用路由队列；旧版 `invoke` 直连入口不支持新的 transport。将其分配给角色可用下面的高级流程。

### 官方免费 API 预设（无订阅起步）

没有 CLI 订阅或付费 API Key 时，可以用供应商官方提供的免费层起步：只需注册拿一个 Key，其余地址、模型、token 参数和额度重置规则由预设填好，全部走现有 OpenAI 兼容 transport。预设只收官方免费层 / 免费模型 / 新人额度，不包含网页版逆向、Cookie 转 API 或账号池。

```bash
./wq providers free                 # 全部预设；--region global 或 --region cn 过滤
./wq providers add-free bigmodel-free --save-key --roles review
./wq providers add-free gemini-free --save-key --roles research
./wq onboard --free gemini-free --free bigmodel-free   # 新用户初始化时直接选
```

| 预设 | 地区 | 默认模型 | 免费额度（2026-09-29 官方文档） | 注意 |
|---|---|---|---|---|
| `gemini-free` | 海外 | `gemini-3.8-flash` | RPM/TPM/RPD 在 AI Studio 查看，太平洋时间午夜重置 | 免费层内容用于改进产品；大陆、香港不可用 |
| `openrouter-free` | 海外 | `qwen/qwen3.8-27b:free` | 20 次/分钟；50 次/天（累计购买满 10 credits 后 1000 次/天），UTC 零点重置 | 免费模型会变动 |
| `zai-free` | 海外 | `glm-4.7-flash` | 标为免费，按并发限流 | 默认开启思考 |
| `mistral-free` | 海外 | `mistral-small-latest` | Free 档每月 10 美元额度 | 可在 Privacy 关闭训练 |
| `groq-free` | 海外 | `openai/gpt-oss-120b` | 约 30 RPM、1000 RPD、8K TPM | 只适合短输入 |
| `bigmodel-free` | 大陆 | `glm-4.7-flash` | 永久免费，只按并发限流 | 只有列出的 ID 免费 |
| `siliconflow-free` | 大陆 | `Qwen/Qwen3-8B` | 免费模型固定限流 | 须实名；小模型 |
| `spark-lite` | 大陆 | `lite` | 官方标注免费 | 输出 ≤4096，建议作审查兜底 |
| `modelscope-free` | 大陆 | `Qwen/Qwen3.5-35B-A3B` | 每天约 125–500 次（魔粒） | 须绑定已实名阿里云账号 |
| `bailian-trial` | 大陆 | `qwen3.7-flash` | 新人每模型 100 万 tokens / 90 天（限时） | **先在控制台打开“免费额度用完即停”** |

`add-free` 与菜单“添加自定义模型”一样把 Key 存到仓库外（0600）并启用该渠道；研究和审查请选不同服务（同一服务的两个名字算一个渠道，例如两个 OpenRouter 模型）。免费层数据政策、地区和额度由供应商决定并可能随时变化，免费 ≠ 无限、≠ 永久。GitHub Models（2026-07-30 退役）、腾讯 hunyuan-lite、百度 ERNIE Speed/Lite、智谱 glm-4.5-flash 已下线，不再列入。

### 额度暂停与自动恢复

推理适配器把供应商错误归成固定类别（`limit=quota|rate|auth`），正文不写入日志：

- **额度用尽**（日/月额度、赠额、余额，如 Gemini `PerDay`、OpenRouter `free-models-per-day`、智谱 1113/1308/1310、百炼 `AllocationQuota.FreeTierOnly`、讯飞 11201、HTTP 402）：不消耗重试次数，该渠道进入“额度暂停”。暂停到：供应商给的 `Retry-After`（秒数或 HTTP-date）/`x-ratelimit-reset*` → 预设的重置时刻（如 Gemini 太平洋时间午夜、OpenRouter UTC 零点）→ 都没有时每小时复查一次（`routing.quota_probe_s`）。
- 链上还有可用渠道就切换；全部暂停时任务留在队列里等最早的恢复时刻，到点自动继续。等待与被额度拒绝的调用都不计入任务尝试次数；授权期限到期仍按原规则停止。
- **短时限流**按 `Retry-After` 走原有 3 次重试；**认证失败**（401、未登录、需实名）直接停止，不重试。
- 旧 launcher 渠道（Grok/Devin/Cursor/ZCode）没有分类输出，仍按“3 次重试后判定容量故障”再进入额度暂停。
- 查看与手动解除：`./wq provider quota`、`./wq provider resume <渠道>`（例如已经充值）。菜单渠道状态显示“额度暂停至 …”，自动研究状态的下一次时间取最早恢复时刻。

### 研究与审查的“不同渠道”

按服务身份判断，不按名字：API 按服务主机（本机回环地址另含端口），CLI 按适配器种类（例如 `claude` 与 `claude-opus` 同为 Claude Code CLI），旧 launcher 按完整启动命令。路由排除提案渠道、补充审查、workflow 校验、初始化与菜单选择都用同一规则；solo 预设仍由用户显式允许同渠道。

### CLI 的环境变量

新增 CLI 适配器只继承运行所需的变量（PATH、HOME、语言、代理、证书、`XDG_*` 与各 CLI 自己的配置目录变量，如 `CODEX_HOME`、`CLAUDE_CONFIG_DIR`），不再把宿主上的 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`WQ_*_API_KEY` 等带进 CLI，避免 CLI 改走计费 API。确实需要环境变量认证时，在该渠道的 `transport.env_passthrough` 写变量名列表。更新源码后运行 `./wq providers refresh-runtime` 让部署的适配器生效。

### 高级流程 JSON

规范见 [JSON Schema](../schemas/workflow.schema.json)，例子见 [workflow.example.json](../config/workflow.example.json)。这是固定研究管线的声明式配置，不是任意 shell/DAG 编程语言。可编辑：

- 三个角色的渠道及备用顺序；实际调用保持模型固定与预算约束。
- 研究和审查的补充 Prompt（最多20000字符）；输出契约、DSL 和审查检查由程序保留。
- 是否在审查后进行模拟、是否收集诊断反馈、是否启用有限组合与组合数量上限。已有累计轮数和模拟预算继续生效。
- 必需顺序为 `research → review → simulate → feedback → pre_submit`；不得关闭审查或提交前验证。`pre_submit` 是验收声明，不会自动提交 Alpha。禁用模拟表示审查通过后归档；现有自动研究的登录/授权预检仍然需要满足。

不要直接修改已应用的版本文件；复制成草稿，再执行 apply。旧任务已冻结的 Prompt 和路由保留。

```bash
./wq workflow init --output config/workflow.draft.json
# 方式1：编辑上述JSON
# 方式2：按步骤编辑角色、Prompt文件、阶段和组合上限
./wq workflow edit config/workflow.draft.json --output config/workflow.edited.json
# 方式3：让已配置的engineering模型理解自然语言并生成草稿
./wq workflow ai-edit config/workflow.draft.json \
  --instruction '研究更强调可测量性，审查增加最强反例，关闭有限组合'
```

`ai-edit` 只创建一次正常计费/计额度的队列任务，不直接更改运行配置。现有 runner 调度执行，或者手动运行 `./wq run-once --lease 3600`。命令返回产物路径，完成后：

```bash
./wq workflow validate /PATH/TO/JOB/result.json
./wq workflow diff /PATH/TO/JOB/result.json
./wq workflow apply /PATH/TO/JOB/result.json
./wq workflow show
```

手动和交互草稿也使用同一套 `validate/diff/apply`。apply 会拒绝 UNKNOWN、排队/运行任务及未结束的研究轮次，并与 runner 共用锁；它不替用户暂停/取消实验。JSON 不提供执行任意命令、设置 Key 或修改平台阈值的字段。高级模式启用后路由以 workflow 为准；移除 `config.json` 中 `workflow` 字段即可恢复预设路由，仍应在空闲时操作。

### 核验范围

本次验证包括本地 HTTP 服务实测三种 API 请求/响应、真实子进程执行模拟 CLI、队列预算、用量、JSON 编辑与应用。没有在六个新增 CLI 的付费账号或外部 API 上执行推理；没有把协议测试记作供应商账号已验证。

## English

Provider installation, authentication, model listings and successful inference are separate states. Discovery does not start inference. Existing Grok/Devin/Cursor support macOS and Linux Docker; ZCode uses its macOS app model. New Claude Code, Codex, Gemini, Copilot, Qwen Code and OpenCode adapters currently support macOS and return JSON for research/review/drafting. They are not general engineering tool runners. Linux users can use the three existing Docker CLIs or standard APIs; native Windows uses WSL.

`wq onboard` detects installed CLIs and lets users choose exact model IDs and roles. Grok, Devin, Cursor and OpenCode can list models with their vendor command. Other CLIs explicitly request a manually entered ID from the vendor model picker. `providers list`, `providers models NAME` and `providers select NAME [--model ID]` remain available later. Listings never prove account entitlement. Existing CLI subscriptions keep their own login, billing and limits; this project does not convert subscription credentials into API keys.

`providers add openai|openai-responses|anthropic --name NAME --model ID [--base-url URL] [--key-env ENV_NAME] [--save-key]` adds a disabled API provider. Base URLs include the version prefix, usually `/v1`. Chat Completions, Responses and Messages are supported; compatible services may require different model IDs. Azure-specific authentication, tools, multimodal and streaming are outside this adapter. Official OpenAI uses `max_completion_tokens`; compatible Chat APIs default to `max_tokens`, configurable through `transport.token_parameter`.

`--save-key` prompts without echo and stores the key outside the repository with mode0600. The JSON holds only a file/environment reference. Environment variables take precedence; background schedulers may not inherit terminal environments. Key files are never overwritten. HTTPS is required except for loopback and private-network (10/8, 172.16/12, 192.168/16) self-hosted endpoints; link-local addresses (169.254/16, fe80::/10, including cloud metadata) are refused. Redirects are not followed. `allow_no_key: true` explicitly permits an unauthenticated endpoint. Reported tokens come from API responses; missing dollar billing is unknown, not zero.

### Free official API presets, quota pause and channel identity

`wq providers free [--region global|cn]` lists official free tiers compiled from vendor docs on 2026-09-29: `gemini-free`, `openrouter-free`, `zai-free`, `mistral-free`, `groq-free` (global) and `bigmodel-free`, `siliconflow-free`, `spark-lite`, `modelscope-free`, `bailian-trial` (mainland China; the last is a 90-day new-user trial — turn on "stop when free quota is used up" first). `wq providers add-free PRESET --save-key [--roles research]` or `wq onboard --free PRESET` needs only an API key; base URL, model, token parameter and reset rule come from the preset. No web-UI reverse engineering, cookie-to-API or account pools are included. Free is neither unlimited nor permanent; vendors decide data use, regions and limits.

The inference adapter reports a fixed class (`limit=quota|rate|auth`) without logging response bodies. Exhausted quota does not consume retries: the provider is paused until the vendor's `Retry-After` (seconds or HTTP-date) / `x-ratelimit-reset*`, else the preset reset time (e.g. Pacific midnight for Gemini, UTC midnight for OpenRouter), else an hourly probe (`routing.quota_probe_s`). Routing falls back to another available provider; when every remaining provider is paused the task waits in the queue until the earliest resume time and continues automatically, without spending task attempts. Short rate limits keep the three Retry-After retries; authentication failures stop immediately. Legacy launchers still reach the pause after three retries. `wq provider quota` lists pauses and `wq provider resume NAME` lifts one early.

"Different channel" for research and review is judged by service identity, not name: API host (plus port for loopback), CLI adapter kind, or the full launcher command. CLI adapters now inherit only basic variables and their own config-dir variables; host API keys are no longer passed through unless listed in `transport.env_passthrough`. Run `wq providers refresh-runtime` after updating source.

Use the commands above for three advanced editing paths: manually edit JSON, `workflow edit` for a bilingual step-by-step wizard, or `workflow ai-edit --instruction '...'` for a queued model proposal. AI editing uses the existing engineering route and normal budgets; it does not apply changes. Validate, inspect the diff and apply the returned `result.json` or your own draft. `workflow show` displays the active document. Apply waits for an idle/reconciled queue and no active research cycle, shares the runner lock, and preserves budgets and execution gates. It never cancels work for you.

The versioned [schema](../schemas/workflow.schema.json) allows role routes/fallback order, additional research/review prompts, simulation and diagnostic-feedback switches, and bounded combination settings. Required stage order, review, output contracts and pre-submission gates remain enforced. It is a declarative research pipeline, not an arbitrary shell/DAG engine. `pre_submit` does not automatically submit. Disabling simulations closes the cycle after review; existing account/authorization preflight still applies. Advanced routes override presets until the `workflow` field is removed from `config.json` while idle. Edit drafts instead of active version files.

New providers start disabled with unknown budgets. Configure model enablement, budget and approved input scope using the operations guide, then verify one routed `job enqueue`/`run-once` call. Legacy direct `invoke` does not support the new transports. CLI versions and account entitlements must be verified locally. This change was tested with local HTTP fixtures and subprocess CLI fixtures, not paid production inference.

## Official references / 官方接口依据

- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat)
- [OpenAI Responses](https://developers.openai.com/api/reference/resources/responses)
- [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create)
- [Claude Code CLI](https://code.claude.com/docs/en/cli-reference)
- [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
- [Gemini headless mode](https://geminicli.com/docs/cli/headless/)
- [GitHub Copilot programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference)
- [OpenCode models](https://opencode.ai/v2/docs/models)

Reviewed 2026-09-22. Local installed help was also checked for command flags; authentication/inference was not invoked.
