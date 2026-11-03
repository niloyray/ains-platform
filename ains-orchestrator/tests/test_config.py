"""T-I2-1 to T-I2-6."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ains_orchestrator.config import (
    AgentTemplate,
    export_schemas,
    load_agent,
    load_config,
    load_flow,
    load_targets,
)
from ains_orchestrator.errors import ConfigError

SANDBOX = """
targets:
  AINSSB:
    repo: niloyray/ains-sandbox
    default_branch: main
    flow: story
    gates: {G1: on, G2: on, G3: on, G4: on, G5: on}
    review_label: ai-review
"""


def test_T_I2_1_shipped_files_validate(config_root: Path, shipped_root: Path):
    cfg = load_config(config_root)
    assert set(cfg.targets.targets) <= {"AINSSB"}, "only the permanent sandbox may ship in targets.yaml (D16)"
    assert "story" in cfg.flows and cfg.flows["story"].stages == ["spec", "plan", "implement", "review", "merge"]
    assert "claude-code" in cfg.agents
    (config_root / "targets.yaml").write_text(SANDBOX, encoding="utf-8")
    cfg = load_config(config_root)
    assert cfg.targets.targets["AINSSB"].repo == "niloyray/ains-sandbox"
    assert cfg.targets.targets["AINSSB"].gates["G3"] == "on"


def test_T_I2_2_unknown_gate_rejected_with_file_line_and_allowed(tmp_path: Path):
    p = tmp_path / "targets.yaml"
    p.write_text("targets:\n  AINSSB:\n    repo: niloyray/ains-sandbox\n    gates: {G9: on}\n", encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        load_targets(p)
    msg = str(ei.value)
    assert "targets.yaml" in msg and "G9" in msg and "G1, G2, G3, G4, G5" in msg
    assert ei.value.line == 4


def test_T_I2_3_gate_after_unknown_stage(tmp_path: Path):
    p = tmp_path / "f.yaml"
    p.write_text("name: x\nstages: [spec]\ngates:\n  G1: {after: nope, where: jira}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="neither a stage nor a gate"):
        load_flow(p)


def test_T_I2_4_agent_without_passthrough_rejected(tmp_path: Path):
    p = tmp_path / "a.yaml"
    p.write_text("name: a\ncommand: [claude, -p]\nstages: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="env_passthrough"):
        load_agent(p)
    p.write_text("name: a\ncommand: [claude, -p]\nenv_passthrough: []\nstages: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="explicit and non-empty"):
        load_agent(p)


@pytest.mark.parametrize("var", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_FOO"])
def test_T_I2_5_anthropic_variables_forbidden(tmp_path: Path, var: str):
    p = tmp_path / "a.yaml"
    p.write_text(
        f"name: a\ncommand: [claude, -p]\nenv_passthrough: [CLAUDE_CODE_OAUTH_TOKEN, {var}]\nstages: {{}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="forbidden variable"):
        load_agent(p)


def test_bare_mode_forbidden():
    with pytest.raises(ValueError, match="--bare"):
        AgentTemplate(
            name="a", command=["claude", "-p", "--bare"], env_passthrough=["CLAUDE_CODE_OAUTH_TOKEN"], stages={}
        )
    with pytest.raises(ValueError, match="non-interactively"):
        AgentTemplate(name="a", command=["claude"], env_passthrough=["CLAUDE_CODE_OAUTH_TOKEN"], stages={})


def test_missing_prompt_file_detected(config_root: Path):
    (config_root / "agents" / "stages" / "review.md").unlink()
    with pytest.raises(ConfigError, match="prompt file agents/stages/review.md missing"):
        load_config(config_root)


def test_T_I2_6_schema_export_is_stable(tmp_path: Path):
    first = export_schemas(tmp_path / "s")
    a = [json.loads(p.read_text()) for p in first]
    second = export_schemas(tmp_path / "s")
    b = [json.loads(p.read_text()) for p in second]
    assert a == b and {p.name for p in first} == {"targets.schema.json", "flow.schema.json", "agent.schema.json"}


def test_yaml_syntax_error_reports_line(tmp_path: Path):
    p = tmp_path / "targets.yaml"
    p.write_text("targets:\n  AINSSB:\n    repo: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML error"):
        load_targets(p)


def test_T_I21_1_profile_catalog_and_validation(config_root):
    from pathlib import Path

    from ains_orchestrator.config import Profile, gates_for_autonomy, load_profiles, validate_profile

    shipped = Path(__file__).resolve().parents[1]
    catalog = load_profiles(shipped)
    assert {e["id"] for e in catalog["autonomy_levels"] if e["supported"]} == {"L3", "L4"}
    assert gates_for_autonomy(catalog, "L4") == {"G1": "off", "G2": "off", "G3": "on", "G4": "off", "G5": "on"}
    assert gates_for_autonomy(catalog, "L3")["G1"] == "on" and gates_for_autonomy(catalog, "nope")["G2"] == "on"
    assert validate_profile(catalog, Profile()) == []
    bad = Profile(
        project_class="legacy-modernization", methodology="bmad", autonomy="L5", phases=["intent", "maintain"]
    )
    msgs = validate_profile(catalog, bad)
    assert any("legacy-modernization" in m and "not supported" in m for m in msgs)
    assert any("bmad" in m for m in msgs) and any("L5" in m for m in msgs) and any("maintain" in m for m in msgs)
    assert any("cannot be left out" in m for m in msgs)
    ceiling = Profile(project_class="enterprise-greenfield", autonomy="L4")
    assert any("above the ceiling" in m for m in validate_profile(catalog, ceiling))
    empty = {k: [] for k in ("project_classes", "lifecycle_phases", "methodologies", "autonomy_levels")}
    assert load_profiles(config_root) == empty
