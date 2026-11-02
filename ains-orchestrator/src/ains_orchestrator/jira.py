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

    def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str,
        labels: list[str] | None = None,
        issue_type: str = "Story",
    ) -> str:
        """Create a story and return its key (used by the acceptance run and the live tests)."""
        payload = {
            "fields": {
                "project": {"key": project_key},
                "summary": summary,
                "description": _adf(description),
                "issuetype": {"name": issue_type},
                "labels": labels or [],
            }
        }
        if self.dry_run:
            self._dry(f"create issue in {project_key}: {summary!r}")
            return f"{project_key}-0"
        r = self._request("POST", "/rest/api/3/issue", json=payload)
        self._check(r, f"create issue in {project_key}")
        return str(r.json()["key"])

    # ------------------------------------------------------------------ projects (onboarding)
    def project_exists(self, key: str) -> bool:
        r = self._request("GET", f"/rest/api/3/project/{key}")
        if r.status_code == 404:
            return False
        self._check(r, f"project {key}")
        return True

    def create_project(self, key: str, name: str, template_key: str) -> dict[str, Any]:
        lead = self.myself()["accountId"]
        payload = {
            "key": key,
            "name": name,
            "projectTypeKey": "software",
            "projectTemplateKey": template_key,
            "leadAccountId": lead,
            "assigneeType": "UNASSIGNED",
        }
        if self.dry_run:
            self._dry(f"create project {key} {name!r}")
            return {}
        r = self._request("POST", "/rest/api/3/project", json=payload)
        self._check(r, f"create project {key}")
        return r.json()

    def project_statuses(self, key: str) -> list[str]:
        r = self._request("GET", f"/rest/api/3/project/{key}/statuses")
        self._check(r, f"statuses {key}")
        names: list[str] = []
        for issue_type in r.json():
            for st in issue_type.get("statuses", []):
                if st["name"] not in names:
                    names.append(st["name"])
        return names

    def ensure_statuses(self, key: str, wanted: list[tuple[str, str]]) -> list[str]:
        """Add missing statuses to a team-managed project's workflow (new statuses + workflows APIs).

        `wanted` is a list of (status name, category) with category in TODO, IN_PROGRESS, DONE.
        Every added status gets a GLOBAL transition, which is how team-managed workflows allow any move.
        Returns the names that were added. Idempotent.
        """
        import uuid

        project = self.get_project(key)
        pid = project["id"]
        issue_type = next(t["id"] for t in project["issueTypes"] if t["name"] != "Subtask")
        r = self._request("GET", "/rest/api/3/statuses/search", params={"projectId": pid, "maxResults": 200})
        self._check(r, f"statuses search {key}")
        by_name = {st["name"]: st for st in r.json().get("values", [])}
        missing = [(n, cat) for n, cat in wanted if n not in by_name]
        if missing:
            if self.dry_run:
                self._dry(f"create statuses in {key}: {[n for n, _ in missing]}")
            else:
                body = {
                    "scope": {"type": "PROJECT", "project": {"id": pid}},
                    "statuses": [{"name": n, "statusCategory": cat} for n, cat in missing],
                }
                r = self._request("POST", "/rest/api/3/statuses", json=body)
                self._check(r, f"create statuses {key}")
                for st in r.json():
                    by_name[st["name"]] = st
        r = self._request(
            "POST",
            "/rest/api/3/workflows",
            json={"projectAndIssueTypes": [{"projectId": pid, "issueTypeId": issue_type}]},
        )
        self._check(r, f"workflows {key}")
        data = r.json()
        if not data.get("workflows"):
            raise JiraError(f"{key}: no project workflow returned (company-managed project?)")
        wf = data["workflows"][0]
        details = {st["id"]: st for st in data.get("statuses", [])}
        have = {st["statusReference"] for st in wf["statuses"]}
        new: list[tuple[str, str]] = []
        for n, _cat in wanted:
            st = by_name.get(n)
            if st and st["id"] not in have:
                new.append((n, st["id"]))
                details[st["id"]] = {"id": st["id"], "name": n, "statusCategory": st["statusCategory"]}
        if not new:
            return []
        if self.dry_run:
            self._dry(f"add statuses to workflow of {key}: {[n for n, _ in new]}")
            return [n for n, _ in new]
        ref: dict[str, str] = {}

        def uref(sid: str) -> str:
            return ref.setdefault(sid, str(uuid.uuid4()))

        statuses = [
            {
                "statusReference": uref(st["statusReference"]),
                "layout": st.get("layout"),
                "properties": st.get("properties", {}),
            }
            for st in wf["statuses"]
        ]
        transitions = []
        for t in wf["transitions"]:
            t2 = dict(t)
            if t2.get("toStatusReference"):
                t2["toStatusReference"] = uref(t2["toStatusReference"])
            if t2.get("from"):
                t2["from"] = [dict(f, statusReference=uref(f["statusReference"])) for f in t2["from"]]
            transitions.append(t2)
        next_id = max(int(t["id"]) for t in wf["transitions"]) + 10
        for n, sid in new:
            statuses.append({"statusReference": uref(sid), "layout": None, "properties": {}})
            transitions.append(
                {
                    "id": str(next_id),
                    "name": n,
                    "type": "GLOBAL",
                    "toStatusReference": uref(sid),
                    "actions": [],
                    "validators": [],
                    "triggers": [],
                    "conditions": {"operation": "ALL", "conditions": []},
                    "properties": {},
                    "links": [],
                }
            )
            next_id += 10
        payload = {
            "statuses": [
                {"id": sid, "statusReference": uref(sid), "name": d["name"], "statusCategory": d["statusCategory"]}
                for sid, d in details.items()
            ],
            "workflows": [
                {
                    "id": wf["id"],
                    "version": wf["version"],
                    "statuses": statuses,
                    "transitions": transitions,
                    "defaultStatusMappings": [],
                    "statusMappings": [],
                }
            ],
        }
        v = self._request(
            "POST",
            "/rest/api/3/workflows/update/validation",
            json={"payload": payload, "validationOptions": {"levels": ["ERROR"]}},
        )
        self._check(v, f"workflow validation {key}")
        errors = v.json().get("errors") or []
        if errors:
            raise JiraError(f"{key}: workflow update rejected: {errors[0].get('message')}")
        u = self._request("POST", "/rest/api/3/workflows/update", json=payload)
        self._check(u, f"workflow update {key}")
        return [n for n, _ in new]

    def get_project(self, key: str) -> dict[str, Any]:
        r = self._request("GET", f"/rest/api/3/project/{key}")
        self._check(r, f"project {key}")
        return r.json()

    def delete_project(self, key: str) -> None:
        if self.dry_run:
            self._dry(f"delete project {key}")
            return
        r = self._request("DELETE", f"/rest/api/3/project/{key}", params={"enableUndo": "true"})
        self._check(r, f"delete project {key}")

    def _dry(self, msg: str) -> None:
        self.dry_run_log.append(msg)
        log.info("[dry-run] jira: %s", msg)

    def close(self) -> None:
        self._client.close()
