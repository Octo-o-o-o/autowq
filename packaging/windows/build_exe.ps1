# 构建 dist/WorldQuantTray.exe（Windows 单文件托盘，含引擎，离线可用）
# 用法：powershell -File packaging/windows/build_exe.ps1
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
  scripts/desktop_tray.py

if (-not (Test-Path dist/WorldQuantTray.exe)) { throw 'dist/WorldQuantTray.exe 未生成' }
Write-Host "==> dist/WorldQuantTray.exe"
Write-Host "运行：先在有 config 的工作区执行 wq onboard，再启动 exe（可用 --workspace <目录> 指定工作区）"
