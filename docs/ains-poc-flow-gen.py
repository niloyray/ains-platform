"""Generates plan/ains-poc-flow.svg and plan/ains-poc-flow.html.

Diagram 2: one PoC story rolled out phase by phase, left to right, from
populating Jira to PR approval, release and Done. Rows are the components
(humans, Jira, orchestrator, spawned stage agents, LLM, repo/PR, Actions);
columns are the phases. Every agent is spawned by the orchestrator, runs the
Claude Code CLI headless and talks to the LLM through the Max subscription.

Edge colours: BLUE = human <-> machine, TEAL = machine <-> machine,
ROSE = agent <-> LLM.
    python -I ains-poc-flow-gen.py
"""
from pathlib import Path

HUMAN, MACH, LLMC = "#2F5E9E", "#146B63", "#A43E2A"
INK, MUTED = "#1B1F23", "#5B6168"
SAND, SAND_BG = "#8A6D1D", "#F2E9D0"
TEAL, TEAL_BG = "#146B63", "#DEEEEC"
GREY, GREY_BG = "#5B6168", "#ECEBE6"
ROSE, ROSE_BG = "#A43E2A", "#F7E3DD"
POC, POC_BG = "#3F6E3C", "#E4EFE2"
AGENT, AGENT_BG = "#5A4A8A", "#ECE8F5"

# ---------------- grid
LABEL_W = 150
X0 = LABEL_W + 20
COL_W, COL_PITCH = 150, 188          # cell width, column pitch (gap holds pass-through arrows)
NCOL = 8
Y0 = 92
lanes = [  # (key, title, subtitle, height, stroke, fill)
    ("H",  "Humans",            "product owner · reviewer · developer",            96,  SAND, SAND_BG),
    ("J",  "Jira · AINSPOC",    "platform component, used only by the PoC",        92,  GREY, GREY_BG),
    ("O",  "ains-orchestrator", "platform · container on the laptop",              104, TEAL, TEAL_BG),
    ("A",  "Stage agents",      "spawned per stage by the orchestrator · Claude Code CLI headless · Spec Kit skills", 104, AGENT, AGENT_BG),
    ("L",  "LLM",               "Claude Max subscription · OAuth token · no API metering", 60, ROSE, ROSE_BG),
    ("R",  "Repo ains-poc · PR","PoC component on GitHub",                         104, POC, POC_BG),
    ("G",  "GitHub Actions",    "reusable workflows from the platform, run in ains-poc · GHCR · environments", 104, GREY, GREY_BG),
]
GAP = 30  # vertical space between cells inside a lane boundary, used for arrow labels
lane_y = {}
y = Y0
for key, *_ , h, _s, _f in lanes:
    lane_y[key] = (y, h)
    y += h
H = y + 70
W = X0 + NCOL * COL_PITCH + 10

phases = [
    ("1", "Populate Jira"),
    ("2", "Intake & claim"),
    ("3", "Specify · G1"),
    ("4", "Plan · G2"),
    ("5", "Implement"),
    ("6", "Verify · agent review"),
    ("7", "Human approval G3 · merge"),
    ("8", "Release G4 G5 · Done"),
]

out = []
def A(s): out.append(s)
def esc(s): return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def colx(c): return X0 + c * COL_PITCH
def cell_rect(c, lane):
    ly, lh = lane_y[lane]
    if lane == "H":
        return colx(c) + 4, ly + GAP/2, COL_W, lh - GAP - 10
    return colx(c) + 4, ly + GAP/2, COL_W, lh - GAP

