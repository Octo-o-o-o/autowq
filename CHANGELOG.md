# Changelog

## 0.2.22 (local candidate; not publicly released)

- Enforce total-cycle and no-information discovery limits for every new lane, and serialize cumulative model-start reservations across workers.
- Wake discovery only when verified, consumed real observations change the input; preserve exhausted pre-upgrade episodes when no such observation exists.
- Retain consumed receipt identities and stop new discovery when receipt files disappear or change, without replenishing exhausted episodes.
- Allow registered campaigns to use their separate resource ledger without contaminating ordinary learning comparisons.
- Add exact, repeatable epoch migration commands that retain cumulative budgets and owner stops; display actionable framework errors and remaining dual-loop resources.
- Reject combinations whose historical field bindings/settings differ from current execution, and choose the first calibratable reference before freezing a contribution experiment.
- Keep pinned local engine snapshots separate from development edits, and identify source-only multi-lane features on the public 0.2.21 download pages.

## 0.2.21

- Menu-bar app UI refresh: shared design tokens (4pt spacing grid, five-level type scale, semantic badge colors), wrapping menu rows that no longer truncate long status text, and a card-based Research progress window rebuilt on Auto Layout with a header toolbar and elastic spacer.
- Language switches now rebuild every list submenu instead of potentially leaving stale titles; alert accessory views use fixed frames where NSStackView sizing was unreliable.
- macOS-only release; engine behavior unchanged.

## 0.2.20

- First-run wizard opens with a three-screen intro: how the loop turns idle subscription tokens (free APIs and self-hosted models too) into gated BRAIN research, the road from sign-up to Gold to the consultant track, and a starting-point choice — already-Gold/consultant users skip newcomer registration guidance (persisted as onboarding.user_stage; BRAIN login is still required because the loop runs under their account).
- The intro mirrors the website's amber terminal style, respects NO_COLOR and non-TTY output, can open worldquantbrain.com on a keypress, and is skipped by --non-interactive/--skip-intro or pre-answered with --stage.

## 0.2.19

- Added a bounded continuous-research loop on the existing task ledger: evidence gaps, registered collectors, late-observation reassessment, scoped paired findings, and versioned issue proposals.
- Separated ordinary learning allocation from campaign resources; retry contribution preparation only when its inputs change, and preserve successful frozen calibration.
- Added explicit epoch transitions that inherit remaining budgets and respect permanent owner stops, plus deterministic work selection and desktop progress diagnostics.
- Preserved paired-review, request identity, replay, authorization and reservation gates. Missing real-world measurement contracts still block execution; these changes do not establish improved Alpha returns or grant additional budget.

## 0.2.17

- Add versioned research lineage, scoped edit/counterexample retrieval, bounded plan selection, typed mutation suggestions and structural-family collision auditing.
- Add prospective budget-bound comparisons, evidence-bound rule ranking, explicit uncertainty/cost gaps, idle maintenance and automatic fallback. Existing execution and submission gates remain enforced.
- Freeze candidate pools and historical risk weights; evaluate only genuinely new post-freeze intervals with immutable PnL identities. Add measurement/NaN coverage audits, local gate counterfactuals and actual effort/cash reporting.
- Provide `research-learning` CLI and operating guide. No Alpha-quality or cash-return improvement is claimed by software validation.

## 0.2.16

- Chinese Windows opens the first-run wizard and tray in Chinese: the language is read from the Windows UI language, because `locale.getlocale()` there returns names like `Chinese (Simplified)_China` that were treated as English.
- The Windows release job attaches `WorldQuantTray.exe` again. Its smoke test read the version with a quoting that PowerShell does not parse, so the 0.2.15 job failed before upload.

## 0.2.15

