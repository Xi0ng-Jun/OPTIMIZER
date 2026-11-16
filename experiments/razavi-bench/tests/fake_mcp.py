"""A stand-in for the notebook's MCP endpoint, for the offline tests (standard library only).

It behaves like the real one where the client has to cope: the unslashed path answers 307, a
session id is issued at initialize and required afterwards, and answers come as an event stream
(tools/list as plain JSON, so both forms are exercised). `fail_after=n` makes it answer 503 to
every connection after the first n.
"""

from __future__ import annotations

import http.server
import json
import threading

TOKEN = "t"
TOOLS = [
    {"name": "search", "inputSchema": {"type": "object", "properties": {"query": {}, "limit": {}}, "required": ["query"]}},
    {"name": "ask", "inputSchema": {"type": "object", "properties": {"question": {}, "output": {}, "mode": {}}, "required": ["question"]}},
]


class Handler(http.server.BaseHTTPRequestHandler):
    def reply(self, status: int, body: bytes = b"", content_type: str = "application/json", **headers: str) -> None:
        self.send_response(status)
        for name, value in {"Content-Type": content_type, "Content-Length": str(len(body)), **headers}.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(body)

    def stream(self, *messages: dict, **headers: str) -> None:
        body = "".join(f"event: message\ndata: {json.dumps(m)}\n\n" for m in messages).encode()
        self.reply(200, body, "text/event-stream", **headers)

    def do_DELETE(self) -> None:
        self.reply(200)

    def do_POST(self) -> None:
        state = self.server.state
        message = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        state["requests"].append({"path": self.path, "auth": self.headers.get("Authorization"),
                                  "session": self.headers.get("Mcp-Session-Id"), "method": message.get("method"),
                                  "params": message.get("params")})
        if self.path == "/mcp":
            return self.reply(307, Location="/mcp/")
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self.reply(401, b"bad token")
        method, ident = message.get("method"), message.get("id")
        answer = lambda result: {"jsonrpc": "2.0", "id": ident, "result": result}
        if method == "initialize":
            state["connections"] += 1
            if state["fail_after"] is not None and state["connections"] > state["fail_after"]:
                return self.reply(503, b"down")
            return self.stream(answer({"protocolVersion": "2025-03-26", "serverInfo": {"name": "fake notebook"}}),
                               Mcp_Session_Id="s1")
        if self.headers.get("Mcp-Session-Id") != "s1":
            return self.reply(400, b"no session")
        if ident is None:
            return self.reply(202)
        if method == "tools/list":
            return self.reply(200, json.dumps(answer({"tools": TOOLS})).encode())
        if method == "tools/call":
            text = json.dumps({"status": "retrieved", "items": [{"text": "x y"}] * 2,
                               "echo": message["params"]["arguments"]}, ensure_ascii=False)
            return self.stream({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}},
                               answer({"content": [{"type": "text", "text": text}], "isError": False}))
        self.reply(200, json.dumps({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "no such method"}}).encode())

    def log_message(self, *args) -> None:
        pass


def start(fail_after: int | None = None) -> tuple[http.server.ThreadingHTTPServer, str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.state = {"requests": [], "connections": 0, "fail_after": fail_after}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/mcp"
