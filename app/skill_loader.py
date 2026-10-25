"""Skill files: progressive loading (4.1) + declarative domain ownership (4.3).

The orchestrator only sees a short INDEX (name + description per skill). The full SKILL.md body
is used only when the orchestrator delegates to that skill.

Skill files are a security surface: whoever can edit one changes an agent's instructions, tools
and model. So they are validated strictly when loaded:
  * required keys present, no unknown keys (a typo like `modle:` must not silently use a default)
  * every tool name exists in TOOL_REGISTRY
  * `model:` is on the allowlist in config.py (missing => DEFAULT_MODEL)

Parsing is cached per file (invalidated when mtime or size changes, or on restart). Validation is
cheap and re-runs on every load so it always uses the current registry and allowlist.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from . import config
from .tools import TOOL_REGISTRY

log = logging.getLogger("harness.skills")

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

_ALLOWED_KEYS = {"name", "description", "tools", "model"}
_REQUIRED_KEYS = {"name", "description"}
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")  # also used to build the sub-agent's name


class SkillError(ValueError):
    """A skill file is malformed or refers to something that does not exist."""


@dataclass
class Skill:
    name: str
    description: str
    tools: list[str]
    model: str  # resolved: the declared model, or DEFAULT_MODEL
    body: str


@dataclass
class _Raw:
    """Syntactically valid skill, not yet checked against the registry or allowlist."""

    path: Path
    meta: dict[str, str]
    body: str


# path -> ((mtime_ns, size), _Raw)
_CACHE: dict[Path, tuple[tuple[int, int], _Raw]] = {}
STATS = {"parses": 0, "cache_hits": 0}  # lets tests (and curious humans) observe caching


def clear_cache() -> None:
    _CACHE.clear()


def _parse_raw(path: Path) -> _Raw:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if not text.startswith("---") or len(parts) != 3:
        raise SkillError(f"{path}: must start with a '---' front matter block")
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise SkillError(f"{path}: bad front matter line {line!r} (expected 'key: value')")
        meta[key.strip()] = value.strip()
    unknown = set(meta) - _ALLOWED_KEYS
    if unknown:
        raise SkillError(f"{path}: unknown front matter key(s) {sorted(unknown)}")
    missing = _REQUIRED_KEYS - set(meta)
    if missing:
        raise SkillError(f"{path}: missing front matter key(s) {sorted(missing)}")
    return _Raw(path=path, meta=meta, body=parts[2].strip())


def _raw_cached(path: Path) -> _Raw:
    stat = path.stat()
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(path)
    if cached and cached[0] == signature:
        STATS["cache_hits"] += 1
        return cached[1]
    raw = _parse_raw(path)  # raises SkillError; failures are not cached
    _CACHE[path] = (signature, raw)
    STATS["parses"] += 1
    return raw


def _validate(raw: _Raw, tool_registry, allowed_models, default_model) -> Skill:
    name = raw.meta["name"]
    if not _NAME_RE.match(name):
        raise SkillError(f"{raw.path}: name {name!r} must be lowercase letters, digits and dashes")
    if raw.path.parent.name != name:
        raise SkillError(f"{raw.path}: name {name!r} must match its folder name {raw.path.parent.name!r}")
    tools = [t.strip() for t in raw.meta.get("tools", "").split(",") if t.strip()]
    unknown_tools = [t for t in tools if t not in tool_registry]
    if unknown_tools:
        raise SkillError(
            f"{raw.path}: unknown tool(s) {unknown_tools}; registered tools: {sorted(tool_registry)}"
        )
    model = raw.meta.get("model") or default_model
    if model not in allowed_models:
        raise SkillError(f"{raw.path}: model {model!r} is not allowed; allowed: {sorted(allowed_models)}")
    return Skill(name=name, description=raw.meta["description"], tools=tools, model=model, body=raw.body)


def _load(skills_dir, tool_registry, allowed_models, default_model):
    """Load every skill. Returns (skills_by_name, [(folder_name, error_message), ...])."""
    skills_dir = Path(skills_dir).resolve()
    tool_registry = TOOL_REGISTRY if tool_registry is None else tool_registry
    allowed_models = config.ALLOWED_MODELS if allowed_models is None else allowed_models
    default_model = config.DEFAULT_MODEL if default_model is None else default_model

    paths = sorted(skills_dir.glob("*/SKILL.md"))
    for stale in [p for p in _CACHE if p.parent.parent == skills_dir and p not in paths]:
        del _CACHE[stale]  # file was deleted

    skills: dict[str, Skill] = {}
    errors: list[tuple[str, str]] = []
    for path in paths:
        try:
            skill = _validate(_raw_cached(path), tool_registry, allowed_models, default_model)
        except SkillError as exc:
            errors.append((path.parent.name, str(exc)))
            continue
        skills[skill.name] = skill  # names are unique: each must equal its (unique) folder name
    return skills, errors


def load_all(
    skills_dir: Path = SKILLS_DIR, *, tool_registry=None, allowed_models=None, default_model=None
) -> dict[str, Skill]:
    """Strict load: raises SkillError listing every problem. Use at startup so bad files fail fast."""
    skills, errors = _load(skills_dir, tool_registry, allowed_models, default_model)
    if errors:
        raise SkillError("invalid skill file(s):\n  " + "\n  ".join(msg for _, msg in errors))
    return skills


def _load_lenient(skills_dir) -> dict[str, Skill]:
    """Runtime load: a bad file is logged loudly and that skill is unavailable; others keep working."""
    skills, errors = _load(skills_dir, None, None, None)
    for _, message in errors:
        log.error("skill unavailable: %s", message)
    return skills


def skill_index(skills_dir: Path = SKILLS_DIR) -> str:
    """Cheap, always-in-context catalogue of available skills."""
    return "\n".join(f"- {s.name}: {s.description}" for s in _load_lenient(skills_dir).values())


def get_skill(name: str, skills_dir: Path = SKILLS_DIR) -> Skill | None:
    """The named skill, or None if there is no such skill.

    Raises SkillError if a file for it exists but is invalid (so callers can tell the
    difference between "no such skill" and "that skill is broken").
    """
    skills, errors = _load(skills_dir, None, None, None)
    if name in skills:
        return skills[name]
    for folder, message in errors:
        log.error("skill unavailable: %s", message)
        if folder == name:
            raise SkillError(message)
    return None
