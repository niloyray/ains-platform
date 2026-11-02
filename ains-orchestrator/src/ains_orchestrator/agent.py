"""I8: spawn one headless Claude Code CLI process per stage.

The subprocess gets a minimal environment: PATH, HOME and friends plus exactly the variables the
agent template lists in `env_passthrough` (schema-checked: never ANTHROPIC_*). The orchestrator
never reads the OAuth token's value for any other purpose. Output is Claude Code's JSON result.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ains_orchestrator.config import AgentTemplate
from ains_orchestrator.flow import AgentResult

log = logging.getLogger("ains.agent")

BASE_ENV_KEYS = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "SystemRoot",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "TERM",
    "APPDATA",
    "LOCALAPPDATA",
    "COMSPEC",
    "PATHEXT",
    "XDG_CONFIG_HOME",
)
FORBIDDEN_PREFIXES = ("ANTHROPIC_",)
VERDICT_PREFIX = "VERDICT:"


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


ProcRunner = Callable[[list[str], Path, dict[str, str], float, str], ProcResult]


def subprocess_proc_runner(
    argv: list[str], cwd: Path, env: dict[str, str], timeout_s: float, stdin: str = ""
) -> ProcResult:
    try:
        p = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            shell=False,
            input=stdin,
        )
    except subprocess.TimeoutExpired as e:
        return ProcResult(
            -1,
            (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
            (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
            True,
        )
    except FileNotFoundError as e:
        return ProcResult(127, "", f"{argv[0]}: not found ({e})")
    return ProcResult(p.returncode, p.stdout, p.stderr)


def trust_workspace(workdir: Path, home: str | None) -> None:
    """Mark `workdir` as trusted in ~/.claude.json so Claude Code applies the repo's .claude/settings.json.

    Headless runs cannot answer the trust dialog; without this the onboarded repo's allow/deny lists are ignored.
    """
    cfg_dir = Path(home) if home else Path.home()
    path = cfg_dir / ".claude.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    projects = data.setdefault("projects", {})
    entry = projects.setdefault(str(workdir), {})
    if entry.get("hasTrustDialogAccepted") is True:
        return
    entry["hasTrustDialogAccepted"] = True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as e:  # not fatal: the agent still runs, with the repo settings ignored
        log.warning("could not mark %s trusted in %s: %s", workdir, path, e)


def minimal_env(parent: Mapping[str, str], passthrough: list[str]) -> dict[str, str]:
    """Only base process variables plus the explicitly allowed ones; ANTHROPIC_* never survives."""
    env: dict[str, str] = {}
    for k in BASE_ENV_KEYS:
        if k in parent:
            env[k] = parent[k]
    for k in passthrough:
        if k.startswith(FORBIDDEN_PREFIXES):
            raise ValueError(f"refusing to pass {k} to an agent (D4)")
        if k in parent:
            env[k] = parent[k]
    for k in list(env):
        if k.startswith(FORBIDDEN_PREFIXES):
            del env[k]
    return env


def render_prompt(template_text: str, key: str, context: dict[str, Any]) -> str:
    """Substitute {key}, {summary}, ... in the stage prompt; unknown placeholders are left as-is."""
    values = {"key": key, **{k: ("" if v is None else str(v)) for k, v in context.items()}}

    class _Safe(dict):
        def __missing__(self, k: str) -> str:
            return "{" + k + "}"

    text = template_text.format_map(_Safe(values))
    if context.get("rework_comment"):
        text += (
            "\n\n## Rework requested by the reviewer\n\nThe previous output was sent back with this comment. "
            f"Address it explicitly:\n\n{context['rework_comment']}\n"
        )
    return text


def parse_result(stdout: str) -> dict[str, Any]:
    """Claude Code `--output-format json` prints one JSON object (or a JSON array of messages)."""
    stdout = stdout.strip()
    if not stdout:
        return {}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        last = stdout.splitlines()[-1]
        try:
            data = json.loads(last)
        except json.JSONDecodeError:
            return {"result": stdout[-4000:], "unparsed": True}
    if isinstance(data, list):
        results = [d for d in data if isinstance(d, dict) and d.get("type") == "result"]
        data = results[-1] if results else (data[-1] if data else {})
    return data if isinstance(data, dict) else {"result": str(data)}


def extract_verdict(text: str) -> str:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.upper().startswith(VERDICT_PREFIX):
            v = line[len(VERDICT_PREFIX) :].strip().strip("*`").lower().replace(" ", "_")
            return "request_changes" if v.startswith("request") else "comment"
    return "comment"


class ClaudeCodeRunner:
    def __init__(
        self,
        template: AgentTemplate,
        root: Path,
        *,
        parent_env: Mapping[str, str] | None = None,
        runner: ProcRunner = subprocess_proc_runner,
        clock: Callable[[], float] = time.monotonic,
        wall_clock_minutes: int | None = None,
    ) -> None:
        self.t = template
        self.root = Path(root)
        self.parent_env = dict(parent_env if parent_env is not None else os.environ)
        self._run = runner
        self._clock = clock
        self.wall_clock_s = 60 * (wall_clock_minutes or template.wall_clock_limit_minutes)

    def argv_for(self, stage: str, prompt: str) -> list[str]:
        sc = self.t.stages[stage]
        argv = list(self.t.command)
        argv += ["--permission-mode", sc.permission_mode, "--max-turns", str(sc.max_turns)]
        if sc.read_only:
            argv += ["--disallowedTools", "Edit,Write,MultiEdit,NotebookEdit,WebFetch,WebSearch"]
        return argv  # the prompt goes through stdin (see run): no argv limits, no option swallowing it

    def run(self, stage: str, key: str, workdir: Any, context: dict[str, Any]) -> AgentResult:
        if stage not in self.t.stages:
            return AgentResult(False, error=f"agent template has no stage {stage!r}")
        sc = self.t.stages[stage]
        template_text = (self.root / sc.prompt).read_text(encoding="utf-8")
        prompt = render_prompt(template_text, key, context)
        argv = self.argv_for(stage, prompt)
        env = minimal_env(self.parent_env, self.t.env_passthrough)
        if "CLAUDE_CODE_OAUTH_TOKEN" not in env:
            return AgentResult(False, error="CLAUDE_CODE_OAUTH_TOKEN is not set; the agent cannot authenticate")
        t0 = self._clock()
        log.info("agent %s for %s: %s (max_turns=%s, limit=%ss)", stage, key, argv[0], sc.max_turns, self.wall_clock_s)
        trust_workspace(Path(workdir), env.get("HOME"))
        pr = self._run(argv, Path(workdir), env, self.wall_clock_s, prompt)
        duration_ms = int((self._clock() - t0) * 1000)
        if pr.timed_out:
            return AgentResult(
                False,
                duration_ms=duration_ms,
                error=f"{stage} agent exceeded {self.wall_clock_s}s",
                extra={"timeout": True},
            )
        data = parse_result(pr.stdout)
        text = str(data.get("result") or "")
        turns = int(data.get("num_turns") or 0)
        cost = data.get("total_cost_usd")
        cost = float(cost) if isinstance(cost, int | float) else None
        if pr.returncode != 0 or data.get("is_error"):
            err = (pr.stderr or text or f"exit {pr.returncode}").strip()[:1000]
            return AgentResult(
                False,
                text=text,
                turns=turns,
                cost_usd=cost,
                duration_ms=duration_ms,
                error=err,
                extra={"subtype": data.get("subtype")},
            )
        extra: dict[str, Any] = {"session_id": data.get("session_id"), "subtype": data.get("subtype")}
        if stage == "review":
            extra["verdict"] = extract_verdict(text)
        return AgentResult(
            True,
            text=text,
            turns=turns,
            cost_usd=cost,
            duration_ms=int(data.get("duration_ms") or duration_ms),
            extra=extra,
        )


class DryRunAgents:
    """Writes placeholder outputs so a whole flow can run offline (`ains poll --dry-run`)."""

    def run(self, stage: str, key: str, workdir: Any, context: dict[str, Any]) -> AgentResult:
        d = Path(workdir) / "specs" / key
        d.mkdir(parents=True, exist_ok=True)
        files = {"spec": ["spec.md"], "plan": ["plan.md", "tasks.md"], "implement": [], "review": []}.get(stage, [])
        for name in files:
            (d / name).write_text(f"# {name} (dry-run placeholder for {key})\n", encoding="utf-8")
        extra = {"verdict": "comment"} if stage == "review" else {}
        return AgentResult(True, text=f"[dry-run] {stage} done", turns=0, cost_usd=0.0, extra=extra)


def claude_on_path() -> bool:
    from shutil import which

    return which("claude") is not None or which("claude.cmd") is not None or sys.platform != "win32" and False
