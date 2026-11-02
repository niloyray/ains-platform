"""T-I5-1 to T-I5-4."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ains_orchestrator.errors import StoreError
from ains_orchestrator.store import Store


def test_T_I5_1_state_survives_reload(tmp_path: Path):
    p = tmp_path / "work" / "sessions.sqlite"
    s = Store(p)
    s.create_session("AINSSB-1", "AINSSB", "niloyray/ains-sandbox", "story", "spec")
    s.update_session("AINSSB-1", stage="wait_g1", gate_state={"G1": "waiting"}, branch="AINSSB-1-scaffold", pr_number=7)
    s.close()
    s2 = Store(p)
    sess = s2.get_session("AINSSB-1")
    assert sess and sess.stage == "wait_g1" and sess.gate_state == {"G1": "waiting"} and sess.pr_number == 7
    assert [e.kind for e in s2.events("AINSSB-1")] == ["session.created"]


def test_T_I5_2_upsert_same_key_once(tmp_path: Path):
    s = Store(tmp_path / "s.sqlite")
    s.create_session("AINSSB-1", "AINSSB", "r", "story", "spec")
    s.create_session("AINSSB-1", "AINSSB", "r", "story", "spec")
    assert len(s.list_sessions()) == 1
    assert s.active_keys() == {"AINSSB-1"}
    s.update_session("AINSSB-1", stage="released")
    assert s.active_keys() == set()


def test_T_I5_3_many_events_ordered_and_fast(tmp_path: Path):
    s = Store(tmp_path / "s.sqlite")
    s.create_session("AINSSB-1", "AINSSB", "r", "story", "spec")
    for i in range(1000):
        s.add_event("AINSSB-1", "agent.turn", {"i": i})
    t0 = time.perf_counter()
    ev = s.events("AINSSB-1")
    assert (time.perf_counter() - t0) < 0.5
    assert [e.detail.get("i") for e in ev[1:]] == list(range(1000))
    assert s.export("AINSSB-1")["session"]["key"] == "AINSSB-1"


def test_T_I5_4_unwritable_path_fails_at_startup(tmp_path: Path):
    f = tmp_path / "not-a-dir"
    f.write_text("x")
    with pytest.raises(StoreError, match="cannot open session store"):
        Store(f / "sessions.sqlite")


def test_unknown_field_rejected(tmp_path: Path):
    s = Store(tmp_path / "s.sqlite")
    s.create_session("AINSSB-1", "AINSSB", "r", "story", "spec")
    with pytest.raises(StoreError, match="unknown session fields"):
        s.update_session("AINSSB-1", colour="red")
    with pytest.raises(StoreError, match="no session"):
        s.update_session("NOPE-1", stage="x")
