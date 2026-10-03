# macOS research workspace

The desktop uses AppKit's native toolbar and sidebar materials. On macOS 26 and later the OS supplies Liquid Glass; older supported systems retain their native appearance. Main content respects the split item's safe area, including the sidebar underlap introduced with the new design.

## Visual direction

A quiet research reader, with the selected experiment as the focal point. No decorative charts or invented performance numbers. Actual ledger content remains selectable and complete in the detail pane.

Reference palette (runtime uses adaptive semantic system colors, not fixed light colors): paper `#FFFFFF`, graphite `#1D1D1F`, secondary ink `#6E6E73`, separator `#D2D2D7`, system blue `#007AFF`, success green `#248A3D`. Failure and standby use native semantic red and orange, paired with text or symbols rather than color alone. Dark mode and accessibility contrast are system-managed.

SF system type: 27–28 pt bold detail/page headings, 19 pt sidebar heading, 13–14 pt body, 11–12 pt supporting labels. Numeric inputs use SF monospaced digits. Text is left-aligned; labels wrap and values stay selectable.

```
┌──────────────────────── Native toolbar ────────────────────────┐
│ Records / search     │ Status                                  │
│ Selected record      │ Research title                          │
│ Record + summary     │                                          │
│                      │ Label                                    │
│                      │ Full value or explanation                │
│ Record count         │                                          │
└──────────────────────┴──────────────────────────────────────────┘
┌──────────────────────── Search settings ───────────────────────┐
│ Settings categories  │ Page title / specific explanation        │
│                      │ Grouped native form                      │
│                      │ Label and help                 Control   │
│                      │                                          │
│                      │ Save state              Revert / Save    │
└──────────────────────┴──────────────────────────────────────────┘
```

Self-critique: repeated dashboard cards and large segmented search strips competed with research content. The replacement uses one reading surface and native window chrome. Only the selected settings category is backed; inactive categories remain quiet. Models explicitly state that selections save immediately, while typed settings retain their batch-save contract.

## Apple references

- [macOS current design and development](https://developer.apple.com/macos/whats-new/)
- [Build an AppKit app with the new design](https://developer.apple.com/videos/play/wwdc2025/310/)
- [Designing for macOS](https://developer.apple.com/design/human-interface-guidelines/designing-for-macos/)
- [Adopting Liquid Glass](https://developer.apple.com/documentation/technologyoverviews/adopting-liquid-glass)

Verification records and screenshots are kept separately from source and release evidence. A successful Swift build is not visual acceptance, and a signed local build is not a notarized public release.

## Verification — 2026-10-03

- Host: macOS 27.2, Xcode 27.0 / SDK 27.0. Both arm64 and x86_64 macOS 12 deployment targets compile. UI was visually exercised on the current Apple Silicon host; older macOS appearance was not exercised.
- Reviewed all eight settings categories and all four activity pages in light and dark appearance using a separate app built from the production view classes and local response snapshots. The preview does not launch the research scheduler or save operating settings.
- Corrected split-view safe-area overlap, inconsistent form-control alignment, narrow subtitle wrapping and fixed-appearance form borders after screenshot inspection.
- Checked search with no matches, advanced settings, English navigation length, Command-A editing, draft retention across category changes and revert-to-original behavior. English navigation used Chinese response snapshots; this does not claim a full English data-content acceptance run.
- Full local suite: 918 tests, all passed, including the explicitly enabled Docker isolation tests. Full and light DMGs mounted successfully, their application signatures and both architectures checked, and all 75 engine source/data files matched. First and repeated setup passed in separate temporary workspaces.
- After the owner accepted the Apple team agreement, both final app bundles and DMGs received Accepted notarization results. Stapler validation and code-signature checks passed. Anonymous downloads from the published GitHub release match the locally verified SHA-256 hashes. This host reports a Gatekeeper security-disabled override; no clean-machine Gatekeeper UX acceptance is claimed.
- Public release: [macos-v0.2.25](https://github.com/Octo-o-o-o/autowq/releases/tag/macos-v0.2.25). Full package: 57,103,129 bytes; light package: 1,680,134 bytes. SHA-256 checksums accompany the release.
