<#
  collect.ps1 — AI 額度收集腳本（AI 額度儀表板後端）
  版號：v2.1.0
  版更記錄：
  - v2.1.0 (2026-07-29) 接入 Antigravity 本機 Language Server 的
    `RetrieveUserQuotaSummary`：只掃描 `language_server`／`agy` 程序，只連
    127.0.0.1，解析 Session、Weekly、Claude、Claude Weekly 四個真實額度池。
    CSRF token 僅保留於記憶體，不寫入 log、Firestore、檔案或命令列；服務不可用時
    只回報不含憑證內容的錯誤分類。
  - v2.0.0 (2026-07-29) 每輪除更新 `ai_usage/current`，另新增不可覆寫的
    `ai_usage/sample_yyyyMMddTHHmmssfffZ` 歷史樣本，供前端繪製 6 小時／24 小時／7 天趨勢。
    新增 agy 狀態欄位；目前 agy CLI 沒有 quota 指令或可靠的本機額度來源，因此明確回報 unavailable，
    不把同 Google 帳號的 Gemini CLI retrieveUserQuota 冒充 Antigravity 專屬額度。
  - v1.4.0 (2026-07-28) Codex 改成即時查詢，不必等使用者實際跑過 Codex：走 Codex CLI 官方 app-server
    JSON-RPC `account/rateLimits/read`（token 由 Codex CLI 自己換發，本腳本不碰 refresh token、
    公開 repo 也不必放 OAuth client）。查不到才回退舊的 session jsonl 解析，並在 `source` 欄位標明
    數據來源（api／session），前端據此決定要不要顯示「取自最近一次 Codex 使用」
  - v1.3.0 (2026-07-14) Gemini 去 npm 依賴：OAuth client 常數改讀 repo 外私有設定檔
    `~\.ai-usage-collector\gemini-oauth-client.json`；設定檔不存在時才回退抽 npm bundle 並自動補寫，
    之後即使移除 npm @google/gemini-cli 套件也不會斷（額度端點仍用舊 retrieveUserQuota，token 仍讀 ~\.gemini\oauth_creds.json）
  - v1.2.0 (2026-07-14) 新增 Gemini 額度：走 Gemini CLI 同款 retrieveUserQuota API（refresh token → loadCodeAssist → retrieveUserQuota），逐模型剩餘比例
  - v1.1.0 (2026-07-13) Codex 0.134 改版適配：rate_limits 不再固定 primary=5h/secondary=週，改用 window_minutes 判斷窗口；缺的窗口寫 null
  - v1.0.0 (2026-07-12) 初版：抓 Claude OAuth 用量端點 + Codex session rate_limits，寫入 Firestore ai_usage/current

  資料來源：
  - Claude：https://api.anthropic.com/api/oauth/usage（token 讀本機 ~\.claude\.credentials.json，不外傳）
  - Codex：codex.exe app-server 的 JSON-RPC `account/rateLimits/read`（即時、不耗額度；
    憑證與換發完全由 Codex CLI 自己處理）。失敗時回退 ~\.codex\sessions 最新 rollout jsonl 內的
    rate_limits 事件（該事件只有實際送出 Codex 請求時才寫入，所以回退值可能過期）
  - Gemini：cloudcode-pa.googleapis.com v1internal:retrieveUserQuota（token 讀本機 ~\.gemini\oauth_creds.json 換發，不外傳；
    OAuth client 為 Gemini CLI 內建的公開 installed-app client，v1.3.0 起改讀 ~\.ai-usage-collector\gemini-oauth-client.json，
    首次或設定檔遺失時回退抽 npm bundle 補寫；額度用完時 API 會省略 remainingFraction，視為 100% 用完）

  - agy／Antigravity：掃描本機 `language_server`／`agy` 程序及其 loopback
    監聽埠，呼叫 `RetrieveUserQuotaSummary`。HTTPS 自簽憑證只允許在硬編碼
    127.0.0.1 端點略過驗證；不向遠端主機傳送 CSRF token。

  排程：Windows 工作排程器每 5 分鐘執行一次（工作名稱 AI-Usage-Collector）
