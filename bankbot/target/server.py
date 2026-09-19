"""Serve the app from a daemon thread so tests and the CLI can start it in-process.

Owns: picking a free port, starting uvicorn in a thread, blocking until the
socket accepts, and stopping it. Does not own: the app (app.py). Governed by
ADR-0006.
"""

import socket
import threading
import time

import uvicorn
from fastapi import FastAPI

LOOPBACK = "127.0.0.1"
START_TIMEOUT_S = 10.0
STOP_TIMEOUT_S = 5.0
POLL_INTERVAL_S = 0.01


class ServerDidNotStart(RuntimeError):
    """uvicorn's thread ended, or never reported ready, within the start timeout."""


class ServerHandle:
    """A running server: where it listens and how to stop it."""

    def __init__(self, base_url: str, server: uvicorn.Server, thread: threading.Thread) -> None:
        self.base_url = base_url
        self._server = server
        self._thread = thread

    def stop(self) -> None:
        """Ask uvicorn to exit and wait for the thread, so the port is free for the next test."""
        self._server.should_exit = True
        self._thread.join(timeout=STOP_TIMEOUT_S)


def start_server(app: FastAPI, port: int = 0) -> ServerHandle:
    """Start the app on the loopback interface and return once it accepts connections.

    I bind the socket myself and hand it to uvicorn. Asking the OS for port 0
    and then telling uvicorn the number would leave a window where another
    process takes it; passing the bound socket closes that window.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((LOOPBACK, port))
    bound_port: int = sock.getsockname()[1]

    config = uvicorn.Config(app, host=LOOPBACK, port=bound_port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [sock]},
        name=f"bankbot-target-{bound_port}",
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + START_TIMEOUT_S
    while not server.started:
        if not thread.is_alive():
            raise ServerDidNotStart("uvicorn thread exited before reporting ready")
        if time.monotonic() > deadline:
            raise ServerDidNotStart(f"uvicorn did not report ready within {START_TIMEOUT_S}s")
        time.sleep(POLL_INTERVAL_S)

    return ServerHandle(base_url=f"http://{LOOPBACK}:{bound_port}", server=server, thread=thread)