- Refreshing the menu account line signs in again from the macOS Keychain when the BRAIN session has expired, then reloads level and score. Manual refresh, opening the app, 08:00 local time and a successful submission are still the only times it contacts BRAIN.
- macOS ships two notarized DMGs: `WorldQuant-<version>.dmg` bundles Python and works out of the box; `WorldQuant-<version>-light.dmg` is the small build and uses a local Python 3.11+ at first setup.
- Native Windows runs the full workflow from `WorldQuantTray.exe` alone, with API providers only (free presets, OpenAI/Anthropic protocols, custom endpoints); CLI providers are refused because native Windows has no sandbox. The exe now dispatches `--engine` (full CLI), `--console` (own console for the wizard and BRAIN login), `--provider-runtime` (API calls), `--scheduled-run` (Task Scheduler tick with logs), `--setup-windows [--no-tray|--remove]` and `--install-cli` (writes `wq.cmd`). Starting research from the tray registers the scheduler task itself; the first launch without a config opens the onboarding wizard. The build now bundles `wq/assets` data and runs in UTF-8 mode, and CI smoke-tests the packaged exe. `build_exe.ps1` is now ASCII so Windows PowerShell 5.1 can parse it. Verified on a real Windows machine: CLI, API calls, scheduler registration and runs, the tray, the first-run wizard and BRAIN login windows. Desktop testing found and fixed three console bugs: inherited invalid handles crashed the exe (WinError 6); output in an interactive cmd was lost because the PyInstaller onefile bootloader sits between the exe's Python process and cmd, so the exe now attaches to the first ancestor console; and `getpass` echoed passwords after stdin was rebound. An abandoned first-run wizard now shows a message instead of exiting silently.

## 0.2.14

- The submission standby list drops Alphas the platform has already accepted, and menu rows for submitted and standby Alphas lead with their cycle number so they line up with cycle history.
- Official free API presets for users without a subscription: `wq providers free`, `wq providers add-free PRESET --save-key`, `wq onboard --free PRESET`, and the menu's custom-model path accept ten presets (Gemini, OpenRouter `:free`, Z.ai, Mistral, Groq; BigModel, SiliconFlow, Spark Lite, ModelScope, Bailian trial). Limits and caveats were compiled from vendor docs on 2026-09-29.
- Quota pause and automatic resume: an exhausted quota no longer burns retries. The provider pauses until the vendor's `Retry-After`/reset header, the preset reset time, or an hourly probe; routing falls back to another provider, or the task waits in the queue and continues automatically at the earliest resume time without spending attempts. Authentication failures stop without retry. `wq provider quota` and `wq provider resume NAME` show and lift pauses; menu provider status and autopilot status show the resume time.
- Research and review must use different services, not just different names: aliases of one API host, one CLI adapter or one launcher command count as the same channel across routing, fallback review, workflow validation, onboarding and menu role selection.
- CLI adapters inherit only basic and config-dir environment variables; other API keys on the host are no longer passed to CLIs (opt in per provider with `transport.env_passthrough`). `Retry-After` accepts HTTP-date values. Plain HTTP stays allowed for loopback and private networks but no longer for link-local addresses such as cloud metadata; the provider guide now states the private-network rule.
- The macOS DMG now works on its own: it bundles pinned python-build-standalone 3.12 runtimes (arm64 and x86_64) with the engine preinstalled, so first-run setup no longer needs system Python ≥ 3.11 or Xcode Command Line Tools. Setup copies the runtime into `~/Library/Application Support/WorldQuant/` before building the venv, so replacing the app does not disturb a running scheduler. The app also ships `Contents/Resources/bin/wq` (full CLI) and `bin/wq-setup` (`activate --runner-only` registers the scheduler without the menu bar) for CLI-only and AI-driven use; see `docs/app-bundle.md`. The DMG grows from about 1 MB to about 56 MB.
- CLI provider hardening across all launcher channels (grok/devin/cursor/zcode): `wq providers refresh-runtime` now idempotently rebuilds `provider_entry.py`, the four launchers and `agents.sb`, closing the drift where the deployed runtime ran a stale entry script and hand-maintained sandbox profiles that source could not regenerate. The sandbox write-list allows `runtime/jobs`, the configured `routing.work_root` and `models.*.workdir`, and CLI state dirs, but never `launchers/`, the runtime itself or anything inside the project; `wq doctor` verifies each work dir with a real in-sandbox write probe. On Linux/WSL2 the command refreshes `docker_provider.py` instead of writing sandbox-exec launchers.
- Capacity-failure detection now recognizes `[1310] Weekly/Monthly Limit Exhausted` (real ZCode failure): retries-exhausted capacity errors fall through to the next provider and set `provider_not_before` instead of terminating the task.
- ZCode headless runs gain a `terminal_protocol` check on the `--json` result event (`projection.status == completed`), an updated `--disallowed-tools` list covering persistence/delegation tools (Cron*, OffPeak*, SendMessage, Skill, ReadSessionContext, workflow tools, AskUserQuestion), preflight existence checks for the app bundle and builtin provider config, a 200k prompt guard, and a verified-version pin (`onboarding.verified_versions.zcode`, enforced via `WQ_ZCODE_VERIFIED_VERSION`) that blocks dispatch before a model call when the app updates.
- CLI inference adapters: qwen's JSON array output is parsed via its final `result` message, non-`success` result subtypes fail the call, and codex token usage is recorded from `turn.completed`.
- `wq doctor` reports zcode prerequisites (node, CLI bundle, builtin provider config, version match) and `doctor --probe` version probes pass through launcher-based providers instead of crashing; `wq onboard --login-only` can drive `zcode login`.

