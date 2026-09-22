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

`--save-key` 在交互终端隐藏输入，保存至 `private_dir/provider-keys/`，权限0600，禁止覆盖已有 Key。也可省略它，自己设置指定的环境变量。环境变量优先于文件；launchd/systemd 不一定继承终端变量，无人值守建议使用私有文件。Key 不进入 Prompt、JSON配置、命令参数或日志；配置仅保存 Key 引用。API Base URL 包含版本前缀（通常 `/v1`），不要填完整 `/chat/completions` 路径。只允许 HTTPS，localhost 可以 HTTP；不跟随 HTTP 重定向。

模型列表失败不阻断手填。官方 OpenAI 默认发送 `max_completion_tokens`；兼容服务默认 `max_tokens`。可以在私有 `profiles.json` 的 `transport.token_parameter` 中调整这两个值，`max_tokens` 是输出上限。`allow_no_key: true` 仅用于用户明确配置的无需鉴权服务。API token 数来自响应；未提供账单价格时美元成本显示“未知”，不填零。

新渠道默认禁用，预算默认未知；添加、模型选择不会启动研究，也不会增加预算。按现有运行手册设置 `models.<name>.enabled`、预算和研究输入授权，再通过 `job enqueue`/`run-once` 做单轮验证。新增渠道使用路由队列；旧版 `invoke` 直连入口不支持新的 transport。将其分配给角色可用下面的高级流程。

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

`--save-key` prompts without echo and stores the key outside the repository with mode0600. The JSON holds only a file/environment reference. Environment variables take precedence; background schedulers may not inherit terminal environments. Key files are never overwritten. HTTPS is required except for loopback. Redirects are not followed. `allow_no_key: true` explicitly permits an unauthenticated endpoint. Reported tokens come from API responses; missing dollar billing is unknown, not zero.

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
