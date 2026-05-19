# v0.1.0 | 2026-05-19
# LINE Data Master MCP server 客戶端
# 透過本機 MCP API 讀取 LINE 資料，不需要處理加密

import json
import pathlib
import urllib.request
import urllib.error

MCP_CONFIG_PATH = pathlib.Path.home() / "AppData/Local/LINE Data Master/mcp_connection.json"


def load_config() -> tuple[str, str]:
    """讀取 LINE Data Master 的 MCP 連線設定。"""
    if not MCP_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"找不到 MCP 設定檔：{MCP_CONFIG_PATH}\n"
            "請先開啟 LINE Data Master 並完成帳號連線。"
        )
    cfg = json.loads(MCP_CONFIG_PATH.read_text())
    base = f"http://localhost:{cfg['port']}"
    token = cfg["token"]
    return base, token


class MCPClient:
    """LINE Data Master MCP server 的簡易客戶端。"""

    def __init__(self):
        self.base, self.token = load_config()
        self.session_id: str | None = None
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _post(self, method: str, params: dict | None = None) -> dict | None:
        """發送 MCP JSON-RPC 請求，回傳解析後的結果。"""
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params or {},
        }).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id

        req = urllib.request.Request(
            f"{self.base}/mcp", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                sid = r.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                raw = r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise ConnectionError(f"MCP 請求失敗 {e.code}: {e.read().decode()[:200]}")

        for line in raw.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        return None

    def connect(self) -> None:
        """建立 MCP session。"""
        self._post("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "line-backup", "version": "0.1"},
        })
        self._post("notifications/initialized")
        print(f"[OK] 已連線到 MCP server ({self.base}), session={self.session_id[:8]}...")

    def call_tool(self, tool_name: str, arguments: dict | None = None) -> dict:
        """呼叫指定工具，回傳結果內容。"""
        resp = self._post("tools/call", {
            "name": tool_name,
            "arguments": arguments or {},
        })
        if resp is None:
            return {}
        if "error" in resp:
            raise RuntimeError(f"工具 {tool_name} 失敗: {resp['error']}")
        # 解析 content[0].text 裡的 JSON
        contents = resp.get("result", {}).get("content", [])
        for item in contents:
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except json.JSONDecodeError:
                    return {"raw": item["text"]}
        return {}

    # ── 便利方法 ──────────────────────────────────────────────────

    def list_chats(self, chat_type: str | None = None) -> list[dict]:
        """取得所有聊天室（自動分頁，每頁 30）。"""
        all_chats = []
        page = 0
        while True:
            args = {"page": page}
            if chat_type:
                args["chat_type"] = chat_type
            result = self.call_tool("list_chats", args)
            if isinstance(result, dict) and "error" in result:
                break
            items = result.get("items", []) if isinstance(result, dict) else result
            if not items:
                break
            all_chats.extend(items)
            if len(items) < 30:
                break
            page += 1
        return all_chats

    def export_chat(self, chat_id: str, fmt: str = "json") -> str:
        """匯出聊天室，回傳本機檔案路徑。"""
        result = self.call_tool("export_chat", {"chat_id": chat_id, "format": fmt})
        return result.get("path", "") or result.get("file_path", "") or str(result)

    def get_chat_history(self, chat_id: str, limit: int = 500, before_id: str | None = None) -> list[dict]:
        args = {"chat_id": chat_id, "limit": limit}
        if before_id:
            args["before_id"] = before_id
        result = self.call_tool("get_chat_history", args)
        return result if isinstance(result, list) else result.get("messages", [])

    def get_dashboard_stats(self) -> dict:
        return self.call_tool("get_dashboard_stats")


if __name__ == "__main__":
    client = MCPClient()
    client.connect()

    print("\n=== 整體統計 ===")
    stats = client.get_dashboard_stats()
    print(json.dumps(stats, ensure_ascii=False, indent=2)[:800])

    print("\n=== 聊天室清單（前 10 個）===")
    chats = client.list_chats()
    for c in chats[:10]:
        print(f"  {c}")
