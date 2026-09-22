# autowq

English | [简体中文](README.md)

A local WorldQuant BRAIN research workflow: propose a hypothesis → review through a different provider → bounded simulation → diagnose real results → investigate complementary signals → validate before submission. You control models, budgets, evidence and the queue. A chat session does not need to stay online.

**A research tool, not a promise of income.** Passing a screen, an accepted submission, consultant eligibility and actual payment are separate states. Research automation does not automatically authorize submissions.

## First run

Requirements: Python 3.11+, Git, and model CLIs installed and authenticated using your own accounts. Runtime dependencies are Python standard library only. macOS is supported; Linux and Windows WSL2 use Docker-isolated providers. Native Windows is not supported.

```sh
git clone https://github.com/Octo-o-o-o/autowq.git
cd autowq
./wq onboard --lang en
./wq doctor --fix-private
./wq validate result fixtures/synthetic-result-pass.json
./wq import-results fixtures/synthetic-result-pass.json
./wq tasks
```

The wizard detects host executables and lets you select providers, model IDs, research/review/engineering roles and optional reasoning effort. It generates local configuration and isolated launchers. **It never overwrites an existing deployment, logs in, calls a model or installs a scheduler.** Finding an executable does not verify authentication or model entitlement.

Built-in adapters cover Grok Build, Devin and Cursor CLI, plus the ZCode app on macOS. One provider supports offline or single-provider work; continuous research requires two distinct providers. Another CLI requires an adapter, not just an arbitrary provider name. Most operational CLI output is currently Chinese; structured output is available for supported commands.

Follow the **[complete bilingual onboarding checklist](docs/onboarding.md)**: sign in → configure budgets and expiration → verify BRAIN access → verify field evidence → validate one real cycle → install scheduling. Models, API access, automatic research and submissions start disabled. Evidence templates deliberately remain unverified.

## Features

- Explicit model and role selection; each task freezes its routing configuration.
- Real simulation, GET polling and accounting; uncertain POST requests are never blindly repeated.
- Failure diagnosis, time-segment evidence, daily PnL correlations and bounded combination experiments.
- Per-candidate submission: research review, fresh platform checks, one POST and verified acceptance.
- Task progress and token/cost provenance; unknown costs never become zero.
- Single concurrency, budgets, authorization expiration, rate-limit handling, recovery and deduplication.

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
