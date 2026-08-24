# v0.2.0 | 2026-05-20
# 變更：改用 frida.spawn() 直接啟動 LINE，確保 hook 在 sqlite3_key 前裝好
# v0.1.0 | 2026-05-20
# 用 Frida hook sqlite3_key，攔截 LINE 傳給 SQLCipher 的實際金鑰

import sys
import io
import pathlib
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    import frida
except ImportError:
    print("[!] frida 未安裝，請先執行：pip install frida frida-tools")
    sys.exit(1)

LINE_EXE = r"C:\Users\admin\AppData\Local\LINE\bin\current\LINE.exe"

HOOK_JS = """
'use strict';

var seen = {};
var hooksInstalled = false;

function bytesToHex(ptr, len) {
    try {
        var b = Memory.readByteArray(ptr, len);
        return Array.from(new Uint8Array(b))
            .map(function(x){ return x.toString(16).padStart(2,'0'); }).join('');
    } catch(e) { return null; }
}

function installHooks() {
    if (hooksInstalled) return;
    var pbkdf2 = Module.findExportByName('libcrypto-1_1-x64.dll', 'PKCS5_PBKDF2_HMAC');
    if (!pbkdf2) { send({type:'info', msg:'libcrypto 還沒載入'}); return; }
    hooksInstalled = true;
    send({type:'info', msg:'libcrypto 已載入，裝 hook...'});

    Interceptor.attach(pbkdf2, {
        onEnter: function(args) {
            var passlen = args[1].toInt32();
            if (passlen <= 0 || passlen > 512) return;
            var hex = bytesToHex(args[0], passlen);
            var iter = args[4].toInt32();
            var keylen = args[6].toInt32();
            if (hex && !seen[hex]) {
                seen[hex] = true;
                send({type:'pbkdf2', passHex:hex, passlen:passlen, iter:iter, keylen:keylen});
            }
        }
    });
    send({type:'info', msg:'Hooked: PKCS5_PBKDF2_HMAC'});

    var cipherInit = Module.findExportByName('libcrypto-1_1-x64.dll', 'EVP_CipherInit_ex');
    if (cipherInit) {
        Interceptor.attach(cipherInit, {
            onEnter: function(args) {
                if (args[3].isNull()) return;
                var hex = bytesToHex(args[3], 32);
                if (hex && !seen[hex]) {
                    seen[hex] = true;
                    send({type:'aes_key', keyHex:hex});
                }
            }
        });
        send({type:'info', msg:'Hooked: EVP_CipherInit_ex'});
    }
}

// 攔截 LoadLibraryExW，在 libcrypto 載入的瞬間裝 hook
var loadLib = Module.findExportByName('KERNEL32.DLL', 'LoadLibraryExW');
if (loadLib) {
    Interceptor.attach(loadLib, {
        onLeave: function() { installHooks(); }
    });
}

// 也試著立即裝（若 libcrypto 在 spawn 前就已載入）
installHooks();
send({type:'info', msg:'等待 libcrypto 載入...'});
"""


def on_message(message, _data):
    if message["type"] == "send":
        payload = message["payload"]
        kind = payload.get("type", "")

        if kind == "pbkdf2":
            print(f"\n{'='*50}")
            print(f"[!!] PBKDF2 passphrase 攔截到！")
            print(f"     長度    : {payload['passlen']} bytes")
            print(f"     Hex     : {payload['passHex']}")
            print(f"     iter    : {payload['iter']}")
            print(f"     keylen  : {payload['keylen']}")
            try:
                s = bytes.fromhex(payload['passHex']).decode('utf-8', errors='replace')
                print(f"     String  : {s!r}")
            except: pass
            print(f"{'='*50}\n")
            out = pathlib.Path(__file__).parent / "found_key.txt"
            out.write_text(payload['passHex'])
            print(f"[OK] 已存到 {out}")
        elif kind == "aes_key":
            print(f"\n{'='*50}")
            print(f"[!!] AES raw key 攔截到！")
            print(f"     Hex : {payload['keyHex']}")
            print(f"{'='*50}\n")
            out = pathlib.Path(__file__).parent / "found_key.txt"
            out.write_text(payload['keyHex'])
            print(f"[OK] 已存到 {out}")
        elif kind == "info":
            print(f"[*] {payload['msg']}")
        elif kind == "err":
            print(f"[!] {payload['msg']}")
        elif kind in ("sqlite3_key", "sqlite3_key_v2"):
            print(f"\n{'='*50}")
            print(f"[!!] 攔截到 {kind}")
            print(f"     長度   : {payload['key_len']} bytes")
            print(f"     Hex    : {payload['key_hex']}")
            if payload.get("key_str"):
                print(f"     String : {payload['key_str']!r}")
            print(f"{'='*50}\n")

            # 自動存檔
            out = pathlib.Path(__file__).parent / "found_key.txt"
            out.write_text(payload["key_hex"])
            print(f"[OK] 已存到 {out}")
    elif message["type"] == "error":
        print(f"[Frida Error] {message['description']}")


def main():
    from detector import find_line_pid

    print(f"[*] 用 Frida spawn 啟動 LINE（金鑰在啟動瞬間產生，必須 spawn）...")
    try:
        pid = frida.spawn(LINE_EXE)
        print(f"[OK] LINE PID: {pid}（已暫停，hook 裝載中）")

        session = frida.attach(pid)
        script = session.create_script(HOOK_JS)
        script.on("message", on_message)
        script.load()

        frida.resume(pid)
        print("[*] LINE 已啟動，等待 libcrypto 載入 + DB 解密...")
        print("[*] Ctrl+C 結束\n")
        sys.stdin.read()

    except frida.PermissionDeniedError:
        print("[!] 權限不足，請用系統管理員身份執行。")
    except KeyboardInterrupt:
        print("\n[*] 結束。")


if __name__ == "__main__":
    main()
