#!/usr/bin/env python3
"""A minimal MCP client over Streamable HTTP, standard library only.

Enough to ask the notebook's MCP endpoint whether it is up and to time one tool call without
going through Claude Code: initialize, tools/list, tools/call. Responses may arrive as JSON or
as a server-sent event stream; a redirect is followed only to the same host, with the method,
body and Authorization header kept.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

PROTOCOL = "2025-03-26"


class McpError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _events(response: Any) -> Iterator[dict[str, Any]]:
    """The JSON messages of a server-sent event stream."""
    data: list[str] = []

    def flush() -> Iterator[dict[str, Any]]:
        if data:
            text, data[:] = "\n".join(data), []
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                return
            if isinstance(message, dict):
                yield message

    for raw in response:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif not line:
            yield from flush()
    yield from flush()


class McpClient:
    def __init__(self, url: str, token: str, timeout: float = 60.0) -> None:
        self.url, self.token, self.timeout = url, token, timeout
        self.session, self.version, self._next_id = "", "", 0
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.version:
            headers["MCP-Protocol-Version"] = self.version
        return headers

    def _open(self, method: str, data: bytes | None, timeout: float) -> Any:
        for _ in range(4):
            request = urllib.request.Request(self.url, data=data, headers=self._headers(), method=method)
            try:
                return self._opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                target = exc.headers.get("Location") if exc.code in (301, 302, 307, 308) else None
                if not target:
                    raise McpError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
                target = urllib.parse.urljoin(self.url, target)
                if urllib.parse.urlsplit(target).netloc != urllib.parse.urlsplit(self.url).netloc:
                    raise McpError("redirected to another host; not following with the token") from exc
                self.url = target
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise McpError(f"{type(exc).__name__}: {exc}") from exc
        raise McpError("too many redirects")

    def _send(self, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any] | None:
        """POST one JSON-RPC message and return the response to it (None for a notification)."""
        want = payload.get("id")
        with self._open("POST", json.dumps(payload).encode("utf-8"), timeout or self.timeout) as response:
            self.session = response.headers.get("Mcp-Session-Id") or self.session
            if want is None:
                return None
            try:
                if response.headers.get("Content-Type", "").startswith("text/event-stream"):
                    for message in _events(response):
                        if message.get("id") == want and ("result" in message or "error" in message):
                            return message
                    raise McpError("the stream ended without a response")
                message = json.loads(response.read().decode("utf-8"))
            except (TimeoutError, OSError, json.JSONDecodeError) as exc:
                raise McpError(f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(message, dict):
            raise McpError("unexpected response")
        return message

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        self._next_id += 1
        message = self._send({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}, timeout)
        if not message or "error" in message:
            raise McpError(f"{method}: {json.dumps((message or {}).get('error'), ensure_ascii=False)[:300]}")
        return message.get("result") or {}

    def initialize(self) -> dict[str, Any]:
        result = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                             "clientInfo": {"name": "razavi-bench-probe", "version": "1"}})
        self.version = str(result.get("protocolVersion") or PROTOCOL)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        for _ in range(50):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += result.get("tools") or []
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments}, timeout)

    def close(self) -> None:
        if self.session:
            try:
                self._open("DELETE", None, 10).close()
            except McpError:
                pass
            self.session = ""


def ping(url: str, token: str, timeout: float = 30.0) -> dict[str, Any]:
    """Connect and list the tools: {"ok", "seconds", "tools" | "error"}."""
    started = time.time()
    client = McpClient(url, token, timeout)
    try:
        server = client.initialize()
        tools = client.list_tools()
        return {"ok": True, "seconds": round(time.time() - started, 1), "tools": tools,
                "server": (server.get("serverInfo") or {}).get("name")}
    except McpError as exc:
        return {"ok": False, "seconds": round(time.time() - started, 1), "error": str(exc)}
    finally:
        client.close()
