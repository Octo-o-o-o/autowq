# autowq

English | [简体中文](README.md)

A local WorldQuant BRAIN research workflow: propose a hypothesis → review through a different provider → bounded simulation → diagnose real results → investigate complementary signals → validate before submission. You control models, budgets, evidence and the queue. A chat session does not need to stay online.

**A research tool, not a promise of income.** Passing a screen, an accepted submission, consultant eligibility and actual payment are separate states. Research automation does not automatically authorize submissions.

## First run

Requirements: Python 3.11+, Git, and your own CLI subscription accounts or API keys. Runtime dependencies are Python standard library only. macOS is supported; Linux and Windows WSL2 use Docker-isolated providers. Native Windows is not supported.

```sh
git clone https://github.com/Octo-o-o-o/autowq.git
cd autowq
./wq onboard
./wq doctor --fix-private
./wq validate result fixtures/synthetic-result-pass.json
./wq import-results fixtures/synthetic-result-pass.json
./wq tasks
```

The wizard detects host executables and lets you select providers, model IDs, research/review/engineering roles and optional reasoning effort. It generates local configuration and isolated launchers. **It never overwrites an existing deployment, starts paid inference or installs a scheduler.** The default language is Chinese for Chinese locales and English otherwise. Choose another language at the first prompt or pass `--lang zh/en`. The wizard includes vendor CLI and BRAIN login, with the [official registration URL](https://platform.worldquantbrain.com/sign-up). You can skip and resume with `./wq onboard --login-only`. A successful login command does not verify model entitlement.

Adapters include Grok Build, Devin, Cursor, ZCode, plus Claude Code, Codex, Gemini CLI, GitHub Copilot CLI, Qwen Code and OpenCode. Standard OpenAI Chat Completions/Responses and Anthropic Messages APIs accept manual Base URL, key references and model IDs. See the **[bilingual provider/workflow guide](docs/providers-workflow.md)** for model discovery, platform support and verification limits. Continuous research still requires distinct review providers.

Advanced mode offers versioned JSON, a step-by-step CLI editor and natural-language AI draft editing. Configure role routes, additional prompts, simulation/feedback switches and bounded combinations, then use `workflow validate/diff/apply`. Existing budgets and submission gates stay enforced.

```bash
./wq providers list
./wq providers models devin
./wq workflow init --output config/workflow.draft.json
./wq workflow edit config/workflow.draft.json --output config/workflow.edited.json
```

Follow the **[complete bilingual onboarding checklist](docs/onboarding.md)**: sign in → configure budgets and expiration → verify BRAIN access → verify field evidence → validate one real cycle → install scheduling. Models, API access, automatic research and submissions start disabled. Evidence templates deliberately remain unverified.

## Features

- Explicit model and role selection; each task freezes its routing configuration.
- Real simulation, GET polling and accounting; uncertain POST requests are never blindly repeated.
- Failure diagnosis, time-segment evidence, daily PnL correlations and bounded combination experiments.
- Per-candidate submission: research review, fresh platform checks, one POST and verified acceptance.
- Task progress and token/cost provenance; unknown costs never become zero.
- Single concurrency, budgets, authorization expiration, rate-limit handling, recovery and deduplication.

## Common commands

```sh
./wq help
./wq help export --lang en
./wq --version
./wq login                         # BRAIN login, with registration URL
./wq onboard --login-only          # Resume account setup
./wq export --kind summary --output exports/summary.json
./wq export --kind results --format csv --output exports/results.csv
```

Exports support summary/tasks/results in JSON or CSV. Results exclude synthetic data by default. Task exports contain only explicitly selected status fields; cookies, passwords, task inputs and evidence paths are excluded. Existing files are never overwritten. Exports contain your local research information and stay local; `exports/` is ignored by Git.

## Inspect and stop

```sh
./wq onboard --list                 # Host CLI detection only; no writes
./wq preset show
./wq autopilot status
./wq autopilot feedback --json
./wq tasks
./wq autopilot stop                 # Stop creating new cycles
./wq pause --reason "manual pause" # Pause queue and local model calls
```

Expired logins, identity challenges, UNKNOWN requests and expired authorizations can require human intervention. Sleeping Macs and stopped servers do not run jobs. The project does not provide fully automatic research acceptance, cash trading, contract signing or guaranteed profitability.

## Development and documentation

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
```

- [Onboarding and model selection — bilingual](docs/onboarding.md)
- [Operations, configuration, submissions and recovery — Chinese](docs/operations.md)
- [Linux / Windows WSL2 deployment — Chinese](docs/linux-windows.md)
- [Architecture and data boundaries — Chinese](docs/architecture.md)
- [Third-party notices and licensing — Chinese](NOTICE.md)

Configuration, cookies, real Alphas, databases, model outputs, logs and personal research stay outside Git. Fixtures are synthetic and do not establish real research performance. A source backup is not a runtime-state backup.

This is not an official WorldQuant product. No open-source LICENSE has been selected yet; publishing source on GitHub does not itself grant an open-source license. Repository visibility and an eventual open-source release are separate maintainer decisions.

All-cycle review supports manual and automatic runs: `./wq auto-research report` and `./wq auto-research run`. See [Auto Research](docs/auto-research.md) for cadence and independent review. Accepted fixed guidance enters future prompts without changing budgets or quality gates.
