# v0.4.0 | 2026-05-20
# 變更：只掃 MEM_PRIVATE heap 區段（跳過 DLL/映射），hex 改 regex 搜尋，raw key 改 set 過濾
# v0.3.0 | 2026-05-20
# 變更：__main__ 加金鑰快取，found_key.txt 存在且有效則直接用，跳過記憶體掃描
# v0.2.0 | 2026-05-20
# 變更：修正 hex key 長度 32→64 chars、套件改 sqlcipher3、__main__ 加驗證迴圈
# v0.1.0 | 2026-05-19
# 從 LINE.exe 執行中的記憶體提取 SQLCipher 加密金鑰
#
# 原理：LINE 資料庫用 SQLCipher 加密，金鑰只存在記憶體中。
# 用 ReadProcessMemory 掃描 LINE.exe 所有可讀區段，
# 找出符合格式的候選金鑰，再用開啟資料庫來驗證。

import ctypes
import ctypes.wintypes
import pathlib
import re
from typing import Generator

# Windows API 常數
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000  # heap/stack 私有記憶體，金鑰只會在這裡
PAGE_READABLE = {0x02, 0x04, 0x20, 0x40}

# 預編譯 regex：64 個小寫 hex char（32 bytes AES-256 key）
_HEX64_RE = re.compile(rb'[0-9a-f]{64}')


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress",       ctypes.c_void_p),
        ("AllocationBase",    ctypes.c_void_p),
        ("AllocationProtect", ctypes.wintypes.DWORD),
        ("RegionSize",        ctypes.c_size_t),
        ("State",             ctypes.wintypes.DWORD),
        ("Protect",           ctypes.wintypes.DWORD),
        ("Type",              ctypes.wintypes.DWORD),
    ]


def _open_process(pid: int):
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid
    )
    if not handle:
        raise PermissionError(f"無法開啟 LINE.exe (PID={pid})，請用系統管理員權限執行。")
    return handle


def _iter_readable_regions(handle) -> Generator[tuple[int, int], None, None]:
    """枚舉所有可讀的記憶體區段，yield (起始位址, 大小)。"""
    mbi = MEMORY_BASIC_INFORMATION()
    addr = 0
    max_addr = 0x7FFFFFFFFFFF  # 64-bit user space 上限

    while addr < max_addr:
        ret = ctypes.windll.kernel32.VirtualQueryEx(
            handle, ctypes.c_void_p(addr),
            ctypes.byref(mbi), ctypes.sizeof(mbi)
        )
        if not ret:
            break

        if (mbi.State == MEM_COMMIT
                and mbi.Protect in PAGE_READABLE
                and mbi.Type == MEM_PRIVATE):  # 只掃 heap，跳過 DLL/映射檔
            yield mbi.BaseAddress, mbi.RegionSize

        addr = (mbi.BaseAddress or 0) + mbi.RegionSize


def _read_region(handle, base: int, size: int) -> bytes | None:
    """讀取指定記憶體區段，失敗回傳 None。"""
    # 限制單次讀取大小避免 OOM（LINE.exe 約 1GB）
    MAX_READ = 64 * 1024 * 1024  # 64 MB
    size = min(size, MAX_READ)

    buf = (ctypes.c_char * size)()
    bytes_read = ctypes.c_size_t(0)
    ok = ctypes.windll.kernel32.ReadProcessMemory(
        handle, ctypes.c_void_p(base), buf, size, ctypes.byref(bytes_read)
    )
    if ok and bytes_read.value > 0:
        return bytes(buf[:bytes_read.value])
    return None


def _extract_hex_keys(data: bytes) -> list[str]:
    """用 C regex 引擎搜尋 64-char hex 字串，比 Python 迴圈快 10~50 倍。"""
    results = []
    for m in _HEX64_RE.finditer(data):
        s = m.group().decode()
        if len(set(s)) > 4:  # 排除重複模式
            results.append(s)
    return results


def _extract_raw_keys(data: bytes) -> list[bytes]:
    """搜尋 32-byte 高熵原始位元組（AES-256 raw key）。用 unique byte 數代替熵計算，更快。"""
    keys = []
    for i in range(0, len(data) - 32, 4):
        chunk = data[i:i+32]
        if len(set(chunk)) >= 16:  # 至少 16 種不同 byte，等效高熵
            keys.append(chunk)
    return keys


