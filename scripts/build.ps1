# 一键打包 Windows 可分发版本（onedir）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\build.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host '==> 1/4 检查依赖' -ForegroundColor Cyan
python -c "import PySide6, requests; print('PySide6', PySide6.__version__)" 
python -c "import PyInstaller; print('PyInstaller', PyInstaller.__version__)"

Write-Host '==> 2/4 生成图标' -ForegroundColor Cyan
python scripts\make_icon.py

Write-Host '==> 3/4 PyInstaller 打包' -ForegroundColor Cyan
python -m PyInstaller duanju.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 打包失败' }

$exe = Join-Path $root 'dist\DuanjuDownloader\DuanjuDownloader.exe'
if (!(Test-Path $exe)) { throw "未生成 exe：$exe" }

Write-Host '==> 4/4 冒烟验证' -ForegroundColor Cyan
& $exe --version
$smokeDir = Join-Path $root 'dist\smoke-shot'
$env:QT_QPA_PLATFORM = 'offscreen'
& $exe --smoke-shot $smokeDir
Remove-Item Env:QT_QPA_PLATFORM
$pngs = Get-ChildItem $smokeDir -Filter *.png -ErrorAction SilentlyContinue
if ($pngs.Count -lt 5) { throw "离屏截图数量不足（$($pngs.Count)），安装包可能有问题" }
Write-Host "打包完成：$exe" -ForegroundColor Green
Write-Host "分发时压缩整个目录：$(Join-Path $root 'dist\DuanjuDownloader')" -ForegroundColor Green
Write-Host '提示：ffmpeg 默认从 PATH / 程序目录 / 设置里指定，不需要随包分发。' -ForegroundColor Yellow
