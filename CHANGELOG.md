# Changelog

## 0.2.5

- Add Chinese, English and system-language preferences shared by the CLI, macOS menu bar and Windows tray. Preserve raw ledger records and translate known messages only at display time.
- Show failed review checks, the original review rationale and follow-up responses in cycle history. Windows history now exposes details in submenus.
- Require candidate-bound evidence for new review rejections. Distinguish incorrect measurements from honest, falsifiable hypotheses awaiting exploratory screening.
- Allow one additional model invocation per research cycle for review rejection or recoverable model-output/execution errors. Preserve the original prompt constraints, task, artifacts and costs. The extra invocation has no nested retries; review reversals must address every original objection.
- Keep UNKNOWN reconciliation, authorization, budgets, candidate AST restrictions, platform request recovery and submission gates intact. Closed historical cycles are not reopened automatically.
- Fix menu-installer completion, the history-review command, Python 3.11 CLI compatibility, language-option parsing and package/CLI version consistency.
- Keep automatically built GitHub Release assets in draft until verification and final publication. The macOS release DMG is built and notarized separately before replacing the CI draft asset.

Windows tray support remains experimental. Passing software tests does not establish model review quality, strategy profitability or real-device acceptance.
