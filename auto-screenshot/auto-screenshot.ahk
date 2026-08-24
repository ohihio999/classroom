#Requires AutoHotkey v2.0
#SingleInstance Force
; =====================================================================
;  自動右鍵截圖.ahk
;  版號：v5.1
;  版更記錄：
;    v5.1 (2026-08-08) 修正第一張圖漏截：原本 F9 一開始就先送右方向鍵，
;                      按 F9 時螢幕上那一頁會直接被翻掉、沒有截到。
;                      改成「開始時先截目前這一頁，之後才按右鍵翻頁」。
;                      第一張仍等滿設定秒數才截（避免把『執行中』提示框
;                      拍進圖裡），開始提示的顯示時間也一併壓到等待秒數
;                      以內。暫停後按 F9 恢復不會重截同一頁（只有這一輪
;                      還沒截過圖時才先截）。
;    v5.0 (2026-08-08) 修正直式／不同縮放螢幕被裁切，並新增截完後的動作：
;                      (1) DPI 感知改成 PER_MONITOR_AWARE_V2（舊系統自動
;                          退回 SetProcessDPIAware）。原本只有系統層級
;                          感知，副螢幕縮放比例與主螢幕不同時，取到的
;                          會是被換算縮小的座標，實際 BitBlt 卻抓真實
;                          像素，於是只截到左上角一塊。
;                      (2) 擷取範圍改成「每張截圖前重新讀取」。螢幕轉
;                          直式、改解析度、插拔螢幕都會自動跟上，不必
;                          回選單重選；尺寸有變會跳提示。選的螢幕被拔
;                          掉時自動退回主畫面。
;                      (3) 新增「截滿之後」選項：不做任何事／關機／休眠，
;                          倒數 60 秒可取消，也可按「立刻執行」。只有設
;                          了張數上限才會觸發。
;    v4.0 (2026-08-08) 啟動選單新增三項可調設定：
;                      (1) 等待秒數：按 → 之後等幾秒才截圖，下拉常用值
;                          也可自行輸入（支援小數，0.2～3600 秒）。
;                      (2) 張數上限：截滿指定張數自動停止，0 = 不限。
;                      (3) 存放位置：可直接輸入或按「瀏覽…」選資料夾，
;                          不存在會自動建立。
;                      設定會保留，托盤選單重開時帶出目前值。
;    v3.0 (2026-08-07) 啟動時先跳選單，讓使用者選要擷取哪個畫面（自動列出
;                      所有實體螢幕、標示主／副與解析度，另有「全部螢幕合併」
;                      選項）。托盤選單也能隨時叫回這個選單重選。
;    v2.0 (2026-08-07) 改成「按右方向鍵 → 等 5 秒 → 截圖 → 確認截圖成功
;                      → 才按下一次右方向鍵」的循序流程，取代 v1.0 的
;                      10 秒／15 秒兩支獨立計時器。截圖範圍改為只抓主螢幕
;                      （原本是含多螢幕的整個虛擬桌面）。截圖失敗會每秒
;                      重試、連續 3 次失敗才停止，期間不會誤按右方向鍵。
;    v1.0 (2026-08-07) 初版。每 10 秒送一次鍵盤右方向鍵、每 15 秒擷取
;                      一次全螢幕（多螢幕合併）存成 PNG。F9 開始/暫停、
;                      F10 結束。截圖用 GDI+ 直接寫檔，不經剪貼簿。
; =====================================================================

; ---------- 預設值（都可在啟動選單中改） ----------
WaitAfterKey   := 5000                  ; 按完右方向鍵後，等多久才截圖（毫秒）
MaxShots       := 0                     ; 截滿幾張自動停止，0 = 不限
SaveDir        := A_Desktop "\自動截圖"  ; 截圖存放資料夾
AfterDone      := "none"                ; 截滿之後：none / shutdown / hibernate
; ---------- 固定參數 ----------
PauseAfterShot := 200                   ; 確認截圖成功後，隔多久按下一次右方向鍵（毫秒）
RetryDelay     := 1000                  ; 截圖失敗的重試間隔（毫秒）
MaxFail        := 3                     ; 連續失敗幾次就停止
AfterCountdown := 60                    ; 關機／休眠前的倒數秒數（可取消）
; --------------------------------

