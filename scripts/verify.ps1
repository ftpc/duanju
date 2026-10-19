# 离线验证：单元测试 + 下载/合并冒烟 + 界面离屏截图
# 用法：powershell -ExecutionPolicy Bypass -File scripts\verify.ps1 [-Network]
param([switch]$Network)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host '==> 单元测试' -ForegroundColor Cyan
python -m unittest discover -s tests -t . -v
if ($LASTEXITCODE -ne 0) { throw '单元测试失败' }

Write-Host '==> 下载 / 合并冒烟' -ForegroundColor Cyan
python scripts\smoke_download.py
if ($LASTEXITCODE -ne 0) { throw '下载/合并冒烟失败' }

Write-Host '==> 端到端冒烟（本地假站 + 队列 + 合并）' -ForegroundColor Cyan
python scripts\smoke_e2e.py
if ($LASTEXITCODE -ne 0) { throw '端到端冒烟失败' }

Write-Host '==> 播放器冒烟' -ForegroundColor Cyan
python scripts\smoke_player.py
if ($LASTEXITCODE -ne 0) { throw '播放器冒烟失败' }

Write-Host '==> 界面离屏截图' -ForegroundColor Cyan
$env:QT_QPA_PLATFORM = 'offscreen'
python app.py --smoke-shot .smoke\shots
Remove-Item Env:QT_QPA_PLATFORM
if (!(Test-Path '.smoke\shots\00-main.png')) { throw '界面截图失败' }

if ($Network) {
    Write-Host '==> 站源网络冒烟' -ForegroundColor Cyan
    python scripts\smoke_net.py
    if ($LASTEXITCODE -ne 0) { throw '网络冒烟失败' }
}

Write-Host '全部验证通过 [OK]' -ForegroundColor Green
exit 0
