"""Talking to Maestro, and holding the token where Windows keeps secrets.

Every call runs on a worker thread and reports back through a Qt signal, so a
slow endpoint can never freeze the strip.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

import keyring                      # Windows Credential Manager
import requests

SERVICE_ACCOUNT = "maestro"


def read_token(service: str) -> str | None:
    try:
        return keyring.get_password(service, SERVICE_ACCOUNT)
    except Exception:               # noqa: BLE001 — a broken vault must not crash the app
        return None


def write_token(service: str, value: str) -> bool:
    try:
        keyring.set_password(service, SERVICE_ACCOUNT, value)
        return True
    except Exception:               # noqa: BLE001
        return False


class Api:
    def __init__(self, base: str = "", token_service: str = "maestro-token"):
        self.base = base.rstrip("/")
        self.token_service = token_service

    def update(self, base: str, token_service: str) -> None:
        self.base = (base or "").rstrip("/")
        self.token_service = token_service

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        tok = read_token(self.token_service)
        if tok:
            h["Authorization"] = "Bearer " + tok
        return h

    def call(self, method: str, path: str, body: dict | None,
             done: Callable[[Any, int], None]) -> None:
        """Fire and forget. `done(json_or_none, status_code)` runs on the worker
        thread, so the caller marshals back to the UI thread itself."""
        if not self.base:
            done(None, 0)
            return

        def work() -> None:
            try:
                r = requests.request(
                    method.upper(), self.base + path,
                    headers=self._headers(),
                    data=None if method.upper() == "GET" else json.dumps(body or {}),
                    timeout=20)
                try:
                    payload = r.json()
                except ValueError:
                    payload = None
                done(payload, r.status_code)
            except requests.RequestException:
                done(None, 0)

        threading.Thread(target=work, daemon=True).start()

    def get(self, path: str, done: Callable[[Any, int], None]) -> None:
        self.call("GET", path, None, done)

    def post(self, path: str, body: dict | None, done: Callable[[Any, int], None]) -> None:
        self.call("POST", path, body, done)


def rows(payload: Any) -> list[dict]:
    """A list endpoint may answer with a bare array or wrap it. Read both."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("items", "data", "results", "rows", "actions", "tasks",
                    "proposals", "cards"):
            v = payload.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    return []


def field(row: dict, keys: list[str]) -> str:
    for k in keys:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, (int, float)):
            return str(v)
    return ""


def row_id(row: dict) -> str:
    for k in ("id", "gmail_id", "action_id", "task_id", "uuid", "key"):
        v = row.get(k)
        if isinstance(v, (str, int)):
            return str(v)
    return ""


def substitute(path: str, row: dict) -> str:
    out = path.replace("{id}", row_id(row))
    for k, v in row.items():
        if isinstance(v, (str, int)):
            out = out.replace("{" + k + "}", str(v))
    return out
