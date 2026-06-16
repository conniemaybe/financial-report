# ============================================================
# push-without-proxy.ps1 — 禁用 Windows 系统代理后立即推送（同一进程内完成）
# 核心原理：Clash 每隔几秒重写 ProxyEnable=1，
# Windows 版 git-remote-https.exe 通过 WinINET API 读取该值走代理。
# 唯一可靠方案：在同一 PowerShell 进程内修改注册表后立即调用 git push，
# 利用进程级 WinINET 缓存刷新机制，抢在 Clash 改回之前完成推送。
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File push-without-proxy.ps1 "commit message"
# ============================================================

param(
    [string]$CommitMsg = "auto: sync to GitHub Pages",
    [string]$RepoDir = "C:\temp\financial-report"
)

$ErrorActionPreference = "Continue"
Set-Location $RepoDir

# 1. 清除 git 全局代理
git config --global --unset http.proxy 2>$null
git config --global --unset https.proxy 2>$null

# 2. 清除环境变量代理
$env:HTTP_PROXY = $null
$env:HTTPS_PROXY = $null
$env:http_proxy = $null
$env:https_proxy = $null
$env:ALL_PROXY = $null
$env:NO_PROXY = "*"

# 3. 禁用 Windows 系统代理（Clash 写入的 ProxyEnable=1）
$regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
Set-ItemProperty -Path $regPath -Name ProxyEnable -Value 0

# 4. 清除当前进程级 git 配置的代理（双保险）
git -c http.proxy= -c https.proxy= config --get http.proxy 2>$null | Out-Null

# 5. 立即推送（零延迟，抢在 Clash 重写 ProxyEnable 之前）
$maxRetries = 5
$success = $false
for ($i = 1; $i -le $maxRetries; $i++) {
    # 每次重试前重新禁用系统代理（Clash 可能在上一轮改回来了）
    Set-ItemProperty -Path $regPath -Name ProxyEnable -Value 0

    # 立即 push，不 sleep
    $pushOutput = git -c http.proxy= -c https.proxy= push origin main 2>&1
    $pushExit = $LASTEXITCODE

    if ($pushExit -eq 0) {
        Write-Host "PUSH_SUCCESS attempt=$i" -ForegroundColor Green
        Write-Host $pushOutput
        $success = $true
        break
    } else {
        Write-Host "ATTEMPT $i FAILED: $pushOutput" -ForegroundColor Yellow
    }
}

if (-not $success) {
    Write-Host "PUSH_FAILED after $maxRetries attempts" -ForegroundColor Red
    exit 1
}
