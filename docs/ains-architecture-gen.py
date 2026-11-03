"""Generates plan/ains-architecture.svg and plan/ains-architecture.html.

Diagram 1: the platform, phase-wise from left to right.
  column A  Phase 1  build the platform (ains-platform), directly from the
            workstation: no Jira project, no CI, no orchestrator for the
            platform itself                                         edges P1..P7
  column B  platform runtime: Jira, orchestrator, reusable Actions
            workflows, credentials. Built in Phase 1, used only by the PoC.
            Agent usage in the AI-native loop                       edges A1..A7
  right     a placeholder for the PoC: everything about ains-poc and the
            story phases lives in diagram 2 (ains-poc-flow).

Edge colours: BLUE = human <-> machine, TEAL = machine <-> machine.
    python -I ains-architecture-gen.py
"""
from pathlib import Path

W, H = 1300, 850

HUMAN = "#2F5E9E"
MACH = "#146B63"
INK, MUTED = "#1B1F23", "#5B6168"
SAND, SAND_BG = "#8A6D1D", "#F2E9D0"
TEAL, TEAL_BG = "#146B63", "#DEEEEC"
GREY, GREY_BG = "#5B6168", "#ECEBE6"
ROSE, ROSE_BG = "#A43E2A", "#F7E3DD"
POC, POC_BG = "#3F6E3C", "#E4EFE2"

