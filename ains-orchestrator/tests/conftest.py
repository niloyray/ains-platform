from __future__ import annotations

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def shipped_root() -> Path:
    """The real ains-orchestrator directory with the shipped YAML files."""
    return ROOT


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    """A copy of the shipped config tree that tests may mutate."""
    for sub in ("flows", "agents"):
        shutil.copytree(ROOT / sub, tmp_path / sub)
    shutil.copy(ROOT / "targets.yaml", tmp_path / "targets.yaml")
    stages = tmp_path / "agents" / "stages"
    stages.mkdir(exist_ok=True)
    for s in ("spec", "plan", "implement", "review"):
        p = stages / f"{s}.md"
        if not p.exists():
            p.write_text(f"# {s} stage prompt (test stub)\n", encoding="utf-8")
    return tmp_path