def scan_memory(pid: int, verbose: bool = False) -> dict[str, list]:
    """
    掃描 LINE.exe 記憶體，回傳候選金鑰：
    {
        "hex_keys": [...],   # 32-char hex 字串格式
        "raw_keys": [...],   # 32-byte 原始位元組
    }
    """
    handle = _open_process(pid)
    hex_keys: set[str] = set()
    raw_keys: set[bytes] = set()

    total_scanned = 0
    try:
        for base, size in _iter_readable_regions(handle):
            data = _read_region(handle, base, size)
            if data is None:
                continue

            total_scanned += len(data)
            if verbose:
                print(f"\r掃描中... {total_scanned // 1024 // 1024} MB", end="", flush=True)

            for k in _extract_hex_keys(data):
                hex_keys.add(k)
            for k in _extract_raw_keys(data):
                raw_keys.add(k)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)

    if verbose:
        print()

    return {
        "hex_keys": list(hex_keys),
        "raw_keys": [k.hex() for k in raw_keys],
        "total_scanned_mb": total_scanned // 1024 // 1024,
    }


def validate_key(edb_path: pathlib.Path, key_hex: str) -> bool:
    """
    嘗試用 key_hex 開啟 .edb，確認是否為正確金鑰。
    需要安裝 pysqlcipher3。
    """
    try:
        import sqlcipher3
    except ImportError:
        print("[!] sqlcipher3 未安裝，跳過驗證。")
        return False

    # 試所有 compatibility 模式（raw hex key）
    for compat in [1, 2, 3, 4]:
        try:
            conn = sqlcipher3.connect(str(edb_path))
            conn.execute(f"PRAGMA key=\"x'{key_hex}'\"")
            conn.execute(f"PRAGMA cipher_compatibility = {compat}")
            conn.execute("SELECT count(*) FROM sqlite_master")
            conn.close()
            print(f"[OK] raw key compat={compat}: {key_hex[:8]}...")
            return True
        except Exception:
            pass

    # 試把 hex 當 passphrase 字串（有些 app 用文字 key）
    try:
        passphrase = bytes.fromhex(key_hex).decode("utf-8", errors="replace")
        for compat in [1, 2, 3, 4]:
            try:
                conn = sqlcipher3.connect(str(edb_path))
                conn.execute(f"PRAGMA key='{passphrase}'")
                conn.execute(f"PRAGMA cipher_compatibility = {compat}")
                conn.execute("SELECT count(*) FROM sqlite_master")
                conn.close()
                print(f"[OK] passphrase compat={compat}: {passphrase[:8]!r}...")
                return True
            except Exception:
                pass
    except Exception:
        pass

    return False


if __name__ == "__main__":
    from detector import find_line_pid, find_edb_files

    # 找 .edb 檔案（掃描前先確認，避免白掃）
    edb_files = find_edb_files()
    if not edb_files:
        print("[!] 找不到 .edb 資料庫，請確認 LINE 已登入。")
        exit(1)
    edb_path = edb_files[0]
    print(f"[OK] 使用資料庫：{edb_path}")

    # 快取：若 found_key.txt 存在且金鑰仍有效，直接用
    key_cache = pathlib.Path(__file__).parent / "found_key.txt"
    if key_cache.exists():
        cached = key_cache.read_text().strip()
        print(f"[快取] 找到已存金鑰，驗證中...")
        if validate_key(edb_path, cached):
            print(f"[OK] 快取金鑰有效，無需重新掃描。")
            print(f"金鑰：{cached}")
            exit(0)
        else:
            print("[!] 快取金鑰已失效，重新掃描...")

    # 需要掃描時才開 LINE process
    pid = find_line_pid()
    if not pid:
        print("[!] LINE 未執行，請先開啟 LINE。")
        exit(1)

    print(f"[OK] LINE PID: {pid}")
    print("開始掃描記憶體（可能需要 30-60 秒）...")

    result = scan_memory(pid, verbose=True)
    print(f"掃描完成：{result['total_scanned_mb']} MB")
    print(f"找到 hex 格式候選金鑰：{len(result['hex_keys'])} 個")
    print(f"找到 raw 格式候選金鑰：{len(result['raw_keys'])} 個")

    # 合併所有候選（hex + raw 轉 hex）
    all_candidates = list(set(result["hex_keys"] + result["raw_keys"]))
    print(f"開始驗證 {len(all_candidates)} 個候選金鑰...")

    found_key = None
    for i, key in enumerate(all_candidates, 1):
        if i % 1000 == 0:
            print(f"\r驗證中... {i}/{len(all_candidates)}", end="", flush=True)
        if validate_key(edb_path, key):
            found_key = key
            break

    print()
    if found_key:
        print(f"\n[SUCCESS] 金鑰找到：{found_key}")
        key_cache.write_text(found_key)
        print(f"已存到 {key_cache}")
    else:
        print("[FAIL] 未找到有效金鑰，raw key 候選清單：")
        for k in result["raw_keys"][:5]:
            print(f"  {k}")