out = []
def A(s): out.append(s)
def esc(s): return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def cluster(x, y, w, h, label, stroke, fill, dashed=False, sub=None):
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    A(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1"{dash}/>')
    A(f'<text x="{x+12}" y="{y+18}" class="cl" fill="{stroke}">{esc(label)}</text>')
    if sub:
        A(f'<text x="{x+12}" y="{y+31}" class="bs" fill="{MUTED}">{esc(sub)}</text>')

def box(x, y, w, h, title, lines, stroke, fill, bold=False, dashed=False):
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    A(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{fill}" stroke="{stroke}" stroke-width="{1.8 if bold else 1}"{dash}/>')
    cy = y + 15 if lines else y + h/2 + 4
    A(f'<text x="{x+w/2}" y="{cy}" class="bt" text-anchor="middle" fill="{INK}">{esc(title)}</text>')
    for ln in lines:
        cy += 12
        A(f'<text x="{x+w/2}" y="{cy}" class="bs" text-anchor="middle" fill="{MUTED}">{esc(ln)}</text>')

def edge(pts, color, label=None, lx=None, ly=None, rot=False, dashed=False, dotted=False, both=False, bold=False, anchor="start", head=True):
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    style = ' stroke-dasharray="6 4"' if dashed else (' stroke-dasharray="2 3"' if dotted else "")
    m = ' marker-end="url(#h)"' if head else ""
    if both: m += ' marker-start="url(#h)"'
    A(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{2 if bold else 1.3}"{style}{m}/>')
    if label:
        t = f' transform="rotate(-90 {lx} {ly})"' if rot else ""
        A(f'<text x="{lx}" y="{ly}" class="el" fill="{color}" text-anchor="{anchor}"{t}>{esc(label)}</text>')

def tag(x, y, text, color):
    w = 8 + 6.2 * len(text)
    A(f'<rect x="{x}" y="{y-9}" width="{w}" height="12" rx="6" fill="{color}"/>')
    A(f'<text x="{x+w/2}" y="{y}" class="tg" text-anchor="middle" fill="#FFFFFF">{esc(text)}</text>')

# ------------------------------------------------------------------ header
A(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t d">')
A('<title id="t">AINS platform: components and flows, phase by phase</title>')
A('<desc id="d">Two columns left to right. Phase 1 builds the platform repo ains-platform directly from the workstation, edges P1 to P7. The platform runtime, used only by onboarded projects, holds Jira, the ains-orchestrator container with the Claude Code CLI stage agents, reusable workflows and credentials; agent usage is traced by edges A1 to A7. A placeholder on the right stands for onboarded projects, the sandbox in Phase 1 and ains-poc in Phase 2; diagram 2 covers a project story. Blue arrows are human to machine, teal arrows machine to machine.</desc>')
A('<defs><marker id="h" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
  '<path d="M1 1 L9 5 L1 9" fill="none" stroke="context-stroke" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></marker>'
  '<style>text{font-family:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif}'
  '.cl{font-size:11.5px;font-weight:600;letter-spacing:.02em}.bt{font-size:11.5px;font-weight:600}.bs{font-size:9.5px}.el{font-size:9px}.tg{font-size:8.5px;font-weight:700}</style></defs>')
A(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>')

# ------------------------------------------------------------------ human roles band
A(f'<text x="30" y="18" class="cl" fill="{SAND}">HUMAN ROLES</text>')
A(f'<text x="130" y="18" class="bs" fill="{MUTED}">in this solo PoC one person holds every role; they are drawn separately to show the hand-offs. Phase 2 roles are in diagram 2.</text>')
box(40, 24, 420, 82, "Phase 1 roles",
    ["Platform engineer:  builds ains-platform directly in VS Code + Claude Code CLI, commits to main",
     "Platform engineer:  runs Jira setup, secret placement, bootstrap, then the acceptance story",
     "Reviewer:  approves G1–G5 on the acceptance story, so every gate is exercised once",
     "Researcher:  records divergences and gaps (docs/divergence.md)"], SAND, SAND_BG)
box(540, 24, 420, 82, "Runtime roles  (used only for onboarded projects)",
    ["Product owner:  writes project stories in Jira, sets Execution Lane, orders the backlog",
     "Reviewer:  G1 spec and G2 plan decisions in Jira, Rework comments, Blocked calls",
     "Platform engineer:  keeps the container running, rotates tokens, tunes gates",
     "(non-human) Claude Code CLI stage agents:  act under the engineer's Max subscription"], SAND, SAND_BG)

# ------------------------------------------------------------------ columns
cluster(30, 120, 450, 690, "PHASE 1 · build the platform", TEAL, "#F2F8F7", sub="repo ains-platform · built directly from the workstation · no Jira, CI or orchestrator for the platform · P1–P7")
cluster(510, 120, 470, 690, "PLATFORM RUNTIME · built in Phase 1, used only by onboarded projects", GREY, "#F6F5F1", sub="Jira Cloud · ains-orchestrator + portal containers · reusable Actions workflows · credentials · agent usage A1–A7")

# ---------------- column A (Phase 1)
box(55, 160, 400, 56, "Workstation: VS Code + Claude Code CLI",
    ["interactive sessions write and test the orchestrator", "same toolchain the Phase 2 developer uses"], TEAL, TEAL_BG)
box(55, 240, 400, 56, "Jira onboarding  (part of ains target add)",
    ["per project: Kanban project · label ai-native as the lane marker · 12 statuses via REST", "Automation rule (audit trace, optional) · profile: class · phases · method · autonomy"], GREY, GREY_BG)
box(55, 320, 400, 86, "Repo ains-platform  (GitHub, code only, no Actions)",
    ["ains-orchestrator/  src · Dockerfile · compose · flows · agents · targets.yaml · profiles.yaml",
     ".github/workflows/  reusable-ci · reusable-release · reusable-jira-callback · intake-ack",
     "   (workflow_call definitions; they run only when ains-poc calls them)",
     "bootstrap/templates · docs/ · plan/ · ains-platform.cmd · .claude/skills · commits straight to main"], TEAL, TEAL_BG, bold=True)
box(55, 430, 400, 58, "Secrets placement",
    ["claude setup-token → CLAUDE_CODE_OAUTH_TOKEN · GitHub PAT · Jira token", "→ .env (gitignored); ains target add copies Jira secrets into each repo · never committed"], SAND, SAND_BG)
box(55, 512, 400, 58, "ains target add / remove  →  project onboarding",
    ["any project: Jira project + repo + protection + environments + secrets", "+ label ai-review + templates · targets.yaml entry · proven on the sandbox"], TEAL, TEAL_BG)
box(55, 594, 400, 70, "Acceptance run on the sandbox  =  Phase 1 done",
    ["AINSSB-2 'repository scaffold' through all phases and five gates, hands-off (done 2026-11-03)",
     "ains-poc is never touched in Phase 1; it is onboarded on Phase 2 day one",
     "docs/divergence.md records the gaps vs. the architecture document"], GREY, GREY_BG)
box(55, 688, 400, 46, "Platform changes after Phase 1",
    ["engineer edits ains-platform directly; image rebuilt with compose; no story lane"], TEAL, TEAL_BG)

# ---------------- column B (runtime)
box(535, 160, 420, 56, "Jira Cloud · rayniloy.atlassian.net · one project per onboarded target",
    ["label ai-native marks the lane · Ready → Specifying → … → Done · Rework · Blocked", "Automation rule = audit trace · project stories only, never platform work"], GREY, GREY_BG)
cluster(525, 236, 440, 450, "Container: ains-orchestrator  (Docker Desktop on the laptop)", TEAL, "#E8F2F0", dashed=True)
box(545, 266, 400, 40, "Poller + PR watcher", ["JQL every 60 s over all targets: AI-native ∧ Ready · PRs ready or labelled ai-review"], TEAL, TEAL_BG)
box(545, 320, 400, 52, "Flow state machine + gates",
    ["flows/story.yaml · SQLite on /work (resumable)", "spec → G1 → plan → G2 → implement → review → G3 → merge · gates per target"], TEAL, TEAL_BG)
box(545, 386, 400, 52, "Dispatcher",
    ["targets.yaml: one block per onboarded project, empty at start", "agents/claude-code.yaml + agents/stages/*.md: one prompt and permission set per stage"], TEAL, TEAL_BG)
box(545, 452, 195, 56, "Claude Code CLI  (stage agents)", ["spawned per stage: spec · plan ·", "implement · review · headless · OAuth only"], TEAL, TEAL_BG, bold=True)
box(750, 452, 195, 56, "Spec Kit skills  (specify)", ["run inside the agent's session", "integration key: claude"], TEAL, TEAL_BG)
box(545, 522, 400, 40, "Volumes", ["/work/<KEY>/ clones (throwaway) · /data: sessions.sqlite · heartbeat · logs/ (durable)"], TEAL, TEAL_BG)
box(535, 578, 200, 56, ".env  (gitignored)", ["CLAUDE_CODE_OAUTH_TOKEN · GITHUB_TOKEN", "JIRA_EMAIL · JIRA_API_TOKEN"], SAND, SAND_BG)
box(755, 578, 200, 56, "Claude Max subscription", ["plan usage, no API metering", "serves every claude -p run"], ROSE, ROSE_BG)
box(545, 642, 400, 38, "Management portal  (ains portal · second container · localhost:8787)",
    ["dashboard · projects (add · verify · remove) · stories with the state map · same /data store"], TEAL, TEAL_BG)
box(535, 710, 420, 56, "Reusable Actions workflows  (defined in ains-platform)",
    ["ci · release · jira-callback · intake-ack  (workflow_call)", "executed only by the thin workflows of onboarded repos"], GREY, GREY_BG)

# ---------------- PoC placeholder (details in diagram 2)
box(1010, 236, 260, 450, "ONBOARDED PROJECTS", [], POC, "#F3F8F2", dashed=True)
A(f'<text x="1140" y="286" class="bs" text-anchor="middle" fill="{MUTED}">one block per project, created by ains target add:</text>')
A(f'<text x="1140" y="300" class="bs" text-anchor="middle" fill="{MUTED}">Jira project · GitHub repo · thin CI and release</text>')
A(f'<text x="1140" y="313" class="bs" text-anchor="middle" fill="{MUTED}">workflows · GHCR · environments · labels · templates</text>')
A(f'<text x="1140" y="326" class="bs" text-anchor="middle" fill="{MUTED}">Phase 1: sandbox AINSSB · Phase 2: ains-poc AINSPOC</text>')
A(f'<text x="1140" y="360" class="bt" text-anchor="middle" fill="{POC}">see diagram 2</text>')
A(f'<text x="1140" y="374" class="bs" text-anchor="middle" fill="{POC}">ains-poc-flow: one story phase by phase,</text>')
A(f'<text x="1140" y="387" class="bs" text-anchor="middle" fill="{POC}">the four stage agents, gates G1–G5</text>')
A(f'<text x="1140" y="640" class="bs" text-anchor="middle" fill="{MUTED}">onboarded by P6 (ains target add) · receives A5–A7</text>')

# ================================================================== edges
# ---------- human <-> machine (blue)
edge([(250, 106), (250, 160)], HUMAN, bold=True); tag(256, 117, "P1", HUMAN); A(f'<text x="276" y="117" class="el" fill="{HUMAN}">build · run · debug · commit</text>')
edge([(560, 106), (560, 160)], HUMAN, bold=True); A(f'<text x="566" y="117" class="el" fill="{HUMAN}">Jira UI: project stories · lane · G1/G2 decisions</text>')

# ---------- Phase 1 (teal), P-series
edge([(55, 188), (42, 188), (42, 541), (55, 541)], MACH)
for lab, yy in (("P2", 268), ("P3", 363), ("P5", 459)):
    edge([(42, yy), (55, yy)], MACH); tag(14, yy - 4, lab, MACH)
tag(14, 537, "P6", MACH)
edge([(455, 268), (500, 268), (500, 192), (535, 192)], MACH, dotted=True); tag(470, 262, "P2", MACH); A(f'<text x="458" y="282" class="el" fill="{MACH}">configures</text>')
edge([(455, 380), (525, 380)], MACH, dotted=True); tag(474, 374, "P4", MACH); A(f'<text x="462" y="394" class="el" fill="{MACH}">compose up</text>')
edge([(455, 459), (495, 459), (495, 606), (535, 606)], MACH, dotted=True); tag(499, 600, "P5", MACH); A(f'<text x="503" y="618" class="el" fill="{MACH}">fills</text>')
tag(62, 607, "P7", MACH)
# P6 bootstrap provisions the PoC: routed under column B
edge([(455, 541), (490, 541), (490, 800), (1140, 800), (1140, 686)], MACH, dotted=True)
tag(760, 796, "P6", MACH); A(f'<text x="784" y="796" class="el" fill="{MACH}">ains target add: Jira project, repo, protection, environments, secrets, label, templates, targets entry</text>')

# ---------- runtime agent usage (teal), A-series
edge([(955, 266), (966, 266), (966, 296), (955, 296)], MACH, both=True); tag(970, 284, "A1", MACH)
edge([(955, 266), (966, 266), (966, 205), (955, 205)], MACH, both=True)
A(f'<text x="978" y="300" class="el" fill="{MACH}" transform="rotate(-90 978 300)">poll · claim</text>')
edge([(745, 306), (745, 320)], MACH)
edge([(745, 372), (745, 386)], MACH); tag(750, 384, "A2", MACH); A(f'<text x="774" y="384" class="el" fill="{MACH}">stage → spawn agent</text>')
edge([(640, 438), (640, 452)], MACH, bold=True)
edge([(845, 438), (845, 452)], MACH)
edge([(740, 480), (750, 480)], MACH)
edge([(640, 508), (640, 522)], MACH); tag(646, 519, "A3", MACH); A(f'<text x="670" y="519" class="el" fill="{MACH}">writes spec · plan · code · review into the clone</text>')
edge([(560, 508), (560, 568), (760, 568), (760, 578)], MACH); tag(600, 565, "A4", MACH); A(f'<text x="624" y="565" class="el" fill="{MACH}">model calls</text>')
# A5 push / PR / review / merge -> PoC repo
edge([(945, 542), (1010, 542)], MACH, bold=True); tag(950, 536, "A5", MACH)
A(f'<text x="978" y="556" class="el" fill="{MACH}" text-anchor="middle">push · PR · merge</text>')
# A6 review agent reads the PR (PR watcher) <- PoC repo
edge([(1010, 470), (980, 470), (980, 430), (955, 430)], MACH, both=True); tag(984, 455, "A6", MACH)
A(f'<text x="992" y="470" class="el" fill="{MACH}" transform="rotate(-90 992 470)">PR ready → review</text>')
# A7 callbacks from the PoC's Actions via the reusable workflows to Jira
edge([(1010, 738), (955, 738)], MACH, dotted=True); tag(968, 752, "A7", MACH)
edge([(900, 710), (900, 698), (976, 698), (976, 212), (955, 212)], MACH, dotted=True)
A(f'<text x="992" y="752" class="el" fill="{MACH}">workflow_call · Jira callbacks</text>')

# ------------------------------------------------------------------ legend
lx, ly = 55, 756
A(f'<line x1="{lx}" y1="{ly-4}" x2="{lx+30}" y2="{ly-4}" stroke="{HUMAN}" stroke-width="2" marker-end="url(#h)"/>')
A(f'<text x="{lx+38}" y="{ly}" class="bs" fill="{INK}">human ↔ machine</text>')
A(f'<line x1="{lx+150}" y1="{ly-4}" x2="{lx+180}" y2="{ly-4}" stroke="{MACH}" stroke-width="1.3" marker-end="url(#h)"/>')
A(f'<text x="{lx+188}" y="{ly}" class="bs" fill="{INK}">machine ↔ machine</text>')
tag(lx, ly + 16, "P1–P7", MACH); A(f'<text x="{lx+44}" y="{ly+16}" class="bs" fill="{INK}">Phase 1 platform build steps</text>')
tag(lx + 200, ly + 16, "A1–A7", MACH); A(f'<text x="{lx+244}" y="{ly+16}" class="bs" fill="{INK}">agent usage in the AI-native loop</text>')
A(f'<text x="{lx}" y="{ly+32}" class="bs" fill="{MUTED}">solid = runtime · dotted = build-time, definition or callback · ◂▸ both ways · project story phases D1–D7 and gates G1–G5: diagram 2</text>')

A('</svg>')
svg = "\n".join(out)
here = Path(__file__).parent
(here / "ains-architecture.svg").write_text(svg, encoding="utf-8")

html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AINS architecture</title>
<style>
:root{{--bg:#F6F5F1;--surface:#FFFFFF;--fg:#1B1F23;--muted:#5B6168;--border:#DAD7CF;--accent:#146B63;--code:#EEEBE2;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#14181C;--surface:#1C2126;--fg:#E7E5DF;--muted:#9AA0A6;--border:#2B3136;--accent:#59C2B8;--code:#11161B;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#14181C;--surface:#1C2126;--fg:#E7E5DF;--muted:#9AA0A6;--border:#2B3136;--accent:#59C2B8;--code:#11161B;color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14.5px/1.6 "IBM Plex Sans",system-ui,sans-serif;padding:0 16px}}
.wrap{{max-width:1400px;margin:0 auto;padding:32px 0 64px}}
h1{{font-size:24px;font-weight:500;margin:0 0 4px}}
h2{{font-size:17px;font-weight:500;margin:36px 0 10px;border-top:1px solid var(--border);padding-top:10px}}
.lede{{color:var(--muted);margin:0 0 16px;max-width:100ch}}
.frame{{background:#fff;border:1px solid var(--border);border-radius:12px;padding:12px;overflow-x:auto}}
.frame svg{{display:block;min-width:1100px;width:100%;height:auto}}
.cap{{font-size:12.5px;color:var(--muted);margin:8px 0 0}}
code{{font-family:"IBM Plex Mono",monospace;font-size:12.5px;background:var(--code);padding:1px 6px;border-radius:4px}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0 18px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top}}
th{{color:var(--muted);font-weight:500;font-size:11.5px;text-transform:uppercase;letter-spacing:.03em}}
.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:0 32px}}
p{{max-width:80ch}}
</style></head><body><div class="wrap">
<p class="lede">AINS · AI-native SDLC research · diagram 1 of 2 · 2026-11-02</p>
<h1>The platform: components and flows by phase</h1>
<p class="lede">Left: Phase 1 builds the platform directly from the workstation (steps P1–P7). Middle: the platform runtime, Jira, orchestrator and reusable Actions workflows, built in Phase 1 but used only by onboarded projects (agent usage A1–A7). Right: a placeholder for onboarded projects, the sandbox in Phase 1 and <code>ains-poc</code> in Phase 2, both created by <code>ains target add</code>. The story phases D1–D7, the four stage agents and the gates G1–G5 are in <a href="ains-poc-flow.html">diagram 2</a>. Blue arrows are people interacting with machines, teal arrows are machines talking to machines.</p>
<div class="frame">
{svg}
</div>
<p class="cap">Source: <code>plan/ains-architecture-gen.py</code> regenerates this page and <code>plan/ains-architecture.svg</code>. Diagram 2: <code>plan/ains-poc-flow.html</code>.</p>

<div class="cols">
<div>
<h2>P · Phase 1 platform build steps</h2>
<table>
<tr><th>#</th><th>Step</th><th>Lands in</th></tr>
<tr><td>P1</td><td>Platform engineer builds, runs, debugs and commits the orchestrator interactively; no story lane, no CI for the platform</td><td>workstation</td></tr>
<tr><td>P2</td><td>Jira side of project onboarding: Kanban project, Execution Lane field, statuses, Automation rule, built as a generic capability; the platform itself has no Jira project</td><td>Jira Cloud</td></tr>
<tr><td>P3</td><td>Push orchestrator, flows, agents, stage prompts, reusable workflow definitions, bootstrap and docs straight to main</td><td>repo ains-platform</td></tr>
<tr><td>P4</td><td><code>docker compose up</code> the orchestrator container on the laptop</td><td>container</td></tr>
<tr><td>P5</td><td>Secret placement: OAuth token from <code>claude setup-token</code>, GitHub PAT, Jira token into <code>.env</code> and the PoC repo's Actions secrets</td><td>.env, secrets</td></tr>
<tr><td>P6</td><td><code>ains target add</code> onboards any project: Jira project, repo, branch protection, environments, secrets, label ai-review, templates, targets entry; <code>ains target remove</code> reverses it. Proven twice on sandbox projects</td><td>onboarded projects</td></tr>
<tr><td>P7</td><td>Acceptance: AINSSB-1 in the sandbox through every phase and all five gates hands-off. ains-poc is onboarded only on Phase 2 day one</td><td>sandbox project</td></tr>
</table>
</div>
<div>
<h2>A · Agent usage in the AI-native loop</h2>
<table>
<tr><th>#</th><th>What the platform does</th><th>Actor</th></tr>
<tr><td>A1</td><td>Poll every onboarded project by JQL; claim the story (→ Specifying, session comment, state in SQLite)</td><td>Poller ↔ Jira</td></tr>
<tr><td>A2</td><td>State machine picks the stage; dispatcher resolves the target, the stage prompt and the permission set, and spawns a fresh Claude Code CLI process</td><td>state machine → dispatcher → agent</td></tr>
<tr><td>A3</td><td>The agent writes its output into the clone: spec.md, plan.md and tasks.md, code and tests, or a PR review</td><td>agent → workspace</td></tr>
<tr><td>A4</td><td>Model calls go through the Claude Code CLI on the Max subscription; the orchestrator never calls a model</td><td>agent ↔ Claude Max</td></tr>
<tr><td>A5</td><td>Push the branch, open or update the PR, mark it ready; after human G3 approval and green CI: squash merge</td><td>orchestrator → target repo</td></tr>
<tr><td>A6</td><td>PR watcher sees a ready PR (or a Human-lane PR labelled ai-review) and spawns the review agent, which posts a PR review and never merges</td><td>target repo → orchestrator → agent</td></tr>
<tr><td>A7</td><td>The PoC's thin workflows call the reusable ones, which call back to Jira: Code Review with PR link, Staging, Done; Automation's intake-ack comment</td><td>Actions → Jira</td></tr>
</table>
<p>Gates G1–G5, the phases D1–D7 and the four stage agents are drawn in diagram 2.</p>
</div>
</div>
</div></body></html>
"""
(here / "ains-architecture.html").write_text(html, encoding="utf-8")
print("wrote ains-architecture.svg and ains-architecture.html")
