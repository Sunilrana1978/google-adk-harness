# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                                   # or: pip install google-adk pytest pyyaml google-agents-cli
python -m pytest -q                       # full offline suite: no model, no network
python -m pytest tests/test_delegate.py::test_timeout_is_reported -q   # single test
adk web                                   # local chat UI (needs GOOGLE_API_KEY or Vertex AI credentials)
adk web --otel_to_cloud                   # same, exporting spans/logs to Cloud Trace/Logging
agents-cli eval run --dataset tests/eval/datasets/skill-selection-dataset.json \
                    --config tests/eval/eval_config.yaml   # needs a live model + GCP project
```

There is no linter or build step configured. Python >= 3.11, `google-adk>=1.17` (developed on 2.13).

## Architecture

A generic Google ADK harness: one orchestrator agent plus Markdown skills. **New business workflows are new `skills/<name>/SKILL.md` files, not new Python** — `app/agent.py` is meant to stay unchanged per workflow.

Request flow:
1. `app/agent.py` builds `root_agent` (on `config.ROUTER_MODEL`) whose instruction is a *callable* (`build_instruction`) rebuilt every turn from `skill_index()` — only one line per skill is in context (progressive loading). `App` adds `EventsCompactionConfig`. ADK discovers `root_agent`/`app` via `app/__init__.py`.
2. The orchestrator's only tool is `delegate_task(skill_name, task, context_summary)` (`app/delegate.py`). It builds a sub-agent on the fly from the skill: the skill body + capped summary as instruction, the skill's `model`, and **only** the tools the skill lists from `TOOL_REGISTRY`. It runs in a fresh `InMemoryRunner` session and returns just the final text as `{"skill", "status", "result"|"error"}`. Status is one of `ok`, `empty`, `unknown_skill`, `invalid_skill`, `timeout`, `error` — failures are returned, never raised, so parallel siblings survive.
3. Independent parts → multiple `delegate_task` calls in one model turn; ADK runs each as its own asyncio task, so they run concurrently.

Key invariants (each is covered by a test; keep them):
- **Never block the event loop.** ADK runs sync tools directly on the loop, so any tool doing I/O must be `async def` (see `run_python` / `app/sandbox.py`, which uses an async subprocess). A blocking tool serializes all parallel delegations.
- **Instructions are callables, not strings**, so ADK's `{var}` templating never touches braces in skill bodies or summaries.
- `delegate_task` keeps no shared mutable state.
- Logs never include task or summary text (may contain user data); only the correlation id, skill, model, status, timings.

Skills (`app/skill_loader.py`):
- Front matter keys: `name` (required, must equal folder name, `[a-z0-9-]`), `description` (required, becomes the index line), `tools` (comma-separated names from `TOOL_REGISTRY` in `app/tools.py`), `model` (must be in `config.ALLOWED_MODELS`; defaults to `DEFAULT_MODEL`). Unknown keys, unknown tools, or disallowed models raise `SkillError`.
- `load_all()` runs strictly at import of `app/agent.py`, so a bad skill stops startup. At runtime loading is lenient: a broken file only hides that skill (logged at ERROR).
- Parsing is cached by (mtime, size); validation re-runs every load so it sees the current registry/allowlist.

Other modules:
- `app/config.py` — all knobs: models + allowlist (model names are placeholders), `MAX_CONTEXT_SUMMARY_CHARS` (600; longer summaries truncated with a `warnings` entry in the result), `SUBTASK_TIMEOUT_S`.
- `app/guardrails.py` — `before_tool_guard`, attached to both root and sub-agents; returning a dict skips the tool. Currently blocks dangerous snippets in `run_python` code.
- `app/sandbox.py` — runs model-written Python with `-I`, empty env, temp cwd, rlimits on CPU/memory/file writes/processes. It cannot block network access.
- `app/telemetry.py` — a `SpanProcessor` stamps `harness.correlation_id` (= parent session id) on every span started during a delegation, including ADK's own sub-agent spans; installed lazily at delegation time. Sub-agent session ids are `<parent session>:<skill>:<random>`.
- `app/tools.py` — mock business tools (plain functions, referenced by name only so they can move behind MCP later).
- `deploy/iam.sh` — least-privilege roles for the Agent Identity principal; must be re-run after every deploy (`./deploy/iam.sh remove` to clean up). Its principal path is a placeholder.

## Tests

- Tests run the **real** ADK runner with scripted models. `tests/fakes.py` provides `ScriptedLlm` (orchestrator) and `SkillRouterLlm` (one fake for all sub-agents, choosing a script by which skill body is in the instruction). `tests/harness.py`'s `Harness` monkeypatches `delegate._resolve_model` (the test seam) and runs one user turn; its `MARKERS` map each skill to a heading in its SKILL.md body.
- `tests/conftest.py` installs a global in-memory OTel provider in `pytest_configure` (before ADK creates its tracer); use the `spans` fixture to assert on spans.
- On macOS the sandbox tests fail: `setrlimit` in `_LAUNCHER` raises `ValueError: current limit exceeds maximum limit`, so every `run_python` call returns empty stdout. The sandbox's rlimits assume Linux.
- Eval: `tests/eval/skill_selection.py` is an agents-cli custom metric that is inlined as source text, so it must stay standard-library only, with no project imports and no `__file__`.

## Adding a skill

Create `skills/<name>/SKILL.md`, then update the places tests check for drift:
- add a single-skill case to `tests/eval/datasets/skill-selection-dataset.json`, plus its entry in `EXPECTED` and `KNOWN_SKILLS` in `tests/eval/skill_selection.py`;
- add its body heading to `MARKERS` in `tests/harness.py` if harness tests should route to it.

A new tool goes in `app/tools.py` and must be added to `TOOL_REGISTRY`; a new model must be added to `ALLOWED_MODELS`.
