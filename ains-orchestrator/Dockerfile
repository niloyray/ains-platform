# ains-orchestrator image: Python 3.12, git, gh, Node 22 (for the Claude Code CLI), uv, Spec Kit, the orchestrator.
# Build context: the ains-orchestrator directory.  `docker compose up -d` runs `ains poll`.
FROM python:3.12-slim

ARG CLAUDE_CODE_VERSION=latest
ARG NODE_MAJOR=22
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    HOME=/root \
    AINS_CONFIG_ROOT=/app \
    AINS_WORK_DIR=/work \
    DISABLE_AUTOUPDATER=1

# base tools, GitHub CLI, Node.js
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git gnupg jq openssh-client ripgrep \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get update && apt-get install -y --no-install-recommends gh nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# uv, Spec Kit, Claude Code CLI
RUN pip install --no-cache-dir uv \
    && uv tool install specify-cli \
    && npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && ln -sf /root/.local/bin/specify /usr/local/bin/specify
ENV PATH="/root/.local/bin:${PATH}"

# the orchestrator
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev && ln -sf /app/.venv/bin/ains /usr/local/bin/ains
# commit identity for the orchestrator (overridable with AINS_GIT_NAME / AINS_GIT_EMAIL)
RUN git config --global user.name "ains-orchestrator" \
    && git config --global user.email "ains-orchestrator@users.noreply.github.com" \
    && git config --global --add safe.directory "*"
COPY targets.yaml claude-settings.json ./
COPY flows ./flows
COPY agents ./agents
COPY schemas ./schemas

# the Claude Code review plugin is optional: the review prompt is self-contained
RUN mkdir -p /work \
    && (claude plugin marketplace add anthropics/claude-plugins-official >/dev/null 2>&1 \
        && claude plugin install code-review@claude-plugins-official >/dev/null 2>&1 \
        || echo "code-review plugin not installed at build time (optional)")

VOLUME ["/work"]
HEALTHCHECK --interval=5m --timeout=60s --start-period=30s --retries=2 CMD ains doctor --no-live || exit 1
CMD ["ains", "poll"]
