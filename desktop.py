"""Entry point for the Crossplay desktop app: starts the FastAPI backend on a
free local port and opens it in a native window via pywebview - no browser
tab, no address bar.
"""

import socket
import threading
import time

import uvicorn
import webview

from app.job_manager import job_manager
from app.server import app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_accepting(port: int, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.1)
    raise RuntimeError("Backend server did not start in time")


def main():
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_accepting(port)

    window = webview.create_window(
        "Crossplay",
        f"http://127.0.0.1:{port}",
        width=1280,
        height=920,
        min_size=(900, 700),
    )

    def on_closed():
        job_manager.shutdown()
        server.should_exit = True

    window.events.closed += on_closed

    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()
