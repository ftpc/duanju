# 一键打包 Windows 可分发版本（onedir + zip）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\build.ps1
# 版本号唯一来源：duanju/version.py 的 VERSION
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host '==> 1/5 读取版本号' -ForegroundColor Cyan
$version = (python -c "from duanju.version import VERSION; print(VERSION)" 2>$null | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($version)) { throw '无法从 duanju/version.py 读取 VERSION' }
$version = $version.Trim()
Write-Host "版本：$version"

Write-Host '==> 2/5 检查依赖' -ForegroundColor Cyan
python -c "import PySide6, requests; print('PySide6', PySide6.__version__)"
python -c "import PyInstaller; print('PyInstaller', PyInstaller.__version__)"

Write-Host '==> 3/5 生成图标' -ForegroundColor Cyan
python scripts\make_icon.py

Write-Host '==> 4/5 PyInstaller 打包' -ForegroundColor Cyan
python -m PyInstaller duanju.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 打包失败' }

$exe = Join-Path $root 'dist\DuanjuDownloader\DuanjuDownloader.exe'
if (!(Test-Path $exe)) { throw "未生成 exe：$exe" }

Write-Host '==> 5/5 冒烟验证' -ForegroundColor Cyan
$appVersion = (& $exe --version 2>$null | Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($appVersion)) {
    Write-Warning '未能从 exe 读取 --version 输出（windowed 程序 stdout 可能不可见），请以 exe 属性的版本资源为准'
} elseif ($appVersion -notmatch [regex]::Escape($version)) {
    throw "exe 自报版本（$appVersion）与 version.py（$version）不一致"
} else {
    Write-Host "exe 自报版本：$appVersion"
}
$smokeDir = Join-Path $root 'dist\smoke-shot'
$env:QT_QPA_PLATFORM = 'offscreen'
& $exe --smoke-shot $smokeDir
Remove-Item Env:QT_QPA_PLATFORM
$pngs = Get-ChildItem $smokeDir -Filter *.png -ErrorAction SilentlyContinue
if ($pngs.Count -lt 5) { throw "离屏截图数量不足（$($pngs.Count)），安装包可能有问题" }

$zip = Join-Path $root "dist\DuanjuDownloader-$version-win64.zip"
$src = Join-Path $root 'dist\DuanjuDownloader\*'
if (Test-Path $zip) { Remove-Item $zip -Force }
try {
    Compress-Archive -Path $src -DestinationPath $zip -CompressionLevel Optimal -ErrorAction Stop
} catch {
    # 刚打包完时杀软/索引器可能短暂占用产物文件，重试一次
    Write-Warning "首次压缩失败（$($_.Exception.Message)），2 秒后重试一次"
    Start-Sleep -Seconds 2
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path $src -DestinationPath $zip -CompressionLevel Optimal
}
$zipMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)

Write-Host "打包完成：$exe" -ForegroundColor Green
Write-Host "分发包：$zip（$zipMB MB）" -ForegroundColor Green
Write-Host "exe 属性里的版本号来自 duanju/version.py，本次为 $version" -ForegroundColor Green
Write-Host '提示：ffmpeg 默认从 PATH / 程序目录 / 设置里指定，不需要随包分发。' -ForegroundColor Yellow