cells = {}
def cell(c, lane, title, lines, bold=False, w=None, stroke=None, fill=None):
    x, y, cw, ch = cell_rect(c, lane)
    if w: cw = w
    st = stroke or dict((k, s) for k, *_, s, _f in lanes)[lane]
    fl = fill or dict((k, f) for k, *_, _s, f in lanes)[lane]
    A(f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="6" fill="{fl}" stroke="{st}" stroke-width="{1.8 if bold else 1}"/>')
    cy = y + 13
    A(f'<text x="{x+cw/2}" y="{cy}" class="bt" text-anchor="middle" fill="{INK}">{esc(title)}</text>')
    for ln in lines:
        cy += 11
        A(f'<text x="{x+cw/2}" y="{cy}" class="bs" text-anchor="middle" fill="{MUTED}">{esc(ln)}</text>')
    cells[(c, lane)] = (x, y, cw, ch)

def varrow(c, lane_from, lane_to, dx, color, label=None, dashed=False, dotted=False, both=False, bold=False):
    """Arrow between two cells of one column. Adjacent lanes: straight vertical at colx(c)+dx.
    Pass-through (a lane in between): elbow that leaves the source box, runs down the gutter
    at colx(c)+dx and lands on the target box, so both ends touch a box."""
    order = [k for k, *_ in lanes]
    adjacent = abs(order.index(lane_from) - order.index(lane_to)) == 1
    (xa, ya, wa, ha) = cells[(c, lane_from)]
    (xb, yb, wb, hb) = cells[(c, lane_to)]
    down = yb > ya
    y1 = ya + ha if down else ya
    y2 = yb if down else yb + hb
    style = ' stroke-dasharray="6 4"' if dashed else (' stroke-dasharray="2 3"' if dotted else "")
    m = ' marker-end="url(#h)"' + (' marker-start="url(#h)"' if both else "")
    if adjacent:
        x = colx(c) + dx
        A(f'<path d="M{x},{y1} L{x},{y2}" fill="none" stroke="{color}" stroke-width="{2 if bold else 1.3}"{style}{m}/>')
        if label:
            A(f'<text x="{x+5}" y="{(y1+y2)/2+3}" class="el" fill="{color}">{esc(label)}</text>')
        return
    # straight if no intermediate cell lies under the candidate x, else an elbow through the gutter
    i1, i2 = sorted((order.index(lane_from), order.index(lane_to)))
    xcand = colx(c) + (dx if dx <= COL_W else dx - 22)
    blocked = False
    for k in order[i1 + 1:i2]:
        if (c, k) in cells:
            cx, cy, cw, ch = cells[(c, k)]
            if cx - 2 <= xcand <= cx + cw + 2:
                blocked = True
    if not blocked:
        A(f'<path d="M{xcand},{y1} L{xcand},{y2}" fill="none" stroke="{color}" stroke-width="{2 if bold else 1.3}"{style}{m}/>')
        if label:
            my = (y1 + y2) / 2
            A(f'<text x="{xcand-4}" y="{my}" class="el" fill="{color}" text-anchor="middle" transform="rotate(-90 {xcand-4} {my})">{esc(label)}</text>')
        return
    xc = colx(c) + dx                    # gutter channel
    xs = min(colx(c) + dx - 22, xa + wa - 8)   # stub x inside the boxes
    s1 = y1 + 12 if down else y1 - 12
    s2 = y2 - 12 if down else y2 + 12
    d = f"M{xs},{y1} L{xs},{s1} L{xc},{s1} L{xc},{s2} L{xs},{s2} L{xs},{y2}"
    A(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{2 if bold else 1.3}"{style}{m}/>')
    if label:
        my = (s1 + s2) / 2
        A(f'<text x="{xc-4}" y="{my}" class="el" fill="{color}" text-anchor="middle" transform="rotate(-90 {xc-4} {my})">{esc(label)}</text>')

def gate_band(c, text):
    """blue band at the bottom of the Humans lane: the platform waits here for a human decision"""
    ly, lh = lane_y["H"]
    x, y, w, h = colx(c) + 4, ly + lh - 17, COL_W, 14
    A(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="{HUMAN}" opacity="0.14"/>')
    A(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="none" stroke="{HUMAN}" stroke-width="1" stroke-dasharray="3 2"/>')
    A(f'<text x="{x+w/2}" y="{y+10}" class="el" text-anchor="middle" fill="{HUMAN}" font-weight="700">{esc(text)}</text>')

# ------------------------------------------------------------------ header
A(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t d">')
A('<title id="t">One PoC story through the AI-native lane, phase by phase</title>')
A('<desc id="d">Columns are the eight phases of one story, from populating Jira to release and Done. Rows are the components: humans, Jira, the orchestrator, the stage agents it spawns, the LLM, the PoC repository with its pull request, and GitHub Actions. Each phase shows which agent is spawned, what it produces, and where a human gate waits.</desc>')
A('<defs><marker id="h" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
  '<path d="M1 1 L9 5 L1 9" fill="none" stroke="context-stroke" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></marker>'
  '<style>text{font-family:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif}'
  '.cl{font-size:11.5px;font-weight:600}.bt{font-size:10.5px;font-weight:600}.bs{font-size:8.8px}.el{font-size:8.5px}.ph{font-size:11px;font-weight:700}.ln{font-size:10.5px;font-weight:600}.ls{font-size:8.5px}</style></defs>')
A(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>')
A(f'<text x="20" y="24" class="cl" fill="{INK}">ONE POC STORY THROUGH THE AI-NATIVE LANE · phase by phase</text>')
A(f'<text x="20" y="40" class="ls" fill="{MUTED}">every agent below is spawned by ains-orchestrator as a headless Claude Code CLI process on the Max subscription; the orchestrator never calls a model itself. Human-lane stories skip the agents in 3–5 and join at 6.</text>')

# phase chevrons
for c, (num, name) in enumerate(phases):
    x = colx(c); yx = 54; w = COL_PITCH - 8; h = 28
    pts = f"{x},{yx} {x+w-12},{yx} {x+w},{yx+h/2} {x+w-12},{yx+h} {x},{yx+h} {x+12 if c else x},{yx+h/2}"
    A(f'<polygon points="{pts}" fill="{TEAL}" opacity="{0.85 if c % 2 else 1}"/>')
    A(f'<text x="{x+w/2}" y="{yx+18}" class="ph" text-anchor="middle" fill="#FFFFFF">{esc(num + "  " + name)}</text>')

# lane bands and labels
for key, title, sub, h, st, fl in lanes:
    ly, lh = lane_y[key]
    A(f'<rect x="10" y="{ly}" width="{W-20}" height="{lh}" fill="{fl}" opacity="0.28"/>')
    A(f'<line x1="10" y1="{ly}" x2="{W-10}" y2="{ly}" stroke="{st}" stroke-width="0.6" opacity="0.5"/>')
    A(f'<text x="18" y="{ly+lh/2-2}" class="ln" fill="{st}">{esc(title)}</text>')
    # wrap subtitle to ~34 chars per line
    words, line, lines_ = sub.split(), "", []
    for wd in words:
        if len(line) + len(wd) + 1 > 34:
            lines_.append(line); line = wd
        else:
            line = (line + " " + wd).strip()
    lines_.append(line)
    yy = ly + lh/2 + 9
    for ln in lines_[:3]:
        A(f'<text x="18" y="{yy}" class="ls" fill="{MUTED}">{esc(ln)}</text>'); yy += 10
A(f'<line x1="{X0-6}" y1="{Y0}" x2="{X0-6}" y2="{y}" stroke="{MUTED}" stroke-width="0.6"/>')

# ------------------------------------------------------------------ cells
# phase 1
cell(0, "H", "Product owner", ["writes story AINSPOC-n", "Execution Lane = AI-native", "moves it to Ready"])
cell(0, "J", "Story in Ready", ["Automation rule fires", "→ repository_dispatch", "(audit trace only)"])
cell(0, "G", "intake-ack workflow", ["runs in ains-platform", "comments 'accepted into", "AI-native lane' on the story"])
# phase 2
cell(1, "J", "Status → Specifying", ["session id comment", "story is now owned by", "the orchestrator"])
cell(1, "O", "Poller + claim", ["JQL every 60 s finds the story", "claims it · SQLite session", "clones ains-poc → /work/<KEY>"], bold=True)
cell(1, "R", "Clone + branch", ["/work/<KEY>/ on the volume", "branch <KEY>-<slug>"])
# phase 3
cell(2, "H", "Reviewer · G1", ["reads spec.md in the draft PR", "Jira: Spec Approved | Rework"])
cell(2, "J", "Status → Spec Review", ["waits for the G1 decision", "Rework comment → re-spawn"])
cell(2, "O", "Stage: spec", ["spawns the spec agent", "polls Jira for G1", "Rework → spawn again with comment"], bold=True)
cell(2, "A", "spec agent", ["claude -p · /speckit.specify", "input: story text", "output: specs/<KEY>/spec.md"], bold=True)
cell(2, "L", "model calls", ["Max plan usage"], w=96)
cell(2, "R", "Draft PR opened", ["commit spec.md · push", "PR title '<KEY>: …'", "draft, not mergeable yet"])
# phase 4
cell(3, "H", "Reviewer · G2", ["reads plan.md, tasks.md", "Jira: Plan Approved | Rework"])
cell(3, "J", "Status → Plan Review", ["waits for the G2 decision"])
cell(3, "O", "Stage: plan", ["spawns the plan agent", "polls Jira for G2"], bold=True)
cell(3, "A", "plan agent", ["claude -p · /speckit.plan + tasks", "input: spec.md", "output: plan.md · tasks.md"], bold=True)
cell(3, "L", "model calls", ["Max plan usage"], w=96)
cell(3, "R", "Draft PR updated", ["commit plan.md, tasks.md", "push"])
# phase 5
cell(4, "J", "Status → Implementing", ["progress comments"])
cell(4, "O", "Stage: implement", ["spawns the implement agent", "--permission-mode acceptEdits", "--max-turns cap · retries once"], bold=True)
cell(4, "A", "implement agent", ["claude -p · /speckit.implement", "input: tasks.md · CLAUDE.md", "output: code + tests, commits '<KEY>:'"], bold=True)
cell(4, "L", "model calls", ["Max plan usage"], w=96)
cell(4, "R", "PR marked ready", ["push · ready for review", "PR body links the story"])
# phase 6
cell(5, "J", "Status → Code Review", ["set by the CI callback", "PR link attached"])
cell(5, "O", "Stage: review", ["PR ready → spawns the review agent", "also for Human-lane PRs", "labelled ai-review"], bold=True)
cell(5, "A", "review agent", ["claude -p · /code-review on the diff", "runs the tests · reads CI result", "posts a PR review, never merges"], bold=True)
cell(5, "L", "model calls", ["Max plan usage"], w=96)
cell(5, "R", "PR review posted", ["comments · approve or", "request changes", "orchestrator now waits"])
cell(5, "G", "CI on pull_request", ["reusable-ci: ruff · pytest +", "Postgres · docker build", "status check 'ci' · Jira callback"])
# phase 7
cell(6, "H", "Reviewer · G3", ["reads agent review + diff", "GitHub: approve | request changes"])
cell(6, "O", "Stage: merge", ["sees approval + CI green", "squash merge via gh", "changes requested → rework"], bold=True)
cell(6, "R", "Approved + merged", ["human approval required", "squash merge to main", "branch deleted"])
# phase 8
cell(7, "H", "Reviewer · G4, G5", ["approves staging, then", "production in Environments"])
cell(7, "J", "Status → Staging → Done", ["PR + run links attached", "loop closed"])
cell(7, "O", "Session closed", ["timings and gate outcomes", "recorded for the research log"])
cell(7, "R", "main updated", ["push main triggers release"])
cell(7, "G", "Release on push main", ["reusable-release: image → GHCR", "staging (G4) → production (G5)", "Jira callback: Staging, Done"])

# ------------------------------------------------------------------ arrows
# phase 1
varrow(0, "H", "J", 60, HUMAN, "story · lane · Ready", bold=True)
varrow(0, "J", "G", 160, MACH, "Automation → dispatch", dotted=True)
varrow(0, "G", "J", 172, MACH, "ack comment", dashed=True)
# phase 2
varrow(1, "J", "O", 50, MACH, "poll")
varrow(1, "O", "J", 110, MACH, "claim")
varrow(1, "O", "R", 160, MACH, "git clone (PAT)")
# phases 3, 4, 5: spawn pattern
for c, spawn, out_label in ((2, "spawn spec agent", "spec.md · push · draft PR"),
                            (3, "spawn plan agent", "plan.md, tasks.md · push"),
                            (4, "spawn implement agent", "code + tests · push · PR ready")):
    varrow(c, "O", "A", 60, MACH, spawn, bold=True)
    varrow(c, "A", "L", 40, LLMC, "", both=True)
    varrow(c, "A", "R", 160, MACH, out_label)
varrow(2, "H", "J", 60, HUMAN, None, bold=True)
varrow(2, "O", "J", 110, MACH, "→ Spec Review", both=True)
varrow(3, "H", "J", 60, HUMAN, None, bold=True)
varrow(3, "O", "J", 110, MACH, "→ Plan Review", both=True)
varrow(4, "O", "J", 110, MACH, "→ Implementing")
# phase 6
varrow(5, "R", "G", 60, MACH, "pull_request → CI")
varrow(5, "G", "J", 172, MACH, "callback → Code Review", dashed=True)
varrow(5, "R", "O", 160, MACH, "PR ready → trigger")
varrow(5, "O", "A", 60, MACH, "spawn review agent", bold=True)
varrow(5, "A", "L", 40, LLMC, "", both=True)
varrow(5, "A", "R", 110, MACH, "posts PR review")
# phase 7
varrow(6, "H", "R", 160, HUMAN, "G3 approve PR | request changes", bold=True)
varrow(6, "R", "O", 60, MACH, "approved + CI green")
varrow(6, "O", "R", 110, MACH, "squash merge (gh)")
# phase 8
varrow(7, "R", "G", 60, MACH, "push main → release")
varrow(7, "H", "G", 160, HUMAN, "G4 staging · G5 production", bold=True)
varrow(7, "G", "J", 172, MACH, "callback → Staging, Done", dashed=True)

# gate bands: where the platform waits for a human decision
gate_band(2, "GATE G1 · waits for Jira decision")
gate_band(3, "GATE G2 · waits for Jira decision")
gate_band(6, "GATE G3 · waits for PR approval")
gate_band(7, "GATES G4 · G5 · environment approvals")

# rework loops: dashed, from the left edge of this phase's orchestrator box back into the previous one
for c, text in ((3, "G1 Rework"), (4, "G2 Rework"), (6, "changes requested")):
    xa, ya, wa, ha = cells[(c, "O")]
    xb, yb, wb, hb = cells[(c - 1, "O")]
    yy = ya + ha - 8
    A(f'<path d="M{xa},{yy} L{xb + wb},{yy}" fill="none" stroke="{MACH}" stroke-width="1.2" stroke-dasharray="4 3" marker-end="url(#h)"/>')
    A(f'<text x="{(xa + xb + wb)/2}" y="{ya + ha + 9}" class="el" text-anchor="middle" fill="{MACH}">{esc(text)}</text>')

# ------------------------------------------------------------------ legend
ly0 = y + 18
A(f'<line x1="20" y1="{ly0}" x2="50" y2="{ly0}" stroke="{HUMAN}" stroke-width="2" marker-end="url(#h)"/><text x="58" y="{ly0+3}" class="bs" fill="{INK}">human ↔ machine (gates G1–G5)</text>')
A(f'<line x1="260" y1="{ly0}" x2="290" y2="{ly0}" stroke="{MACH}" stroke-width="1.3" marker-end="url(#h)"/><text x="298" y="{ly0+3}" class="bs" fill="{INK}">machine ↔ machine</text>')
A(f'<line x1="440" y1="{ly0}" x2="470" y2="{ly0}" stroke="{LLMC}" stroke-width="1.3" marker-end="url(#h)"/><text x="478" y="{ly0+3}" class="bs" fill="{INK}">agent ↔ LLM (Max subscription)</text>')
A(f'<text x="20" y="{ly0+20}" class="bs" fill="{MUTED}">dashed = feedback or callback · dotted = audit trace · bold boxes = where an agent or the orchestrator acts · Human lane: the developer does phases 3–5 in VS Code + Claude Code CLI, then the same 6–8 (review agent on label ai-review)</text>')
A(f'<text x="20" y="{ly0+34}" class="bs" fill="{MUTED}">blue dashed band = human gate where the platform waits (G1, G2 in Jira · G3 on the PR · G4, G5 in GitHub Environments). Gates can be switched off per target; a skipped gate is auto-passed with a Jira comment so the trace stays complete.</text>')

A('</svg>')
svg = "\n".join(out)
here = Path(__file__).parent
(here / "ains-poc-flow.svg").write_text(svg, encoding="utf-8")

html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AINS PoC story flow</title>
<style>
:root{{--bg:#F6F5F1;--fg:#1B1F23;--muted:#5B6168;--border:#DAD7CF;--code:#EEEBE2;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#14181C;--fg:#E7E5DF;--muted:#9AA0A6;--border:#2B3136;--code:#11161B;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#14181C;--fg:#E7E5DF;--muted:#9AA0A6;--border:#2B3136;--code:#11161B;color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14.5px/1.6 "IBM Plex Sans",system-ui,sans-serif;padding:0 16px}}
.wrap{{max-width:1800px;margin:0 auto;padding:32px 0 64px}}
h1{{font-size:24px;font-weight:500;margin:0 0 4px}}
h2{{font-size:17px;font-weight:500;margin:36px 0 10px;border-top:1px solid var(--border);padding-top:10px}}
.lede{{color:var(--muted);margin:0 0 16px;max-width:100ch}}
.frame{{background:#fff;border:1px solid var(--border);border-radius:12px;padding:12px;overflow-x:auto}}
.frame svg{{display:block;min-width:1500px;width:100%;height:auto}}
.cap{{font-size:12.5px;color:var(--muted);margin:8px 0 0}}
code{{font-family:"IBM Plex Mono",monospace;font-size:12.5px;background:var(--code);padding:1px 6px;border-radius:4px}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0 18px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top}}
th{{color:var(--muted);font-weight:500;font-size:11.5px;text-transform:uppercase;letter-spacing:.03em}}
p{{max-width:85ch}}
</style></head><body><div class="wrap">
<p class="lede">AINS · AI-native SDLC research · diagram 2 of 2 · 2026-11-02</p>
<h1>One PoC story through the AI-native lane, phase by phase</h1>
<p class="lede">Companion to <code>ains-architecture.html</code> (the phase overview). This diagram follows a single <code>ains-poc</code> story from populating Jira to release and Done. Columns are the eight phases; rows are the components. Every agent is spawned by the orchestrator for one stage, runs the Claude Code CLI headless under the Max subscription, writes its output into the clone, and exits. The orchestrator then waits on a human gate or on GitHub.</p>
<div class="frame">
{svg}
</div>
<p class="cap">Source: <code>plan/ains-poc-flow-gen.py</code> regenerates this page and <code>plan/ains-poc-flow.svg</code>.</p>

<h2>The agents and what spawns them</h2>
<table>
<tr><th>Agent</th><th>Spawned when</th><th>Runs</th><th>Reads</th><th>Writes</th><th>Then waits for</th></tr>
<tr><td>spec agent</td><td>story claimed (phase 2 → 3)</td><td><code>claude -p</code> + <code>/speckit.specify</code></td><td>story text, CLAUDE.md</td><td><code>specs/&lt;KEY&gt;/spec.md</code>, draft PR</td><td>G1 in Jira</td></tr>
<tr><td>plan agent</td><td>G1 approved</td><td><code>claude -p</code> + <code>/speckit.plan</code>, <code>/speckit.tasks</code></td><td>spec.md</td><td><code>plan.md</code>, <code>tasks.md</code></td><td>G2 in Jira</td></tr>
<tr><td>implement agent</td><td>G2 approved</td><td><code>claude -p</code> + <code>/speckit.implement</code>, acceptEdits, max-turns cap</td><td>tasks.md, repo</td><td>code, tests, commits prefixed with the key; PR ready</td><td>CI + review agent</td></tr>
<tr><td>review agent</td><td>PR marked ready (or a Human-lane PR labelled <code>ai-review</code>)</td><td><code>claude -p</code> + <code>/code-review</code> on the diff, runs tests</td><td>diff, CI result, spec and plan</td><td>a PR review: comments, approve or request changes. Never merges</td><td>G3 human approval on the PR</td></tr>
</table>
<p>On Rework (G1, G2) or "changes requested" (G3) the orchestrator spawns the same stage's agent again with the human comment appended to its input. Merge, release and the Jira callbacks are done by the orchestrator and GitHub Actions, not by an agent.</p>

<h2>Phase by phase</h2>
<table>
<tr><th>Phase</th><th>Trigger</th><th>Who acts</th><th>Result</th><th>Gate</th></tr>
<tr><td>1 Populate Jira</td><td>product owner</td><td>human → Jira; Automation → intake-ack comment</td><td>story in Ready, lane AI-native</td><td>—</td></tr>
<tr><td>2 Intake & claim</td><td>poll</td><td>orchestrator</td><td>status Specifying, clone and branch</td><td>—</td></tr>
<tr><td>3 Specify</td><td>claim</td><td>spec agent</td><td>spec.md in a draft PR, status Spec Review</td><td>G1 Jira</td></tr>
<tr><td>4 Plan</td><td>G1 approved</td><td>plan agent</td><td>plan.md, tasks.md, status Plan Review</td><td>G2 Jira</td></tr>
<tr><td>5 Implement</td><td>G2 approved</td><td>implement agent</td><td>code and tests, PR ready, status Implementing</td><td>—</td></tr>
<tr><td>6 Verify · agent review</td><td>PR ready</td><td>CI (Actions), review agent</td><td>status check, PR review posted, status Code Review</td><td>—</td></tr>
<tr><td>7 Human approval · merge</td><td>G3</td><td>reviewer, then orchestrator</td><td>squash merge to main</td><td>G3 GitHub PR</td></tr>
<tr><td>8 Release · Done</td><td>push main</td><td>Actions, reviewer</td><td>image in GHCR, staging, production, Jira Done with links</td><td>G4, G5 Environments</td></tr>
</table>
</div></body></html>
"""
(here / "ains-poc-flow.html").write_text(html, encoding="utf-8")
print("wrote ains-poc-flow.svg and ains-poc-flow.html")
