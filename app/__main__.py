"""Start ClipForge: python -m app [--no-browser]"""
from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from . import jobs
from .config import CONFIG

HOST = "127.0.0.1"  # local only, never exposed on the network


def _open_when_ready(url: str, port: int) -> None:
    for _ in range(100):
        try:
            with socket.create_connection((HOST, port), timeout=0.5):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.2)


def _quiet_connection_resets() -> None:
    """Windows + Python 3.12: when the browser drops a connection (seeking in a video, closing the
    tab), asyncio's cleanup calls socket.shutdown() on the dead socket and prints a scary
    ConnectionResetError traceback. Same cleanup, with that one call allowed to fail."""
    if sys.platform != "win32":
        return
    from asyncio import proactor_events

    def _call_connection_lost(self, exc):
        if self._called_connection_lost:
            return
        try:
            self._protocol.connection_lost(exc)
        finally:
            if hasattr(self._sock, "shutdown") and self._sock.fileno() != -1:
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # the other side is already gone
            self._sock.close()
            self._sock = None
            server = self._server
            if server is not None:
                server._detach()
                self._server = None
            self._called_connection_lost = True

    proactor_events._ProactorBasePipeTransport._call_connection_lost = _call_connection_lost


def main() -> None:
    _quiet_connection_resets()
    port = int(CONFIG["server"]["port"])
    url = f"http://{HOST}:{port}"
    browser = CONFIG["server"]["open_browser"] and "--no-browser" not in sys.argv
    try:
        jobs.acquire_lock()
    except jobs.AlreadyRunning as exc:
        print(exc)
        if browser:
            webbrowser.open(url)  # most likely the user just wants the open app
        sys.exit(1)
    print(f"ClipForge running at {url}  (Ctrl+C to stop)")
    if browser:
        threading.Thread(target=_open_when_ready, args=(url, port), daemon=True).start()
    uvicorn.run("app.main:app", host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()
