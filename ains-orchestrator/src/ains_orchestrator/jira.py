"""I3: minimal Jira Cloud client (REST v3) for the orchestrator.

Basic auth with email + API token. Retries on 429 and 5xx. Never logs the token.
`dry_run=True` logs every write instead of sending it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from ains_orchestrator.errors import JiraError, TransitionUnavailable

log = logging.getLogger("ains.jira")
RETRY_STATUSES = {429, 500, 502, 503, 504}


def _adf(text: str) -> dict[str, Any]:
    """Atlassian Document Format for a plain-text paragraph per line."""
    paragraphs = [
        {"type": "paragraph", "content": [{"type": "text", "text": line}] if line else []} for line in text.split("\n")
    ]
    return {"type": "doc", "version": 1, "content": paragraphs}


class JiraClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        *,
        dry_run: bool = False,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
    ) -> None:
        if not (base_url and email and token):
            raise JiraError("JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN are all required")
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._client = httpx.Client(
            base_url=self.base_url,
            auth=(email, token),
            timeout=30,
            transport=transport,
            headers={"Accept": "application/json"},
        )
        self.dry_run_log: list[str] = []

    # ------------------------------------------------------------------ transport
    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        last: httpx.Response | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                r = self._client.request(method, path, **kw)
            except httpx.HTTPError as e:
                if attempt == self._max_attempts:
                    raise JiraError(f"{method} {path}: network error: {type(e).__name__}") from e
                self._sleep(2 * attempt)
                continue
            if r.status_code in RETRY_STATUSES and attempt < self._max_attempts:
                delay = float(r.headers.get("Retry-After", 2 * attempt))
                log.warning("jira %s %s -> %s, retry in %ss", method, path, r.status_code, delay)
                self._sleep(delay)
                last = r
                continue
            return r
        assert last is not None
        return last

    def _check(self, r: httpx.Response, what: str) -> None:
        if r.status_code >= 400:
            raise JiraError(f"{what}: HTTP {r.status_code}: {r.text[:300]}", r.status_code)

    # ------------------------------------------------------------------ reads
    def myself(self) -> dict[str, Any]:
        r = self._request("GET", "/rest/api/3/myself")
        self._check(r, "myself")
        return r.json()

    def search(self, jql: str, fields: list[str] | None = None, max_results: int = 50) -> list[dict[str, Any]]:
        """Run a JQL query and follow pagination (search/jql endpoint, nextPageToken)."""
        issues: list[dict[str, Any]] = []
        body: dict[str, Any] = {"jql": jql, "maxResults": max_results, "fields": fields or ["summary", "status"]}
        while True:
            r = self._request("POST", "/rest/api/3/search/jql", json=body)
            self._check(r, f"search {jql!r}")
            data = r.json()
            issues.extend(data.get("issues", []))
            token = data.get("nextPageToken")
            if not token or data.get("isLast", not token):
                break
            body["nextPageToken"] = token
        return issues

    def get_issue(self, key: str, fields: list[str] | None = None) -> dict[str, Any]:
        params = {"fields": ",".join(fields)} if fields else None
        r = self._request("GET", f"/rest/api/3/issue/{key}", params=params)
        self._check(r, f"get {key}")
        return r.json()

    def transitions(self, key: str) -> dict[str, str]:
        """Available transitions as {name: id}."""
        r = self._request("GET", f"/rest/api/3/issue/{key}/transitions")
        self._check(r, f"transitions {key}")
        return {t["name"]: str(t["id"]) for t in r.json().get("transitions", [])}

    def status_of(self, key: str) -> str:
        return self.get_issue(key, ["status"])["fields"]["status"]["name"]

    # ------------------------------------------------------------------ writes
    def transition(self, key: str, name: str) -> None:
        available = self.transitions(key)
        if name not in available:
            raise TransitionUnavailable(key, name, sorted(available))
        if self.dry_run:
            self._dry(f"transition {key} -> {name}")
            return
        r = self._request("POST", f"/rest/api/3/issue/{key}/transitions", json={"transition": {"id": available[name]}})
        self._check(r, f"transition {key} -> {name}")

    def add_comment(self, key: str, text: str) -> str | None:
        if self.dry_run:
            self._dry(f"comment {key}: {text[:80]!r}")
            return None
        r = self._request("POST", f"/rest/api/3/issue/{key}/comment", json={"body": _adf(text)})
        self._check(r, f"comment {key}")
        return str(r.json().get("id"))

    def add_remote_link(self, key: str, url: str, title: str) -> None:
        if self.dry_run:
            self._dry(f"remote link {key}: {title} -> {url}")
            return
        payload = {"globalId": url, "object": {"url": url, "title": title}}
        r = self._request("POST", f"/rest/api/3/issue/{key}/remotelink", json=payload)
        self._check(r, f"remote link {key}")

    def _dry(self, msg: str) -> None:
        self.dry_run_log.append(msg)
        log.info("[dry-run] jira: %s", msg)

    def close(self) -> None:
        self._client.close()
