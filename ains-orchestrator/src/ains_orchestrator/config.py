"""I2: typed loaders for targets.yaml, flows/*.yaml and agents/*.yaml.

Every loader raises ConfigError with file and (best-effort) line on bad input. The agent
schema is the first line of defence for decision D4: no ANTHROPIC_* variable may ever be
passed through to a Claude Code subprocess, and `--bare` is forbidden because it ignores
the subscription token.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ains_orchestrator.errors import ConfigError

GATE_NAMES = ("G1", "G2", "G3", "G4", "G5")
OnOff = Literal["on", "off"]
FORBIDDEN_ENV_PREFIXES = ("ANTHROPIC_",)
FORBIDDEN_ENV = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"}
ApprovalMode = Literal["review", "label"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ----------------------------------------------------------------------------- targets.yaml
class Target(Strict):
    repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    default_branch: str = "main"
    flow: str = "story"
    gates: dict[str, OnOff] = Field(default_factory=lambda: {g: "on" for g in GATE_NAMES})
    review_label: str = "ai-review"
    approval_mode: ApprovalMode = "review"
    approval_label: str = "g3-approved"
    rework_label: str = "g3-rework"
    execution_lane_field_id: str | None = None

    @field_validator("gates", mode="before")
    @classmethod
    def _yaml_booleans(cls, v: object) -> object:
        """YAML 1.1 reads `on`/`off` as booleans; accept both spellings."""
        if isinstance(v, dict):
            return {str(k): ("on" if x is True else "off" if x is False else x) for k, x in v.items()}
        return v

    @field_validator("gates")
    @classmethod
    def _known_gates(cls, v: dict[str, OnOff]) -> dict[str, OnOff]:
        unknown = sorted(set(v) - set(GATE_NAMES))
        if unknown:
            raise ValueError(f"unknown gate(s) {unknown}; allowed: {', '.join(GATE_NAMES)}")
        return {g: v.get(g, "on") for g in GATE_NAMES}

    @property
    def owner(self) -> str:
        return self.repo.split("/")[0]

    @property
    def name(self) -> str:
        return self.repo.split("/")[1]


class TargetsFile(Strict):
    targets: dict[str, Target] = Field(default_factory=dict)

    @field_validator("targets")
    @classmethod
    def _keys_are_jira_keys(cls, v: dict[str, Target]) -> dict[str, Target]:
        for k in v:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,9}", k):
                raise ValueError(f"'{k}' is not a Jira project key (2-10 uppercase letters/digits)")
        return v


# ----------------------------------------------------------------------------- flows/*.yaml
class GateDef(Strict):
    after: str
    where: Literal["jira", "github", "actions_environment"]
    approve: str | None = None
    rework: str | None = None
    name: str | None = None


class Flow(Strict):
    name: str
    stages: list[str] = Field(min_length=1)
    gates: dict[str, GateDef] = Field(default_factory=dict)
    jira_status: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _gate_refs(self) -> Flow:
        unknown = sorted(set(self.gates) - set(GATE_NAMES))
        if unknown:
            raise ValueError(f"unknown gate(s) {unknown}; allowed: {', '.join(GATE_NAMES)}")
        for gname, g in self.gates.items():
            if g.after not in self.stages and g.after not in self.gates:
                raise ValueError(f"gate {gname}: 'after: {g.after}' is neither a stage nor a gate")
            if g.where == "actions_environment" and not g.name:
                raise ValueError(f"gate {gname}: actions_environment gates need 'name'")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("stages must be unique")
        return self


# ----------------------------------------------------------------------------- agents/*.yaml
class StageConfig(Strict):
    prompt: str
    permission_mode: Literal["default", "acceptEdits", "plan", "dontAsk"] = "acceptEdits"
    max_turns: int = Field(ge=1, le=500)
    read_only: bool = False


class Retry(Strict):
    attempts: int = Field(default=2, ge=1, le=5)
    backoff_seconds: int = Field(default=300, ge=0)


class AgentTemplate(Strict):
    name: str
    command: list[str] = Field(min_length=1)
    env_passthrough: list[str]
    stages: dict[str, StageConfig]
    retry: Retry = Field(default_factory=Retry)
    wall_clock_limit_minutes: int = Field(default=30, ge=1, le=240)

    @field_validator("env_passthrough")
    @classmethod
    def _explicit_and_safe(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("env_passthrough must be explicit and non-empty")
        bad = [x for x in v if x in FORBIDDEN_ENV or x.startswith(FORBIDDEN_ENV_PREFIXES)]
        if bad:
            raise ValueError(f"forbidden variable(s) in env_passthrough: {bad} (D4: never pass API credentials)")
        return v

    @field_validator("command")
    @classmethod
    def _headless_not_bare(cls, v: list[str]) -> list[str]:
        if "--bare" in v:
            raise ValueError("'--bare' ignores CLAUDE_CODE_OAUTH_TOKEN and is forbidden")
        if "-p" not in v and "--print" not in v:
            raise ValueError("command must run Claude Code non-interactively ('-p')")
        return v


# ----------------------------------------------------------------------------- loading
def _find_line(text: str, needle: str) -> int | None:
    for i, line in enumerate(text.splitlines(), start=1):
        if needle and needle in line:
            return i
    return None


def _load_yaml(path: Path) -> tuple[Any, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(str(path), f"cannot read: {e}") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        raise ConfigError(str(path), f"YAML error: {e}", getattr(mark, "line", None)) from e
    return data if data is not None else {}, text


def _validate(model: type[BaseModel], data: Any, path: Path, text: str) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(x) for x in first["loc"]) or "<root>"
        last_key = str(first["loc"][-1]) if first["loc"] else ""
        line = _find_line(text, last_key)
        raise ConfigError(str(path), f"{loc}: {first['msg']}", line) from e


def load_targets(path: Path) -> TargetsFile:
    data, text = _load_yaml(path)
    return _validate(TargetsFile, data, path, text)


def load_flow(path: Path) -> Flow:
    data, text = _load_yaml(path)
    return _validate(Flow, data, path, text)


def load_agent(path: Path) -> AgentTemplate:
    data, text = _load_yaml(path)
    return _validate(AgentTemplate, data, path, text)


class Config(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    root: Path
    targets: TargetsFile
    flows: dict[str, Flow]
    agents: dict[str, AgentTemplate]

    def flow_for(self, target: Target) -> Flow:
        if target.flow not in self.flows:
            raise ConfigError(str(self.root / "targets.yaml"), f"flow '{target.flow}' not found")
        return self.flows[target.flow]


def load_config(root: Path) -> Config:
    """Load targets.yaml, flows/*.yaml and agents/*.yaml under `root` (the ains-orchestrator dir)."""
    targets = load_targets(root / "targets.yaml")
    flows = {f.name: f for f in (load_flow(p) for p in sorted((root / "flows").glob("*.yaml")))}
    agents = {a.name: a for a in (load_agent(p) for p in sorted((root / "agents").glob("*.yaml")))}
    for key, t in targets.targets.items():
        if t.flow not in flows:
            raise ConfigError(str(root / "targets.yaml"), f"targets.{key}.flow: '{t.flow}' not in flows/")
    for a in agents.values():
        for stage, sc in a.stages.items():
            if not (root / sc.prompt).exists():
                raise ConfigError(
                    str(root / "agents"), f"agent {a.name} stage {stage}: prompt file {sc.prompt} missing"
                )
    return Config(root=root, targets=targets, flows=flows, agents=agents)


def export_schemas(out_dir: Path) -> list[Path]:
    """Write JSON schemas for editor validation."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in (("targets", TargetsFile), ("flow", Flow), ("agent", AgentTemplate)):
        p = out_dir / f"{name}.schema.json"
        p.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8")
        written.append(p)
    return written