; 高 DPI：優先用 PER_MONITOR_AWARE_V2(-4)，這樣每台螢幕都拿到真實像素座標，
; 副螢幕縮放比例不同也不會截到一半。舊系統沒有這個 API 就退回系統層級感知。
try {
    if !DllCall("SetProcessDpiAwarenessContext", "ptr", -4, "int")
        DllCall("SetProcessDPIAware")
} catch
    DllCall("SetProcessDPIAware")

; GDI+ 啟動
if !DllCall("LoadLibrary", "str", "gdiplus", "ptr") {
    MsgBox "找不到 gdiplus.dll，無法截圖。"
    ExitApp
}
si := Buffer(24, 0)
NumPut("uint", 1, si, 0)
DllCall("gdiplus\GdiplusStartup", "ptr*", &pToken := 0, "ptr", si, "ptr", 0)

Running   := false
ShotCount := 0
FailCount := 0
CapX := 0, CapY := 0, CapW := 0, CapH := 0   ; 擷取範圍，每張截圖前重算
CapName   := ""
CapChoice := 0                                ; 0=未設定　-1=全部螢幕合併　n=第 n 台螢幕
AfterLeft := 0

; 托盤選單：可隨時重選畫面與設定
A_TrayMenu.Insert("1&", "設定（畫面／秒數／張數／位置）", (*) => ShowMenu())
A_TrayMenu.Insert("2&")

ShowMenu()

F9::Toggle()
F10::Quit()

; ---------------- 啟動選單 ----------------
ShowMenu() {
    global
    local items, prim, l, t, r, b, cnt, chooseIdx, afterIdx

    if Running                            ; 執行中先暫停，免得換到一半
        Toggle()

    prim := MonitorGetPrimary()
    cnt  := MonitorGetCount()
    items := []
    Loop cnt {
        MonitorGet(A_Index, &l, &t, &r, &b)
        items.Push("螢幕 " A_Index (A_Index = prim ? "（主畫面）" : "（副畫面）")
                 . "　" (r - l) " × " (b - t) ((r - l) < (b - t) ? "（直式）" : "")
                 . "　位置 " l "," t)
    }
    items.Push("全部螢幕合併（虛擬桌面）")

    chooseIdx := (CapChoice = -1) ? items.Length
               : (CapChoice >= 1 && CapChoice <= cnt) ? CapChoice
               : prim

    MenuGui := Gui("+AlwaysOnTop", "自動右鍵截圖 v5.1")
    MenuGui.SetFont("s10", "Microsoft JhengHei")

    MenuGui.Add("Text", , "要擷取哪個畫面？（尺寸會在每次截圖前自動重讀，轉直式不必回來重選）")
    MenuDDL := MenuGui.Add("DropDownList", "xm w520 Choose" chooseIdx, items)

    MenuGui.Add("Text", "xm y+14", "按 → 之後等幾秒才截圖？（可自行輸入，支援小數）")
    MenuSec := MenuGui.Add("ComboBox", "xm w120", ["1", "2", "3", "5", "8", "10", "15", "20", "30"])
    MenuSec.Text := WaitAfterKey / 1000
    MenuGui.Add("Text", "x+8 yp+4", "秒")

    MenuGui.Add("Text", "xm y+14", "截幾張後自動停止？（0 = 不限，按 F10 才停）")
    MenuMax := MenuGui.Add("ComboBox", "xm w120", ["0", "10", "20", "30", "50", "100", "200", "500"])
    MenuMax.Text := MaxShots
    MenuGui.Add("Text", "x+8 yp+4", "張")

    afterIdx := (AfterDone = "shutdown") ? 2 : (AfterDone = "hibernate") ? 3 : 1
    MenuGui.Add("Text", "xm y+14", "截滿之後要做什麼？（會先倒數 " AfterCountdown " 秒，隨時可取消）")
    MenuAfter := MenuGui.Add("DropDownList", "xm w200 Choose" afterIdx, ["不做任何事", "關機", "休眠"])

    MenuGui.Add("Text", "xm y+14", "圖片存放位置（資料夾不存在會自動建立）")
    MenuDir := MenuGui.Add("Edit", "xm w420", SaveDir)
    btnBrowse := MenuGui.Add("Button", "x+8 yp-2 w90", "瀏覽…")
    btnBrowse.OnEvent("Click", BrowseDir)

    MenuGui.Add("Text", "xm y+14 cGray", "確定後：按 F9 開始／暫停，F10 結束")

    btnOK := MenuGui.Add("Button", "xm y+12 w110 Default", "確定")
    btnOK.OnEvent("Click", MenuConfirm)
    btnCancel := MenuGui.Add("Button", "x+10 yp w110", "結束程式")
    btnCancel.OnEvent("Click", (*) => Quit())
    MenuGui.OnEvent("Close", (*) => Quit())
    MenuGui.OnEvent("Escape", (*) => Quit())
    MenuGui.Show()
}

