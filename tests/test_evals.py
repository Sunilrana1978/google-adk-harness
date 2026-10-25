"""The agents-cli eval set: well-formed, complete, and its metric works on REAL traces.

Nothing here calls a model or Google Cloud. What cannot be checked offline is how the real model
behaves, which is what `agents-cli eval run` is for.
"""
import asyncio
import importlib.util
import json
import pathlib
import sys

import pytest

from app import skill_loader
from fakes import calls, say
from harness import Harness, delegate_call

yaml = pytest.importorskip("yaml")
cli_types = pytest.importorskip("google.agents.cli._agent_platform_types").types
cli_events = pytest.importorskip("google.agents.cli.eval._events")
cli_eval_utils = pytest.importorskip("google.agents.cli.eval.eval_utils")

EVAL_DIR = pathlib.Path(__file__).resolve().parent / "eval"
DATASET = EVAL_DIR / "datasets" / "skill-selection-dataset.json"
CONFIG = EVAL_DIR / "eval_config.yaml"


def _load_metric_module():
    spec = importlib.util.spec_from_file_location("skill_selection", EVAL_DIR / "skill_selection.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["skill_selection"] = module
    spec.loader.exec_module(module)
    return module


metric_module = _load_metric_module()
CASES = json.loads(DATASET.read_text())["eval_cases"]


def prompt_of(case):
    return case["prompt"]["parts"][0]["text"]


@pytest.fixture(scope="module")
def metric():
    """The metric exactly as agents-cli compiles it from the config file (inlined source)."""
    metrics, local_count, remote_count = cli_eval_utils.prepare_eval_metrics(str(CONFIG), None)
    assert (len(metrics), local_count, remote_count) == (1, 1, 0)
    return metrics[0].custom_function


# --- the dataset itself -----------------------------------------------------------------------
def test_every_case_validates_against_the_agents_cli_schema():
    for case in CASES:
        cli_types.EvalCase.model_validate(case)
    ids = [c["eval_case_id"] for c in CASES]
    assert len(ids) == len(set(ids))


def test_dataset_and_expected_skills_do_not_drift():
    prompts = [prompt_of(c) for c in CASES]
    assert sorted(prompts) == sorted(metric_module.EXPECTED)
    for a in prompts:  # the metric matches by substring, so no prompt may contain another
        assert not any(a != b and b in a for b in prompts), a


def test_known_skills_match_the_skills_directory():
    on_disk = set(skill_loader.load_all())
    assert metric_module.KNOWN_SKILLS == on_disk
    for skills in metric_module.EXPECTED.values():
        assert set(skills) <= on_disk


def test_coverage_one_case_per_skill_plus_multi_intent_and_no_delegation():
    expected = list(metric_module.EXPECTED.values())
    for skill in skill_loader.load_all():
        assert [skill] in expected, f"no single-skill eval case for {skill}"
    assert sum(len(e) >= 2 for e in expected) >= 2, "need multi-intent questions"
    assert [] in expected, "need a question that must NOT be delegated"
    assert any(c["eval_case_id"].startswith("no_overtrigger") for c in CASES)


# --- the metric, run on traces produced by the real harness ----------------------------------
def real_agent_data(monkeypatch, skills):
    """Run the real orchestrator, then shape its events the way agents-cli does for grading."""
    if skills:
        root = [calls(*[delegate_call(s, "task", "context") for s in skills]), say("done")]
    else:
        root = [say("Hello! I can help with orders and sales.")]
    h = asyncio.run(Harness(monkeypatch, root, {s: [say("ok")] for s in skills}).run())
    parsed = [
        cli_events.parse_content_event(json.loads(e.model_dump_json(exclude_none=True, by_alias=True)))
        for e in h.events
    ]
    turn = cli_types.evals.ConversationTurn(turn_index=0, events=[p for p in parsed if p])
    return cli_types.evals.AgentData(turns=[turn])


def forms(agent_data):
    """The metric must not care how the grader hands the trace over."""
    as_dict = agent_data.model_dump(mode="json", exclude_none=True)
    return {"dict": as_dict, "json_text": json.dumps(as_dict), "repr": str(as_dict)}


def case_prompt(case_id):
    return prompt_of(next(c for c in CASES if c["eval_case_id"] == case_id))


@pytest.mark.parametrize(
    "case_id, delegated, score, explanation_has",
    [
        ("refund_single", ["refund-policy"], 1, "as expected"),
        ("multi_refund_and_sales", ["refund-policy", "sales-analysis"], 1, "as expected"),
        ("multi_status_and_refund", ["refund-policy", "order-support"], 1, "as expected"),
        ("trivial_greeting_no_delegation", [], 1, "no skill"),
        ("out_of_scope_no_delegation", [], 1, "no skill"),
        # failures: missing a needed skill
        ("multi_refund_and_sales", ["refund-policy"], 0, "missing ['sales-analysis']"),
        ("sales_single", [], 0, "missing ['sales-analysis']"),
        # failures: over-triggering
        ("no_overtrigger_status_only", ["order-support", "refund-policy"], 0, "over-triggered ['refund-policy']"),
        ("trivial_greeting_no_delegation", ["order-support"], 0, "over-triggered ['order-support']"),
        # failures: wrong skill
        ("order_status_single", ["sales-analysis"], 0, "over-triggered ['sales-analysis']"),
    ],
)
def test_metric_scores_real_traces(monkeypatch, metric, case_id, delegated, score, explanation_has):
    trace = real_agent_data(monkeypatch, delegated)
    for form_name, form in forms(trace).items():
        out = metric({"prompt": case_prompt(case_id), "response": "done", "agent_data": form})
        assert out["score"] == score, (form_name, out)
        assert explanation_has in out["explanation"], (form_name, out)


def test_the_real_trace_contains_what_the_metric_reads(monkeypatch):
    """Guards against a vacuous pass: the delegations must actually be visible in agent_data."""
    trace = real_agent_data(monkeypatch, ["refund-policy", "sales-analysis"])
    assert metric_module.delegated_skills(trace.model_dump(mode="json")) == {"refund-policy", "sales-analysis"}


def test_instruction_text_mentioning_skill_name_is_not_mistaken_for_a_call(metric):
    trace = {"agents": {"root": {"instruction": "Delegate with delegate_task(skill_name, task, context_summary)."}}}
    out = metric({"prompt": case_prompt("trivial_greeting_no_delegation"), "agent_data": trace})
    assert out["score"] == 1


def test_metric_fails_closed(metric):
    assert metric({"prompt": "a question nobody registered", "agent_data": {"x": 1}})["score"] == 0
    assert metric({"prompt": case_prompt("refund_single")})["score"] == 0  # no trace at all
    assert metric({"prompt": case_prompt("refund_single"), "agent_data": {}})["score"] == 0