#>

$ErrorActionPreference = "Continue"
$logFile = Join-Path $PSScriptRoot "collect.log"

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

$result = [ordered]@{
    schemaVersion = 2
    collectedAt = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
    claude      = $null
    codex       = $null
    gemini      = $null
    agy         = $null
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
function ConvertFrom-Unix($u) {
    if ($null -eq $u) { return $null }
    [DateTimeOffset]::FromUnixTimeSeconds([long]$u).ToLocalTime().ToString("yyyy-MM-ddTHH:mm:sszzz")
}

# 找 codex 執行檔：優先用 npm 套件內的原生 exe（app-server 走 stdio，直接跑 exe 最穩），
# 找不到才退回 PATH 上的 codex shim。
function Get-CodexExe {
    $globs = @(
        "$env:APPDATA\npm\node_modules\@openai\codex\node_modules\@openai\codex-*\vendor\*\bin\codex.exe"
        "$env:LOCALAPPDATA\npm\node_modules\@openai\codex\node_modules\@openai\codex-*\vendor\*\bin\codex.exe"
    )
    foreach ($g in $globs) {
        $hit = Get-ChildItem $g -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    $cmd = Get-Command codex -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

# 即時查 Codex 額度：codex app-server 的 JSON-RPC `account/rateLimits/read`。
# 不耗額度，也不需要使用者先跑過 Codex；access token 過期由 Codex CLI 自己換發。
function Get-CodexRateLimitsLive {
    $exe = Get-CodexExe
    if (-not $exe) { throw "找不到 codex 執行檔" }
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName               = $exe
    $psi.Arguments              = "app-server"
    $psi.RedirectStandardInput  = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true
    $p = [Diagnostics.Process]::Start($psi)
    try {
        $p.StandardInput.WriteLine('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"ai-usage-collector","title":"AI Usage Collector","version":"1.4.0"}}}')
        $p.StandardInput.Flush()
        Start-Sleep -Milliseconds 1200
        $p.StandardInput.WriteLine('{"jsonrpc":"2.0","method":"initialized","params":{}}')
        $p.StandardInput.WriteLine('{"jsonrpc":"2.0","id":2,"method":"account/rateLimits/read","params":{}}')
        $p.StandardInput.Flush()
        # app-server 會夾雜推播通知（例：remoteControl/status/changed），只挑 id=2 的回應
        $deadline = (Get-Date).AddSeconds(45)
        while ((Get-Date) -lt $deadline) {
            $t = $p.StandardOutput.ReadLineAsync()
            $remain = [int][math]::Max(1000, ($deadline - (Get-Date)).TotalMilliseconds)
            if (-not $t.Wait($remain)) { throw "app-server 讀取逾時" }
            $line = $t.Result
            if ($null -eq $line) { throw "app-server 提早關閉輸出" }
            if ($line -notmatch '"id"\s*:\s*2\b') { continue }
            $msg = $line | ConvertFrom-Json
            if ($msg.error) { throw "app-server 回錯誤：$($msg.error.message)" }
            if ($msg.result.rateLimits) { return $msg.result.rateLimits }
            throw "app-server 回應中沒有 rateLimits"
        }
        throw "app-server 等待逾時"
    } finally {
        try { if (-not $p.HasExited) { $p.Kill() } } catch {}
        try { $p.Dispose() } catch {}
    }
}

try {
    # 統一成 used_percent / window_minutes / resets_at(unix) 三欄，兩種來源共用後面的分類邏輯
    $wins = @()
    $planType = $null
    $asOf = $null
    $source = $null

    try {
        $live = Get-CodexRateLimitsLive
        foreach ($w in @($live.primary, $live.secondary)) {
            if ($w -and $null -ne $w.usedPercent) {
                $wins += [pscustomobject]@{
                    used_percent   = $w.usedPercent
                    window_minutes = $w.windowDurationMins
                    resets_at      = $w.resetsAt
                }
            }
        }
        if (-not $wins.Count) { throw "app-server 沒回任何窗口數據" }
        $planType = "$($live.planType)"
        $asOf     = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
        $source   = "api"
    } catch {
        Write-Log "Codex 即時查詢失敗，回退 session 檔：$($_.Exception.Message)"
        # 回退：翻最近的 session jsonl。該事件只有實際送出 Codex 請求時才寫入，數字可能已過期。
        $rl = $null
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
        if (-not $rl) { throw "即時查詢失敗，且 sessions 中也找不到 rate_limits 記錄" }
        $wins = @($rl.primary, $rl.secondary) | Where-Object { $_ -and $null -ne $_.used_percent }
        $planType = "$($rl.plan_type)"
        $source = "session"
    }

    # Codex 0.134 起 rate_limits 可能只剩單一窗口（週窗口在 primary、secondary=null），
    # 不能假設 primary=5h/secondary=週，改用窗口長度分類（<=1440 分鐘視為 5 小時窗口）
    $five = $wins | Where-Object { $null -ne $_.window_minutes -and $_.window_minutes -le 1440 } | Select-Object -First 1
    $week = $wins | Where-Object { $null -ne $_.window_minutes -and $_.window_minutes -gt 1440 } | Select-Object -First 1
    if (-not $five -and -not $week -and $wins.Count -gt 0) {
        # 舊格式保險：沒有 window_minutes 就照位置解讀
        $five = $wins[0]
        if ($wins.Count -gt 1) { $week = $wins[1] }
    }
    $result.codex = [ordered]@{
        status   = "ok"
        planType = $planType
        asOf     = $asOf
        source   = $source
        fiveHour = if ($five) { @{ percent = [math]::Round($five.used_percent, 1); resetsAt = ConvertFrom-Unix $five.resets_at } } else { $null }
        sevenDay = if ($week) { @{ percent = [math]::Round($week.used_percent, 1); resetsAt = ConvertFrom-Unix $week.resets_at } } else { $null }
    }
} catch {
    $result.codex = @{ status = "error"; error = $_.Exception.Message }
    Write-Log "Codex 失敗: $($_.Exception.Message)"
}

# ---------- Gemini ----------
try {
    # OAuth client（公開 installed-app client，repo 公開不可硬編碼）：優先讀 repo 外私有設定檔；
    # 設定檔遺失時才回退抽 npm bundle 並自動補寫，之後即使移除 npm gemini 套件也不會斷。
    $gClientId = $null; $gClientSecret = $null
    $gCfgFile = Join-Path $env:USERPROFILE ".ai-usage-collector\gemini-oauth-client.json"
    if (Test-Path $gCfgFile) {
        $gc = Get-Content $gCfgFile -Raw | ConvertFrom-Json
        $gClientId = $gc.clientId; $gClientSecret = $gc.clientSecret
    }
    if (-not $gClientId -or -not $gClientSecret) {
        # 回退：從 npm bundle 抽取一次並補寫私有設定檔
        foreach ($bf in Get-ChildItem "$env:APPDATA\npm\node_modules\@google\gemini-cli\bundle\*.js" -ErrorAction SilentlyContinue) {
            if (-not $gClientId) {
                $m = Select-String -Path $bf.FullName -Pattern 'OAUTH_CLIENT_ID\s*=\s*"([^"]+)"' | Select-Object -First 1
                if ($m) { $gClientId = $m.Matches[0].Groups[1].Value }
            }
            if (-not $gClientSecret) {
                $m = Select-String -Path $bf.FullName -Pattern 'OAUTH_CLIENT_SECRET\s*=\s*"([^"]+)"' | Select-Object -First 1
                if ($m) { $gClientSecret = $m.Matches[0].Groups[1].Value }
            }
            if ($gClientId -and $gClientSecret) { break }
        }
        if ($gClientId -and $gClientSecret) {
            $gCfgDir = Split-Path $gCfgFile -Parent
            if (-not (Test-Path $gCfgDir)) { New-Item -ItemType Directory -Path $gCfgDir | Out-Null }
            @{ clientId = $gClientId; clientSecret = $gClientSecret; extractedAt = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz"); source = "npm @google/gemini-cli bundle (auto-backfill)" } |
                ConvertTo-Json | Set-Content -Path $gCfgFile -Encoding UTF8
            Write-Log "Gemini OAuth client 已從 npm bundle 補寫私有設定檔"
        }
    }
    if (-not $gClientId -or -not $gClientSecret) { throw "找不到 Gemini OAuth client（私有設定檔 $gCfgFile 不存在，且 npm bundle 也抽不到）" }
    $gCred = Get-Content "$env:USERPROFILE\.gemini\oauth_creds.json" -Raw | ConvertFrom-Json
    $gTok = Invoke-RestMethod -Method Post -Uri "https://oauth2.googleapis.com/token" -TimeoutSec 30 -Body @{
        client_id     = $gClientId
        client_secret = $gClientSecret
        refresh_token = $gCred.refresh_token
        grant_type    = "refresh_token"
    }
    $gHead = @{ Authorization = "Bearer $($gTok.access_token)" }
    $gLoad = Invoke-RestMethod -Method Post -Uri "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist" `
        -Headers $gHead -ContentType "application/json" -TimeoutSec 30 -Body '{"metadata":{"pluginType":"GEMINI"}}'
    $gQuota = Invoke-RestMethod -Method Post -Uri "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota" `
        -Headers $gHead -ContentType "application/json" -TimeoutSec 30 `
        -Body (@{ project = $gLoad.cloudaicompanionProject } | ConvertTo-Json)
    if (-not $gQuota.buckets) { throw "retrieveUserQuota 沒回 buckets" }
    $models = @()
    foreach ($b in $gQuota.buckets) {
        if (-not $b.modelId) { continue }
        # 額度用完時 API 會省略 remainingFraction（proto 省略零值），視為 100% 用完
        $frac = if ($null -ne $b.remainingFraction) { [double]$b.remainingFraction } else { 0 }
        # ConvertFrom-Json 會把 ISO 字串轉成 DateTime，這裡統一輸出成 UTC ISO 格式（前端才不會差 8 小時）
        $reset = if ($b.resetTime -is [datetime]) { $b.resetTime.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'") } else { "$($b.resetTime)" }
        $models += [ordered]@{
            name     = "$($b.modelId)"
            percent  = [math]::Round((1 - $frac) * 100, 1)
            resetsAt = $reset
        }
    }
    $result.gemini = [ordered]@{
        status = "ok"
        tier   = "$($gLoad.currentTier.id)"
        models = $models
    }
} catch {
    $result.gemini = @{ status = "error"; error = $_.Exception.Message }
    Write-Log "Gemini 失敗: $($_.Exception.Message)"
}

# ---------- agy／Antigravity ----------
function Get-ProcessFlagValue([string]$commandLine, [string]$flag) {
    if (-not $commandLine -or -not $flag) { return $null }
    $escaped = [regex]::Escape($flag)
    $match = [regex]::Match($commandLine, "(?:^|\s)$escaped(?:=|\s+)(?:`"([^`"]+)`"|(\S+))")
    if (-not $match.Success) { return $null }
    if ($match.Groups[1].Success) { return $match.Groups[1].Value }
    return $match.Groups[2].Value
}

function Get-AntigravityLanguageServers {
    $processes = @(
        Get-Process -Name "language_server", "agy" -ErrorAction SilentlyContinue |
            Sort-Object @{ Expression = { if ($_.ProcessName -eq "language_server") { 0 } else { 1 } } }, Id
    )

    foreach ($process in $processes) {
        $details = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.Id)" -ErrorAction SilentlyContinue
        if (-not $details) { continue }
        $commandLine = [string]$details.CommandLine
        $isLanguageServer = $process.ProcessName -eq "language_server"
        if ($isLanguageServer) {
            $isAntigravity = ([string]$details.ExecutablePath -match "(?i)antigravity") -or
                ($commandLine -match "(?i)(?:--(?:app_data_dir|ide_name|override_ide_name)(?:=|\s+))antigravity(?:-ide)?(?:\s|$)")
            if (-not $isAntigravity) { continue }
        }

        $csrf = if ($isLanguageServer) { Get-ProcessFlagValue $commandLine "--csrf_token" } else { "" }
        if ($isLanguageServer -and -not $csrf) { continue }

        $ports = @(
            Get-NetTCPConnection -State Listen -OwningProcess $process.Id -ErrorAction SilentlyContinue |
                Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1", "0.0.0.0", "::") } |
                Select-Object -ExpandProperty LocalPort -Unique |
                Sort-Object
        )
        $extensionPortText = Get-ProcessFlagValue $commandLine "--extension_server_port"
        $extensionPort = if ($extensionPortText -match "^\d+$") { [int]$extensionPortText } else { $null }
        if (-not $ports -and -not $extensionPort) { continue }

        [pscustomobject]@{
            ProcessName  = $process.ProcessName
            Csrf          = $csrf
            Ports         = $ports
            ExtensionPort = $extensionPort
        }
    }
}

function ConvertTo-AntigravityMeter($bucket, [string]$source, [string]$collectedAt) {
    if ($null -eq $bucket.remainingFraction) { return $null }
    $remainingRaw = [double]$bucket.remainingFraction
    if ([double]::IsNaN($remainingRaw) -or [double]::IsInfinity($remainingRaw)) { return $null }
    $remaining = [math]::Max([double]0, [math]::Min([double]1, $remainingRaw))
    $resetTime = if ($bucket.resetTime -is [datetime]) {
        $bucket.resetTime.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss'Z'")
    } elseif ($bucket.resetTime) {
        "$($bucket.resetTime)"
    } else {
        $null
    }
    return [ordered]@{
        remainingFraction = [math]::Round($remaining, 7)
        usedPercent       = [math]::Round((1 - $remaining) * 100, 1)
        resetTime         = $resetTime
        source            = $source
        collectedAt       = $collectedAt
    }
}

function Get-AntigravityQuota {
    $collectedAt = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
    $servers = @(Get-AntigravityLanguageServers)
    if (-not $servers) {
        return [ordered]@{
            status = "unavailable"; source = "antigravity-language-server"; checkedAt = $collectedAt
            errorClass = "local_service_not_found"; reason = "找不到本機 Antigravity / agy 額度服務"
            action = "請啟動 Antigravity 或 agy 後等待下一輪更新"
        }
    }

    $path = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
    $body = @{ metadata = @{
        ideName = "antigravity"; extensionName = "antigravity"; ideVersion = "unknown"; locale = "en"
    } } | ConvertTo-Json -Compress

    foreach ($server in $servers) {
        $endpoints = @()
        foreach ($port in $server.Ports) {
            $endpoints += [pscustomobject]@{ Scheme = "https"; Port = [int]$port }
            $endpoints += [pscustomobject]@{ Scheme = "http"; Port = [int]$port }
        }
        if ($server.ExtensionPort -and $server.ExtensionPort -notin $server.Ports) {
            $endpoints += [pscustomobject]@{ Scheme = "http"; Port = [int]$server.ExtensionPort }
        }

        foreach ($endpoint in $endpoints) {
            try {
                # Host 固定為 127.0.0.1；CSRF token 只存在此程序記憶體與 HTTP header。
                $params = @{
                    Method = "Post"
                    Uri = "$($endpoint.Scheme)://127.0.0.1:$($endpoint.Port)$path"
                    Headers = @{
                        "Connect-Protocol-Version" = "1"
                        "x-codeium-csrf-token" = $server.Csrf
                    }
                    ContentType = "application/json"
                    Body = $body
                    TimeoutSec = 10
                    ErrorAction = "Stop"
                }
                if ($endpoint.Scheme -eq "https") { $params.SkipCertificateCheck = $true }
                $response = Invoke-WebRequest @params
                $payload = $response.Content | ConvertFrom-Json
                $groups = if ($payload.response) { @($payload.response.groups) } else { @($payload.groups) }
                if (-not $groups) { continue }

                $knownIds = @("gemini-5h", "gemini-weekly", "3p-5h", "3p-weekly")
                $buckets = @($groups | ForEach-Object { $_.buckets } | Where-Object { $_.bucketId -in $knownIds })
                $byId = @{}
                foreach ($bucket in $buckets) {
                    if (-not $byId.ContainsKey("$($bucket.bucketId)")) { $byId["$($bucket.bucketId)"] = $bucket }
                }
                $source = "antigravity-language-server"
                $agy = [ordered]@{
                    status      = "ok"
                    source      = $source
                    collectedAt = $collectedAt
                    checkedAt   = $collectedAt
                    session     = ConvertTo-AntigravityMeter $byId["gemini-5h"] $source $collectedAt
                    weekly      = ConvertTo-AntigravityMeter $byId["gemini-weekly"] $source $collectedAt
                    claude      = ConvertTo-AntigravityMeter $byId["3p-5h"] $source $collectedAt
                    claudeWeekly = ConvertTo-AntigravityMeter $byId["3p-weekly"] $source $collectedAt
                }
                $availableMeters = @($agy.session, $agy.weekly, $agy.claude, $agy.claudeWeekly) |
                    Where-Object { $null -ne $_ }
                if ($availableMeters.Count -gt 0) {
                    return $agy
                }
            } catch {
                # 不記錄 Exception.Message，避免 HTTP/解析器未來把敏感 header 帶進錯誤文字。
                continue
            }
        }
    }

    return [ordered]@{
        status = "unavailable"; source = "antigravity-language-server"; checkedAt = $collectedAt
        errorClass = "local_rpc_unavailable"; reason = "本機服務暫時無法回傳額度"
        action = "將於下一輪重試"
    }
}

$result.agy = Get-AntigravityQuota

# ---------- 寫入 Firestore（current + 不覆寫的時間序列） ----------
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

    # 歷史樣本放在既有 ai_usage collection，以沿用目前已部署的 Firestore 規則。
    # 毫秒級 UTC ID 可排序且每輪唯一；Patch 指定完整文件路徑，不會覆寫其他時間點。
    $sampleId = "sample_" + (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmssfff'Z'")
    $historyUri = "https://firestore.googleapis.com/v1/projects/my-teaching-tools-2b36c/databases/(default)/documents/ai_usage/$sampleId" +
                  "?key=AIzaSyBf0sHTsndFCksNlh46G_2mw9rk5zmPdc0"
    Invoke-RestMethod -Method Patch -Uri $historyUri -ContentType "application/json; charset=utf-8" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 30 | Out-Null

    $codexTag = if ($result.codex.source) { "$($result.codex.status)/$($result.codex.source)" } else { "$($result.codex.status)" }
    Write-Log "OK sample=$sampleId claude=$($result.claude.status) codex=$codexTag gemini=$($result.gemini.status) agy=$($result.agy.status)"
} catch {
    Write-Log "Firestore 寫入失敗: $($_.Exception.Message)"
    exit 1
}

# ---------- log 瘦身（保留最後 500 行） ----------
try {
    $lines = Get-Content $logFile -ErrorAction SilentlyContinue
    if ($lines.Count -gt 500) { $lines | Select-Object -Last 500 | Set-Content $logFile -Encoding UTF8 }
} catch {}
