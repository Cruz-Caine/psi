"""Jev client — the only remote call (ARCHITECTURE A5/D9).

Backend-pluggable (typesafe | openrouter, same schema). Retries with
backoff; after exhausting retries it raises OutageError so the policy
layer can apply the on_outage mode. Transport is injectable for tests.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class OutageError(RuntimeError):
    """Jev unreachable after retries — caller applies on_outage policy."""


def post_systemone(url: str, api_key: str, state, questions: dict, *, timeout: float = 60.0) -> dict:
    body = {"state": state, "model": "jev-latest", "questions": questions}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class JevClient:
    def __init__(self, *, url: str, api_key: str, retries: int = 3, transport=post_systemone):
        self.url = url
        self.api_key = api_key
        self.retries = retries
        self._transport = transport

    def ask(self, state, questions: dict) -> dict:
        """Returns the raw response dict (answers under ['answers'])."""
        if not self.api_key:
            raise OutageError("no API key configured for Jev backend")
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                return self._transport(self.url, self.api_key, state, questions)
            except urllib.error.HTTPError as e:
                # deterministic client errors: retrying cannot help; surface
                # the API's error body so the failing call shape is diagnosable
                # (smoke-skrub: 28 opaque "HTTP Error 400" outages in one run)
                body = ""
                try:
                    body = e.read().decode(errors="replace")[:300]
                except Exception:
                    pass
                if e.code in (400, 401, 403, 404, 422):
                    raise OutageError(f"Jev HTTP {e.code}: {body or e.reason}")
                last = e
                if attempt < self.retries - 1:
                    time.sleep(0.5 * (2**attempt))
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = e
                if attempt < self.retries - 1:
                    time.sleep(0.5 * (2**attempt))
        raise OutageError(f"Jev unreachable after {self.retries} attempts: {last}")