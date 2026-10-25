"""Central knobs. Everything a reviewer should look at when changing behaviour lives here.

Model names are placeholders: confirm them against the models your project can call, and edit
ALLOWED_MODELS in code (skill authors cannot widen it from a SKILL.md).
"""

# Model for skills that do not declare `model:` in their front matter.
DEFAULT_MODEL = "gemini-2.5-flash"

# Model for the orchestrator (routing + merging). Keep it fast: it runs on every turn.
ROUTER_MODEL = "gemini-2.5-flash"

# Models a SKILL.md may select. Anything else fails at load time.
ALLOWED_MODELS = frozenset(
    {
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    }
)

# Longest context_summary passed from the orchestrator to a sub-agent (characters).
# Longer summaries are truncated and the orchestrator is told in the tool result.
MAX_CONTEXT_SUMMARY_CHARS = 600

# Wall-clock limit for one delegation. Protects parallel fan-out from one hung sub-agent.
SUBTASK_TIMEOUT_S = 90

assert DEFAULT_MODEL in ALLOWED_MODELS, "DEFAULT_MODEL must be on the allowlist"
assert ROUTER_MODEL in ALLOWED_MODELS, "ROUTER_MODEL must be on the allowlist"
