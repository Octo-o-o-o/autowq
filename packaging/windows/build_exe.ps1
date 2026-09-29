# Build dist/WorldQuantTray.exe: one file that is the tray, the full wq CLI (--engine) and the scheduler entry.
# Native Windows supports API providers only; see docs/linux-windows.md.
# Usage: powershell -File packaging/windows/build_exe.ps1
# Keep this file ASCII: Windows PowerShell 5.1 reads BOM-less UTF-8 as the ANSI code page.
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ge 7) { $PSNativeCommandUseErrorActionPreference = $true }
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

python -m pip install --upgrade pyinstaller pystray pillow
python -m pip install --no-deps .
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name WorldQuantTray `
  --icon packaging/windows/WorldQuant.ico `
  --add-data "packaging/assets/tray-icon.png;." `
  --paths src `
  --collect-submodules wq `
  --add-data "src/wq/assets;wq/assets" `
  --add-data "src/wq/provider_runtime.py;wq" `
  --python-option "X utf8" `
  scripts/desktop_tray.py

if (-not (Test-Path dist/WorldQuantTray.exe)) { throw 'dist/WorldQuantTray.exe was not produced' }
Write-Host "==> dist/WorldQuantTray.exe"
Write-Host "Double-click: without a config it opens a console for the onboarding wizard (workspace ~/autowq, or --workspace <dir>)"
Write-Host "CLI: WorldQuantTray.exe --engine <wq args>, or --install-cli to create wq.cmd"
