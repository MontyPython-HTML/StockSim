import asyncio
import atexit
import json
import logging
import sys
import threading
from contextlib import AsyncExitStack
from datetime import date
from pathlib import Path

from mcp import Client, StdioServerParameters

SRC_DIR = Path(__file__).resolve().parents[2]
CALL_TIMEOUT_SECONDS = 45
STARTUP_TIMEOUT_SECONDS = 30

log = logging.getLogger(__name__)


class MCPUnavailable(RuntimeError):
    pass


class MCPClientThread:
    def __init__(self) -> None:
        self.alive = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Client | None = None
        self._stack: AsyncExitStack | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mcp-client", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=STARTUP_TIMEOUT_SECONDS):
            raise MCPUnavailable("MCP server did not start in time")
        if self._error:
            raise MCPUnavailable(f"MCP server failed to start: {self._error}")

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect())
        except BaseException as exc:
            self._error = exc
            self._ready.set()
            return
        self.alive = True
        self._ready.set()
        self._loop.run_forever()

    async def _connect(self) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.server"],
            cwd=str(SRC_DIR),
        )
        self._stack = AsyncExitStack()
        self._client = await self._stack.enter_async_context(Client(params))
        await self._client.list_tools()

    def call_tool(self, name: str, arguments: dict) -> dict:
        if self._client is None or self._loop is None:
            raise MCPUnavailable("MCP client is not connected")
        future = asyncio.run_coroutine_threadsafe(
            self._client.call_tool(name, arguments), self._loop
        )
        try:
            result = future.result(timeout=CALL_TIMEOUT_SECONDS)
        except Exception:
            # Transport-level failure means the subprocess is gone; let the next call respawn it.
            self.alive = False
            raise
        if result.is_error:
            raise MCPUnavailable(f"tool {name} failed: {result.content}")
        if result.structured_content:
            return result.structured_content
        return json.loads(result.content[0].text)

    def shutdown(self, graceful: bool = True) -> None:
        was_alive, self.alive = self.alive, False
        if self._loop is None or self._stack is None:
            return
        # A dead subprocess never finishes aclose(), so skip it and just drop the loop.
        if graceful and was_alive:
            try:
                asyncio.run_coroutine_threadsafe(self._stack.aclose(), self._loop).result(timeout=5)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)


_client: MCPClientThread | None = None
_client_lock = threading.Lock()
_startup_failed = False


def get_client() -> MCPClientThread:
    global _client, _startup_failed
    if _client is not None and not _client.alive:
        with _client_lock:
            if _client is not None and not _client.alive:
                _client.shutdown(graceful=False)
                _client = None
                _startup_failed = False
    if _client is None:
        with _client_lock:
            if _client is None:
                if _startup_failed:
                    raise MCPUnavailable("MCP server previously failed to start")
                candidate = MCPClientThread()
                try:
                    candidate.start()
                except Exception:
                    _startup_failed = True
                    raise
                _client = candidate
                atexit.register(candidate.shutdown)
    return _client


def is_available() -> bool:
    try:
        get_client()
        return True
    except Exception:
        return False


def _call_and_log(tool: str, event_type: str, session_id: str, ticker: str, as_of: date):
    client = get_client()
    payload = client.call_tool(
        tool,
        {"session_id": session_id, "ticker": ticker, "as_of_date": as_of.isoformat()},
    )
    if payload.get("error"):
        return None
    client.call_tool(
        "log_ai_event",
        {
            "session_id": session_id,
            "ticker": ticker,
            "sim_date": as_of.isoformat(),
            "event_type": event_type,
            "payload": payload,
        },
    )
    return payload


def predict(session_id: str, ticker: str, as_of: date) -> dict | None:
    try:
        return _call_and_log("predict_next_move", "PREDICTION", session_id, ticker, as_of)
    except Exception as exc:
        log.warning("MCP prediction unavailable: %s: %s", type(exc).__name__, exc)
        return None


def market_event(session_id: str, ticker: str, as_of: date) -> dict | None:
    try:
        return _call_and_log("generate_market_event", "NEWS_EVENT", session_id, ticker, as_of)
    except Exception as exc:
        log.warning("MCP market event unavailable: %s: %s", type(exc).__name__, exc)
        return None
