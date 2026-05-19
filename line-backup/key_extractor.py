# v0.1.0 | 2026-05-19
# 從 LINE.exe 執行中的記憶體提取 SQLCipher 加密金鑰
#
# 原理：LINE 資料庫用 SQLCipher 加密，金鑰只存在記憶體中。
# 用 ReadProcessMemory 掃描 LINE.exe 所有可讀區段，
# 找出符合格式的候選金鑰，再用開啟資料庫來驗證。

import ctypes
import ctypes.wintypes
import struct
import pathlib
from typing import Generator

# Windows API 常數
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_READABLE = {0x02, 0x04, 0x20, 0x40}  # PAGE_READONLY, PAGE_READWRITE, PAGE_EXECUTE_READ, PAGE_EXECUTE_READWRITE


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

        if mbi.State == MEM_COMMIT and mbi.Protect in PAGE_READABLE:
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
    """
    在記憶體區段中搜尋 32 字元的十六進位字串（16 bytes = 128-bit key）。
    LINE 的圖片金鑰格式為 32 hex chars（小寫），資料庫金鑰可能同格式。
    """
    keys = []
    for i in range(len(data) - 32):
        chunk = data[i:i+32]
        try:
            s = chunk.decode("ascii")
            # 必須是純十六進位且全小寫
            if s.isalnum() and s == s.lower() and all(c in "0123456789abcdef" for c in s):
                # 排除全相同字元（非真實金鑰）
                if len(set(s)) > 4:
                    keys.append(s)
        except (UnicodeDecodeError, ValueError):
            pass
    return keys


def _extract_raw_keys(data: bytes) -> list[bytes]:
    """
    搜尋 32-byte 高熵原始位元組序列（AES-256 raw key）。
    用熵值過濾，避免誤判全零或重複模式。
    """
    import math
    keys = []
    for i in range(0, len(data) - 32, 4):  # 4-byte 對齊
        chunk = data[i:i+32]
        # 計算 byte 分佈熵
        counts = [0] * 256
        for b in chunk:
            counts[b] += 1
        entropy = -sum((c/32) * math.log2(c/32) for c in counts if c > 0)
        if entropy > 3.5:  # 真實金鑰熵值通常 > 3.5
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
        import pysqlcipher3.dbapi2 as sqlcipher
    except ImportError:
        print("[!] pysqlcipher3 未安裝，跳過驗證。")
        return False

    for compat in [3, 4]:
        try:
            conn = sqlcipher.connect(str(edb_path))
            conn.execute(f"PRAGMA key=\"x'{key_hex}'\"")
            conn.execute(f"PRAGMA cipher_compatibility = {compat}")
            conn.execute("SELECT count(*) FROM sqlite_master")
            conn.close()
            print(f"[OK] 金鑰驗證成功 (cipher_compatibility={compat}): {key_hex[:8]}...")
            return True
        except Exception:
            pass
    return False


if __name__ == "__main__":
    from detector import find_line_pid, find_edb_files

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

    # 顯示前幾個 hex 候選
    for k in result["hex_keys"][:10]:
        print(f"  hex: {k}")
