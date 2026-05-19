# v0.1.0 | 2026-05-19
# 在 Windows 工作排程器建立 LINE 自動備份排程
# 用法：在 PowerShell（系統管理員）執行：.\schedule_task.ps1

$TaskName   = "LINE_Auto_Backup"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python     = (Get-Command python -ErrorAction SilentlyContinue).Source
$BackupScript = Join-Path $ScriptDir "backup.py"
$LogFile    = Join-Path $ScriptDir "backup_schedule.log"

if (-not $Python) {
    Write-Error "找不到 python，請確認已安裝 Python 並加入 PATH"
    exit 1
}

Write-Host "設定資訊："
Write-Host "  排程名稱：$TaskName"
Write-Host "  Python：  $Python"
Write-Host "  腳本：    $BackupScript"
Write-Host "  執行時間：每天 03:00"
Write-Host ""

# 排程執行的指令（redirect log）
$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$BackupScript`"" `
    -WorkingDirectory $ScriptDir

# 每天凌晨 3 點執行
$Trigger = New-ScheduledTaskTrigger -Daily -At "03:00"

# 以目前使用者身分執行（不需要密碼彈窗）
$Principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType S4U `
    -RunLevel Highest

# 設定：失敗時 5 分鐘後重試，最多 3 次
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable

# 刪除同名舊排程（避免重複）
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已刪除舊排程"
}

# 建立新排程
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "每日自動備份 LINE 聊天資料" | Out-Null

Write-Host "排程建立成功！"
Write-Host ""
Write-Host "管理指令："
Write-Host "  查看：  Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "  立即跑：Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  刪除：  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
