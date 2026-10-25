"""Skill loading: model field, allowlist, load-time validation, caching."""
import os

import pytest

from app import config, skill_loader
from app.skill_loader import SkillError

TOOLS = {"lookup_order": object(), "run_python": object()}  # a stand-in registry
MODELS = frozenset({"m-fast", "m-strong"})


def write_skill(root, name, *, tools="lookup_order", model=None, extra="", desc="Does a thing."):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    lines = [f"name: {name}", f"description: {desc}", f"tools: {tools}"]
    if model:
        lines.append(f"model: {model}")
    if extra:
        lines.append(extra)
    path = folder / "SKILL.md"
    path.write_text("---\n" + "\n".join(lines) + "\n---\n\n# Body\nDo the thing.\n")
    return path


def load(root, **kw):
    kw.setdefault("tool_registry", TOOLS)
    kw.setdefault("allowed_models", MODELS)
    kw.setdefault("default_model", "m-fast")
    return skill_loader.load_all(root, **kw)


@pytest.fixture(autouse=True)
def fresh_cache():
    skill_loader.clear_cache()
    yield
    skill_loader.clear_cache()


# --- model field -----------------------------------------------------------------------------
def test_model_field_is_parsed(tmp_path):
    write_skill(tmp_path, "analyst", model="m-strong")
    assert load(tmp_path)["analyst"].model == "m-strong"


def test_missing_model_falls_back_to_default(tmp_path):
    write_skill(tmp_path, "lookup")
    assert load(tmp_path)["lookup"].model == "m-fast"
    assert load(tmp_path, default_model="m-strong")["lookup"].model == "m-strong"


def test_model_not_on_allowlist_fails_loudly(tmp_path):
    write_skill(tmp_path, "sneaky", model="gpt-expensive")
    with pytest.raises(SkillError, match="model 'gpt-expensive' is not allowed"):
        load(tmp_path)


def test_real_project_skills_use_the_declared_models():
    skills = skill_loader.load_all()
    assert skills["refund-policy"].model == config.DEFAULT_MODEL  # declares nothing
    assert skills["order-support"].model == "gemini-2.5-flash"
    assert skills["sales-analysis"].model == "gemini-2.5-pro"
    assert all(s.model in config.ALLOWED_MODELS for s in skills.values())


# --- validation at load time -----------------------------------------------------------------
def test_unknown_tool_fails_at_load(tmp_path):
    write_skill(tmp_path, "broken", tools="lookup_order, delete_everything")
    with pytest.raises(SkillError, match=r"unknown tool\(s\) \['delete_everything'\]"):
        load(tmp_path)


def test_every_problem_is_reported_not_just_the_first(tmp_path):
    write_skill(tmp_path, "a", tools="nope")
    write_skill(tmp_path, "b", model="bad-model")
    with pytest.raises(SkillError) as err:
        load(tmp_path)
    assert "nope" in str(err.value) and "bad-model" in str(err.value)


def test_typo_in_a_key_is_rejected_not_ignored(tmp_path):
    write_skill(tmp_path, "typo", extra="modle: m-strong")
    with pytest.raises(SkillError, match="unknown front matter key"):
        load(tmp_path)


def test_missing_required_key_and_bad_front_matter(tmp_path):
    (tmp_path / "nodesc").mkdir()
    (tmp_path / "nodesc" / "SKILL.md").write_text("---\nname: nodesc\n---\nbody")
    with pytest.raises(SkillError, match="missing front matter"):
        load(tmp_path)
    skill_loader.clear_cache()
    (tmp_path / "nodesc" / "SKILL.md").write_text("no front matter at all")
    with pytest.raises(SkillError, match="front matter"):
        load(tmp_path)


def test_name_must_match_folder_and_be_safe(tmp_path):
    write_skill(tmp_path, "good")
    (tmp_path / "good" / "SKILL.md").write_text("---\nname: other\ndescription: x\n---\nb")
    with pytest.raises(SkillError, match="must match its folder name"):
        load(tmp_path)


def test_validation_uses_the_current_registry_even_when_parsing_is_cached(tmp_path):
    write_skill(tmp_path, "uses-run-python", tools="run_python")
    assert load(tmp_path)  # parsed and cached with run_python available
    with pytest.raises(SkillError, match="unknown tool"):
        load(tmp_path, tool_registry={"lookup_order": object()})  # same file, tool now gone


# --- caching ---------------------------------------------------------------------------------
def test_files_are_parsed_once_then_served_from_cache(tmp_path):
    write_skill(tmp_path, "one")
    write_skill(tmp_path, "two")
    before = dict(skill_loader.STATS)
    load(tmp_path)
    assert skill_loader.STATS["parses"] - before["parses"] == 2
    load(tmp_path)
    load(tmp_path)
    assert skill_loader.STATS["parses"] - before["parses"] == 2  # no re-parsing
    assert skill_loader.STATS["cache_hits"] - before["cache_hits"] == 4


def test_cache_is_invalidated_when_a_file_changes(tmp_path):
    path = write_skill(tmp_path, "edit-me", desc="old description")
    assert load(tmp_path)["edit-me"].description == "old description"
    path.write_text(path.read_text().replace("old description", "new description, longer"))
    assert load(tmp_path)["edit-me"].description == "new description, longer"


def test_cache_is_invalidated_by_mtime_alone(tmp_path):
    path = write_skill(tmp_path, "touch")
    load(tmp_path)
    parses = skill_loader.STATS["parses"]
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))  # same content and size
    load(tmp_path)
    assert skill_loader.STATS["parses"] == parses + 1


def test_deleted_and_added_skills_are_picked_up(tmp_path):
    write_skill(tmp_path, "stays")
    gone = write_skill(tmp_path, "goes")
    assert set(load(tmp_path)) == {"stays", "goes"}
    gone.unlink()
    gone.parent.rmdir()
    write_skill(tmp_path, "arrives")
    assert set(load(tmp_path)) == {"stays", "arrives"}


# --- runtime behaviour: one bad file must not take everything down ---------------------------
def test_runtime_lookup_isolates_a_broken_skill(tmp_path, caplog):
    write_skill(tmp_path, "healthy")
    write_skill(tmp_path, "broken", tools="not_a_tool")
    with caplog.at_level("ERROR", logger="harness.skills"):
        index = skill_loader.skill_index(tmp_path)
    assert "healthy" in index and "broken" not in index  # still usable, broken one hidden
    assert "not_a_tool" in caplog.text  # ...but logged loudly
    assert skill_loader.get_skill("healthy", tmp_path).name == "healthy"
    with pytest.raises(SkillError, match="not_a_tool"):
        skill_loader.get_skill("broken", tmp_path)
    assert skill_loader.get_skill("never-existed", tmp_path) is None
