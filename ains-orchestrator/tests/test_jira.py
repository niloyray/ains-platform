"""T-I3-1 to T-I3-5, T-I3-7 (offline, via httpx MockTransport). T-I3-6 is the live test."""

from __future__ import annotations

import json
import logging
import os

import httpx
import pytest

from ains_orchestrator.errors import JiraError, TransitionUnavailable
from ains_orchestrator.jira import JiraClient

BASE = "https://example.atlassian.net"
TOKEN = "ATATT3xFfGF0-fake-token-value-for-tests-only"


def make_client(handler, **kw) -> JiraClient:
    return JiraClient(BASE, "me@example.com", TOKEN, transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw)


def test_T_I3_1_search_follows_pagination():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append(body)
        assert req.url.path == "/rest/api/3/search/jql"
        if "nextPageToken" not in body:
            return httpx.Response(200, json={"issues": [{"key": "AINSSB-1"}], "nextPageToken": "p2", "isLast": False})
        return httpx.Response(200, json={"issues": [{"key": "AINSSB-2"}], "isLast": True})

    issues = make_client(handler).search("project = AINSSB")
    assert [i["key"] for i in issues] == ["AINSSB-1", "AINSSB-2"]
    assert len(calls) == 2 and calls[1]["nextPageToken"] == "p2"


def test_T_I3_2_transition_by_name_posts_id():
    posted = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            trs = [{"id": "31", "name": "Specifying"}, {"id": "41", "name": "Blocked"}]
            return httpx.Response(200, json={"transitions": trs})
        posted.update(json.loads(req.content))
        return httpx.Response(204)

    make_client(handler).transition("AINSSB-1", "Specifying")
    assert posted == {"transition": {"id": "31"}}


def test_T_I3_3_unavailable_transition_lists_available():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"transitions": [{"id": "31", "name": "Specifying"}]})

    with pytest.raises(TransitionUnavailable) as ei:
        make_client(handler).transition("AINSSB-1", "Done")
    assert ei.value.available == ["Specifying"] and "Done" in str(ei.value)


def test_T_I3_4_retries_on_429_then_succeeds():
    n = {"calls": 0}
    slept = []

    def handler(req: httpx.Request) -> httpx.Response:
        n["calls"] += 1
        if n["calls"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"accountId": "x", "displayName": "Test"})

    c = JiraClient(BASE, "me@example.com", TOKEN, transport=httpx.MockTransport(handler), sleep=slept.append)
    assert c.myself()["displayName"] == "Test"
    assert n["calls"] == 2 and slept == [7.0]


def test_T_I3_5_dry_run_makes_no_write_calls():
    writes = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method != "GET":
            writes.append(req.url.path)
        return httpx.Response(200, json={"transitions": [{"id": "31", "name": "Specifying"}]})

    c = make_client(handler, dry_run=True)
    c.transition("AINSSB-1", "Specifying")
    assert c.add_comment("AINSSB-1", "hello") is None
    c.add_remote_link("AINSSB-1", "https://github.com/x/y/pull/1", "PR")
    assert writes == [] and len(c.dry_run_log) == 3


def test_T_I3_7_token_never_in_logs_or_errors(caplog):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    caplog.set_level(logging.DEBUG)
    with pytest.raises(JiraError) as ei:
        JiraClient(BASE, "me@example.com", TOKEN, transport=httpx.MockTransport(handler), sleep=lambda s: None).myself()
    assert TOKEN not in str(ei.value) and TOKEN not in caplog.text


def test_missing_credentials_rejected():
    with pytest.raises(JiraError):
        JiraClient(BASE, "", TOKEN)


@pytest.mark.live
def test_T_I3_6_live_smoke():
    base, email, token = os.environ.get("JIRA_BASE_URL"), os.environ.get("JIRA_EMAIL"), os.environ.get("JIRA_API_TOKEN")
    key = os.environ.get("AINS_SANDBOX_ISSUE")
    if not (os.environ.get("AINS_LIVE") == "1" and base and email and token and key):
        pytest.skip("AINS_LIVE=1 with Jira credentials and AINS_SANDBOX_ISSUE required")
    c = JiraClient(base, email, token)
    assert c.myself()["accountId"]
    assert isinstance(c.search(f"key = {key}"), list)
    cid = c.add_comment(key, "ains live smoke test T-I3-6 (safe to delete)")
    assert cid
