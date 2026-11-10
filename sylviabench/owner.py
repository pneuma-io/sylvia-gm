"""Headless client for Sylvia's ordinary owner protocol, with stream attachment."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from sylvia_gm.infrastructure.launch_handshake import LaunchHandshake
from sylvia_gm.interfaces.owner_process import run


class OwnerError(RuntimeError):
    """An explicit owner rejection, retaining its safe protocol payload."""

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(str(error.get("message", "Owner command failed")))
        self.error = error


class OwnerClient:
    """Own one isolated database through the same protocol as the desktop."""

    def __init__(self, database: Path, resolver: Any, *, timeout: float = 120) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._lock = threading.Lock()
        self._pending: dict[str, Any] = {}
        self._replies: queue.Queue[Any] = queue.Queue()
        owner_read, client_write = os.pipe()
        client_read, owner_write = os.pipe()
        self._input = os.fdopen(owner_read, "r", encoding="utf-8")
        self._output = os.fdopen(owner_write, "w", encoding="utf-8", buffering=1)
        self._send_stream = os.fdopen(client_write, "w", encoding="utf-8", buffering=1)
        self._read_stream = os.fdopen(client_read, "r", encoding="utf-8")

        def serve() -> None:
            try:
                run(self._input, self._output, provider_resolver=resolver)
            except BaseException as error:
                self._replies.put(error)
            finally:
                self._output.close()

        def read() -> None:
            try:
                for line in self._read_stream:
                    self._replies.put(json.loads(line))
            except BaseException as error:
                self._replies.put(error)
            finally:
                self._replies.put(EOFError("Owner transport closed"))

        self._thread = threading.Thread(target=serve, daemon=True)
        self._reader = threading.Thread(target=read, daemon=True)
        self._thread.start()
        self._reader.start()
        handshake = LaunchHandshake()
        credential = handshake.issue()
        self._send(
            {
                "type": "launch",
                "secret_hex": credential.secret_hex,
                "database_path": str(database),
                "owner_id": "sylviabench",
            }
        )
        ready = self._receive()
        if not ready.get("ok"):
            self.close()
            raise OwnerError(ready.get("error", {}))
        handshake.verify(ready["response_hex"])

    def _send(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._send_stream.write(json.dumps(payload) + "\n")
            self._send_stream.flush()

    def _receive(self) -> Any:
        try:
            reply = self._replies.get(timeout=self.timeout)
        except queue.Empty as error:
            raise TimeoutError("Owner did not reply within the configured timeout") from error
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        campaign: str | None = None,
        attempt: str | None = None,
    ) -> Any:
        """Keep an active GM request attached until its ordinary owner reply arrives."""
        identity = uuid.uuid4().hex
        self._send({"id": identity, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        heartbeat = time.monotonic()
        generation = None
        heartbeat_ids: set[str] = set()
        while True:
            if campaign and attempt and time.monotonic() >= heartbeat:
                heartbeat_id = identity + "-heartbeat-" + str(len(heartbeat_ids))
                heartbeat_ids.add(heartbeat_id)
                attachment: dict[str, Any] = {"campaign_id": campaign, "attempt_id": attempt}
                if generation is not None:
                    attachment["attachment_generation"] = generation
                self._send(
                    {
                        "id": heartbeat_id,
                        "method": "stream_heartbeat",
                        "params": attachment,
                    }
                )
                heartbeat = time.monotonic() + 1
            try:
                reply = self._replies.get(timeout=min(0.2, max(0.001, deadline - time.monotonic())))
            except queue.Empty:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Owner command exceeded its configured timeout") from None
                continue
            if isinstance(reply, BaseException):
                raise reply
            if reply.get("id") != identity:
                if reply.get("id") in heartbeat_ids and reply.get("ok"):
                    generation = reply["result"]["attachment_generation"]
                continue
            if not reply["ok"]:
                raise OwnerError(reply["error"])
            return reply["result"]

    def close(self) -> None:
        """Stop this client's owner and release its file handles before copying a save."""
        if self._thread.is_alive():
            try:
                self.request("shutdown")
            finally:
                self._thread.join(timeout=15)
        if self._thread.is_alive():
            raise RuntimeError("Benchmark owner did not shut down; database is still in use")
        self._reader.join(timeout=5)
        for stream in (self._send_stream, self._read_stream, self._input, self._output):
            if not stream.closed:
                stream.close()

    def __enter__(self) -> OwnerClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