BrowseDir(*) {
    global MenuDir
    local sel := DirSelect("*" MenuDir.Value, 3, "選擇圖片存放位置")
    if (sel != "")
        MenuDir.Value := sel
}

MenuConfirm(*) {
    global
    local idx, sec, mx, dir

    sec := ParseNum(MenuSec.Text)
    if (sec < 0.2 || sec > 3600) {
        MsgBox "等待秒數請填 0.2 ～ 3600 之間的數字。", "設定有誤", "Icon!"
        return
    }
    mx := ParseNum(MenuMax.Text)
    if (mx < 0 || mx > 100000) {
        MsgBox "張數上限請填 0（不限）～ 100000 的整數。", "設定有誤", "Icon!"
        return
    }
    if (MenuAfter.Value > 1 && mx = 0) {
        MsgBox "張數上限是 0（不限）就永遠不會自動停止，關機／休眠也不會執行。`n"
             . "請填張數上限，或把「截滿之後」改回「不做任何事」。", "設定有誤", "Icon!"
        return
    }
    dir := Trim(MenuDir.Value, " `t`r`n`"")
    if (dir = "") {
        MsgBox "請填圖片存放位置。", "設定有誤", "Icon!"
        return
    }
    if !DirExist(dir) {
        try
            DirCreate dir
        catch as e {
            MsgBox "無法建立資料夾：`n" dir "`n`n" e.Message, "設定有誤", "Icon!"
            return
        }
    }

    WaitAfterKey := Round(sec * 1000)
    MaxShots     := Integer(Round(mx))
    SaveDir      := dir
    AfterDone    := ["none", "shutdown", "hibernate"][MenuAfter.Value]

    idx := MenuDDL.Value
    CapChoice := (idx > MonitorGetCount()) ? -1 : idx
    RefreshCapRect()

    ShotCount := 0                                        ; 改設定後重新計數
    MenuGui.Destroy()
    TrayTip "自動右鍵截圖 v5.1"
          , "擷取範圍：" CapName "　" CapW "×" CapH "`n"
          . "間隔：" (WaitAfterKey / 1000) " 秒　張數：" (MaxShots > 0 ? MaxShots " 張" : "不限") "`n"
          . "截滿之後：" AfterDoneText() "`n"
          . "存放：" SaveDir "`n"
          . "F9 開始/暫停　F10 結束"
}

AfterDoneText() {
    global AfterDone
    return (AfterDone = "shutdown") ? "關機" : (AfterDone = "hibernate") ? "休眠" : "不做任何事"
}

; 從字串開頭取出數字（"10"、"3.5"、"0（不限）" 都吃得下）；取不到回傳 -1
ParseNum(s) {
    local m
    if RegExMatch(Trim(s), "^([0-9]+(?:\.[0-9]+)?)", &m)
        return Number(m[1])
    return -1
}

; 依目前選擇重新計算擷取範圍。螢幕轉直式／改解析度／插拔都靠這個跟上。
RefreshCapRect() {
    global CapChoice, CapX, CapY, CapW, CapH, CapName
    local l, t, r, b, cnt := MonitorGetCount()

    if (CapChoice = 0)
        return false

    if (CapChoice = -1) {                       ; 全部螢幕合併（虛擬桌面）
        CapX := SysGet(76), CapY := SysGet(77)
        CapW := SysGet(78), CapH := SysGet(79)
        CapName := "全部螢幕合併"
        return true
    }

    if (CapChoice > cnt)                        ; 選的那台被拔掉了 → 退回主畫面
        CapChoice := MonitorGetPrimary()

    MonitorGet(CapChoice, &l, &t, &r, &b)
    CapX := l, CapY := t, CapW := r - l, CapH := b - t
    CapName := "螢幕 " CapChoice (CapChoice = MonitorGetPrimary() ? "（主畫面）" : "（副畫面）")
             . ((r - l) < (b - t) ? "（直式）" : "")
    return true
}

; ---------------- 主流程 ----------------
Toggle() {
    global Running, FailCount, ShotCount, CapChoice, CapName, CapW, CapH, MaxShots, WaitAfterKey
    local firstShot, tipMs
    if (CapChoice = 0) {
        Notify "請先在選單設定擷取畫面（托盤圖示 → 設定）", 2500
        return
    }
    Running := !Running
    if Running {
        if (MaxShots > 0 && ShotCount >= MaxShots)
            ShotCount := 0                ; 上一輪已截滿，這次重新計數
        FailCount := 0
        RefreshCapRect()
        ; 這一輪還沒截過圖 → 先截目前這一頁，不要一開始就把它翻掉
        firstShot := (ShotCount = 0)
        ; 提示框要在截圖前消失，否則會被拍進第一張圖裡
        tipMs := firstShot ? Min(2000, Max(300, WaitAfterKey - 500)) : 2000
        Notify "▶ 執行中｜" CapName " " CapW "×" CapH "｜每 " (WaitAfterKey / 1000) " 秒一張｜"
             . (MaxShots > 0 ? "目標 " MaxShots " 張（已 " ShotCount "）" : "不限張數（已 " ShotCount " 張）"), tipMs
        if firstShot
            SetTimer Step_Capture, -WaitAfterKey   ; 第一張：不按右鍵，直接截目前畫面
        else
            SetTimer Step_Press, -1                ; 恢復執行：照原流程先翻頁
    } else {
        Notify "⏸ 已暫停（已截 " ShotCount " 張）", 1500
    }
}

; 第一步：按右方向鍵，然後排程等待秒數後截圖
Step_Press() {
    global Running, WaitAfterKey
    if !Running
        return
    Send "{Right}"
    SetTimer Step_Capture, -WaitAfterKey
}

; 第二步：截圖並確認寫檔成功，成功才回到第一步
Step_Capture() {
    global Running, SaveDir, ShotCount, FailCount, PauseAfterShot, RetryDelay, MaxFail
    global MaxShots, AfterDone, CapW, CapH
    local oldW, oldH
    if !Running
        return

    oldW := CapW, oldH := CapH
    RefreshCapRect()                     ; 每張都重讀，轉直式／改解析度立刻跟上
    if (CapW != oldW || CapH != oldH)
        Notify "🔄 畫面尺寸改變，改抓 " CapW "×" CapH, 1500

    if (CapW <= 0 || CapH <= 0) {
        Notify "⚠ 讀不到有效的畫面範圍，暫停", 2500
        Running := false
        return
    }

    if !DirExist(SaveDir) {              ; 執行中資料夾被移除也能自救
        try
            DirCreate SaveDir
    }

    file := SaveDir "\shot_" FormatTime(A_Now, "yyyyMMdd_HHmmss") ".png"
    n := 1
    while FileExist(file)                ; 同一秒內重複時加流水號，避免覆蓋
        file := SaveDir "\shot_" FormatTime(A_Now, "yyyyMMdd_HHmmss") "_" n++ ".png"

    ok := CaptureScreen(file)            ; 0 = GDI+ 回報成功

    if (ok = 0 && FileExist(file) && FileGetSize(file) > 0) {
        ShotCount++
        FailCount := 0
        Notify "📸 已截 " ShotCount (MaxShots > 0 ? " / " MaxShots : "") " 張", 800
        if (MaxShots > 0 && ShotCount >= MaxShots) {          ; 截滿就收工
            Running := false
            TrayTip "自動右鍵截圖 v5.1"
                  , "已截滿 " MaxShots " 張，自動停止。`n存放：" SaveDir
            if (AfterDone != "none")
                StartAfterDone()
            else
                Notify "✅ 已截滿 " MaxShots " 張，自動停止", 3000
            return
        }
        SetTimer Step_Press, -PauseAfterShot     ; 確認成功，才按下一次右方向鍵
    } else {
        FailCount++
        if (FailCount >= MaxFail) {
            Running := false
            MsgBox "連續 " MaxFail " 次截圖失敗，已停止。`n存放路徑：" SaveDir
            return
        }
        Notify "⚠ 截圖失敗，重試中（" FailCount "/" MaxFail "）", 1500
        SetTimer Step_Capture, -RetryDelay        ; 只重試截圖，不按右方向鍵
    }
}

; ---------------- 截滿之後：關機／休眠（倒數可取消） ----------------
StartAfterDone() {
    global AfterGui, AfterText, AfterLeft, AfterCountdown
    local btnCancel, btnNow

    AfterLeft := AfterCountdown
    AfterGui := Gui("+AlwaysOnTop", "自動右鍵截圖 v5.1")
    AfterGui.SetFont("s11", "Microsoft JhengHei")
    AfterText := AfterGui.Add("Text", "w400 r3", "")
    btnCancel := AfterGui.Add("Button", "xm w190 Default", "取消（什麼都不做）")
    btnCancel.OnEvent("Click", CancelAfterDone)
    btnNow := AfterGui.Add("Button", "x+10 yp w190", "立刻執行")
    btnNow.OnEvent("Click", (*) => DoAfterDone())
    AfterGui.OnEvent("Close", CancelAfterDone)
    AfterGui.OnEvent("Escape", CancelAfterDone)

    AfterTick()
    AfterGui.Show()
    SetTimer AfterTick, 1000
}

AfterTick() {
    global AfterLeft, AfterText, ShotCount, SaveDir
    AfterText.Value := "已截滿 " ShotCount " 張，存放：" SaveDir "`n`n"
                     . AfterLeft " 秒後" AfterDoneText() "。要停手請按「取消」。"
    if (AfterLeft <= 0) {
        SetTimer AfterTick, 0
        DoAfterDone()
        return
    }
    AfterLeft--
}

CancelAfterDone(*) {
    global AfterGui
    SetTimer AfterTick, 0
    try AfterGui.Destroy()
    Notify "已取消，電腦不會關機／休眠", 2500
}

DoAfterDone() {
    global AfterGui, AfterDone
    SetTimer AfterTick, 0
    SetTimer Step_Press, 0
    SetTimer Step_Capture, 0
    try AfterGui.Destroy()

    if (AfterDone = "shutdown") {
        Run 'shutdown.exe /s /t 0', , "Hide"
    } else if (AfterDone = "hibernate") {
        ; SetSuspendState(Hibernate=1, Force=0, DisableWakeEvent=0)
        if !DllCall("PowrProf\SetSuspendState", "int", 1, "int", 0, "int", 0, "int")
            Run 'shutdown.exe /h', , "Hide"      ; API 失敗就走命令列休眠
    }
}

Quit() {
    global pToken
    SetTimer Step_Press, 0
    SetTimer Step_Capture, 0
    SetTimer AfterTick, 0
    DllCall("gdiplus\GdiplusShutdown", "ptr", pToken)
    ExitApp
}

Notify(text, ms) {
    ToolTip text
    SetTimer () => ToolTip(), -ms
}

; 擷取指定範圍並存成 PNG，回傳 GDI+ 狀態碼（0 = 成功）
CaptureScreen(outPath) {
    global CapX, CapY, CapW, CapH
    local x := CapX, y := CapY, w := CapW, h := CapH

    hdcSrc := DllCall("GetDC", "ptr", 0, "ptr")
    hdcDst := DllCall("gdi32\CreateCompatibleDC", "ptr", hdcSrc, "ptr")
    hbm    := DllCall("gdi32\CreateCompatibleBitmap", "ptr", hdcSrc, "int", w, "int", h, "ptr")
    obm    := DllCall("gdi32\SelectObject", "ptr", hdcDst, "ptr", hbm, "ptr")

    ; SRCCOPY(0x00CC0020) | CAPTUREBLT(0x40000000)：CAPTUREBLT 才抓得到分層視窗
    DllCall("gdi32\BitBlt", "ptr", hdcDst, "int", 0, "int", 0, "int", w, "int", h
                          , "ptr", hdcSrc, "int", x, "int", y, "uint", 0x40CC0020)

    DllCall("gdiplus\GdipCreateBitmapFromHBITMAP", "ptr", hbm, "ptr", 0, "ptr*", &pBitmap := 0)
    clsid := Buffer(16, 0)
    DllCall("ole32\CLSIDFromString", "wstr", "{557CF406-1A04-11D3-9A73-0000F81EF32E}", "ptr", clsid)
    status := DllCall("gdiplus\GdipSaveImageToFile", "ptr", pBitmap, "wstr", outPath
                    , "ptr", clsid, "ptr", 0, "int")

    DllCall("gdiplus\GdipDisposeImage", "ptr", pBitmap)
    DllCall("gdi32\SelectObject", "ptr", hdcDst, "ptr", obm)
    DllCall("gdi32\DeleteObject", "ptr", hbm)
    DllCall("gdi32\DeleteDC", "ptr", hdcDst)
    DllCall("ReleaseDC", "ptr", 0, "ptr", hdcSrc)
    return status
}
