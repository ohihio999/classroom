<#
  collect.ps1 — AI 額度收集腳本（AI 額度儀表板後端）
  版號：v1.0.0
  版更記錄：
  - v1.0.0 (2026-07-12) 初版：抓 Claude OAuth 用量端點 + Codex session rate_limits，寫入 Firestore ai_usage/current

  資料來源：
  - Claude：https://api.anthropic.com/api/oauth/usage（token 讀本機 ~\.claude\.credentials.json，不外傳）
  - Codex：~\.codex\sessions 最新 rollout jsonl 內的 rate_limits 事件
  - Gemini：官方無額度查詢端點，前端只放連結，此腳本不處理

  排程：Windows 工作排程器每 5 分鐘執行一次（工作名稱 AI-Usage-Collector）
#>

$ErrorActionPreference = "Continue"
$logFile = Join-Path $PSScriptRoot "collect.log"

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

$result = [ordered]@{
    collectedAt = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
    claude      = $null
    codex       = $null
}

# ---------- Claude ----------
try {
    $cred = Get-Content "$env:USERPROFILE\.claude\.credentials.json" -Raw | ConvertFrom-Json
    $r = Invoke-RestMethod -Uri "https://api.anthropic.com/api/oauth/usage" -TimeoutSec 30 -Headers @{
        "Authorization"  = "Bearer $($cred.claudeAiOauth.accessToken)"
        "anthropic-beta" = "oauth-2025-04-20"
    }
    $claude = [ordered]@{
        status   = "ok"
        fiveHour = @{ percent = [math]::Round($r.five_hour.utilization, 1); resetsAt = $r.five_hour.resets_at }
        sevenDay = @{ percent = [math]::Round($r.seven_day.utilization, 1); resetsAt = $r.seven_day.resets_at }
    }
    $scoped = @($r.limits | Where-Object { $_.kind -eq "weekly_scoped" }) | Select-Object -First 1
    if ($scoped) {
        $claude.model = @{
            name     = "$($scoped.scope.model.display_name)"
            percent  = [math]::Round($scoped.percent, 1)
            resetsAt = $scoped.resets_at
        }
    }
    $result.claude = $claude
} catch {
    $result.claude = @{ status = "error"; error = $_.Exception.Message }
    Write-Log "Claude 失敗: $($_.Exception.Message)"
}

# ---------- Codex ----------
try {
    $rl = $null
    $asOf = $null
    $files = Get-ChildItem "$env:USERPROFILE\.codex\sessions" -Recurse -Filter "*.jsonl" |
        Sort-Object LastWriteTime -Descending | Select-Object -First 5
    foreach ($f in $files) {
        $hit = Select-String -Path $f.FullName -Pattern '"rate_limits"' | Select-Object -Last 1
        if ($hit) {
            $obj = $hit.Line | ConvertFrom-Json
            $rl = $obj.payload.rate_limits
            $asOf = if ($obj.timestamp) { $obj.timestamp } else { $f.LastWriteTime.ToString("yyyy-MM-ddTHH:mm:sszzz") }
            break
        }
    }
    if (-not $rl) { throw "sessions 中找不到 rate_limits 記錄" }
    function ConvertFrom-Unix($u) {
        if ($null -eq $u) { return $null }
        [DateTimeOffset]::FromUnixTimeSeconds([long]$u).ToLocalTime().ToString("yyyy-MM-ddTHH:mm:sszzz")
    }
    $result.codex = [ordered]@{
        status   = "ok"
        planType = "$($rl.plan_type)"
        asOf     = $asOf
        fiveHour = @{ percent = [math]::Round($rl.primary.used_percent, 1); resetsAt = ConvertFrom-Unix $rl.primary.resets_at }
        sevenDay = @{ percent = [math]::Round($rl.secondary.used_percent, 1); resetsAt = ConvertFrom-Unix $rl.secondary.resets_at }
    }
} catch {
    $result.codex = @{ status = "error"; error = $_.Exception.Message }
    Write-Log "Codex 失敗: $($_.Exception.Message)"
}

# ---------- 寫入 Firestore ----------
try {
    $dataJson = $result | ConvertTo-Json -Depth 8 -Compress
    $body = @{
        fields = @{
            data      = @{ stringValue = $dataJson }
            updatedAt = @{ stringValue = $result.collectedAt }
        }
    } | ConvertTo-Json -Depth 8
    $uri = "https://firestore.googleapis.com/v1/projects/my-teaching-tools-2b36c/databases/(default)/documents/ai_usage/current" +
           "?key=AIzaSyBf0sHTsndFCksNlh46G_2mw9rk5zmPdc0" +
           "&updateMask.fieldPaths=data&updateMask.fieldPaths=updatedAt"
    Invoke-RestMethod -Method Patch -Uri $uri -ContentType "application/json; charset=utf-8" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 30 | Out-Null
    Write-Log "OK claude=$($result.claude.status) codex=$($result.codex.status)"
} catch {
    Write-Log "Firestore 寫入失敗: $($_.Exception.Message)"
    exit 1
}

# ---------- log 瘦身（保留最後 500 行） ----------
try {
    $lines = Get-Content $logFile -ErrorAction SilentlyContinue
    if ($lines.Count -gt 500) { $lines | Select-Object -Last 500 | Set-Content $logFile -Encoding UTF8 }
} catch {}