## 0.2.13

- Reopening the menu while a task or cycle is already running only shows the menu. It does not replace the engine, resume, or stop that work.

## 0.2.12

- Each routing preset shows the research model and the review model on the row itself. The full fallback order stays in the tooltip.

## 0.2.11

- Opening the menu no longer aborts when the saved interface language differs from the system language. Menu rows are detached before the menu is rebuilt.

## 0.2.10

- The menu leads with the WorldQuant account name or email and level. Scheduling install is shown only when those login items are missing. Check for updates and the run log moved into Settings.
- Custom models are saved as templates. Settings → Models chooses the research model and the review model separately; those two slots must be different providers, and the choice applies from the next cycle.

## 0.2.9

- Add a self-hosted OpenAI-compatible or Anthropic-compatible model from onboarding, `wq providers add`, or the menu’s “Add a custom model…”. The key is stored outside the repository. HTTP is accepted only for loopback and private-network addresses.
- The settings row that looked like a research switch is the login-item installer. Once installed it shows that scheduling and login auto-start are already in place. The gray submission line now matches the queue: a passing Alpha is queued automatically while the submission queue is on and authorization is valid.

## 0.2.8

- Quitting the menu asks whether to close the menu only, stop after the current research cycle, or stop local tasks immediately. A simulation already sent to the platform is not withdrawn.
- If a new app cannot replace the bundled engine because a cycle still holds the scheduler lock, the same choices are offered at launch: open the menu and update later, stop after this cycle, or stop now and update. An explicit stop stays stopped until Start is pressed.

## 0.2.7

- The menu-bar icon is dimmed only while automatic research is paused. Opening the app starts research by default; Settings has “Start automatic research on launch” to turn that off.
- Start and Stop are one button. While a cycle is open, “Run next cycle now” becomes “Cancel current cycle”. A simulation already sent to the platform is not withdrawn. Successful controls update the menu instead of showing a dialog.
- Menu pause reasons and the update check follow the interface language. Check for Updates sends an app user agent so the site no longer answers 403. Settings shows the current language and can switch among Chinese, English and the system locale.

## 0.2.6

- The menu bar and tray list standby Alphas under submitted Alphas, with the same metrics, settings and expression detail. A standby Alpha is one that passed internal gates and is waiting for a 24-hour submission slot.
- Check for Updates compares the installed build with https://autowq.octoooo.com/version.json and opens the download page when a newer notarized DMG is published. It does not install anything by itself.

- Research funnel quality fixes grounded in the local ledger (57 cycles after submission `rKO9JAW9`, 0 submittable): generation prompt and `failure_playbook` no longer steer toward `group_rank(...,industry)` products under an already INDUSTRY-neutralized setting (31 product-form cycles since #90: median Sharpe 0.23, heavy sub-universe failures). Rank-sums of different data clusters and slow observables' long-window time-rank positions are the preferred forms; products require a stated conjunction mechanism.
- Split the single `decay8` setting variant into three mutually exclusive turnover tiers, each gated on base Sharpe ≥ 1.25 (rescue only official-Sharpe-passing near-misses): `decay2` for turnover 12.5%–29.99% (LLNzJGK9/XgbVwvEl class, fitness missed by 0.01), `decay8` for 30%–39.99%, `decay16` for ≥40% (88jVvlpX class, 60% turnover after decay8). `MAX_SETTING_VARIANTS` raised to 3; tiers stay preregistered and fully accounted.
- Final review rejections now record their failed checks and the candidate's own quoted sentences into `research_events`; the next research prompt receives them as hard constraints (repeat mapping errors should return `blocked` instead of reworded retries).
- Feedback adds a coarse `最近测试年收益风险比为负` label (no precise values) and revokes complementarity retention for candidates whose latest test segment is negative (9qjVXgN1/KPNgk2xk passed official checks but lost money in 2023).
- Combination parents now require Sharpe ≥ 1.2 (was 0.9), submitted-correlation < 0.5 (existing) and a non-negative test segment; near-miss healthy candidates are the preferred parents over fresh single-role probes. Combination plans that never produced a simulation no longer count toward parent exhaustion.
- The research prompt lists role sets of already accepted submissions, without scores or expressions, so new proposals do not rediscover those neighborhoods by changing a window.
- The menu bar and tray show that passing an Alpha does not auto-submit, and can arm or disarm `brain_submission.enabled`. Arming the queue still requires a per-candidate review before any POST.
- “Run next cycle” no longer fails while the scheduler is inside a model call. A click during an open cycle is kept and starts the following cycle as soon as the current one ends, skipping the usual wait. The menu shows the result of each action.
- The menu bar and tray can set a model spend cap in dollars (`limits.model_spend_cap_usd`). New model calls stop when known spend since the cap was set reaches it. Calls without a price are not counted as `$0`.
- An Alpha whose official checks all PASS is no longer blocked from submission by a weak or negative test segment. The test-segment result stays on the report, and a negative test year still cannot be a combination parent.
- Cycle history has a standby-submission state: an Alpha that passed internal gates but was not posted because the 24-hour submission cap is full. The scheduler submits it automatically once a slot opens, after a fresh official check. A previous official FAIL is not retried.
- Routing preset temporary overrides now cover a configurable number of new research cycles (1–100) instead of exactly one: `wq preset use NAME --once --cycles N`, a stepper in the macOS menu dialog, and 1/3/5/10-cycle entries in the Windows/Linux tray submenu. The remaining count is shown in panel headers and can be cancelled at any time (`wq preset cancel-once`); each new cycle claims one round and the permanent preset resumes automatically when the quota is used up.

## 0.2.5

- Add Chinese, English and system-language preferences shared by the CLI, macOS menu bar and Windows tray. Preserve raw ledger records and translate known messages only at display time.
- Show failed review checks, the original review rationale and follow-up responses in cycle history. Windows history now exposes details in submenus.
- Require candidate-bound evidence for new review rejections. Distinguish incorrect measurements from honest, falsifiable hypotheses awaiting exploratory screening.
- Allow one additional model invocation per research cycle for review rejection or recoverable model-output/execution errors. Preserve the original prompt constraints, task, artifacts and costs. The extra invocation has no nested retries; review reversals must address every original objection.
- Keep UNKNOWN reconciliation, authorization, budgets, candidate AST restrictions, platform request recovery and submission gates intact. Closed historical cycles are not reopened automatically.
- Fix menu-installer completion, the history-review command, Python 3.11 CLI compatibility, language-option parsing and package/CLI version consistency.
- Keep automatically built GitHub Release assets in draft until verification and final publication. The macOS release DMG is built and notarized separately before replacing the CI draft asset.

Windows tray support remains experimental. Passing software tests does not establish model review quality, strategy profitability or real-device acceptance.
