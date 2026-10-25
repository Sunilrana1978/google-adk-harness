"""End-to-end tests of the REAL orchestrator + delegate_task with scripted models (no network).

These run the actual ADK runner, tool dispatch, sessions and OpenTelemetry spans. Only the model
is faked. They are what proves parallelism, isolation, per-skill models and span nesting.
"""
import asyncio

from app import agent as agent_module
from app import config, delegate
from fakes import calls, contents_text, say
from harness import MARKERS, Harness, delegate_call

def test_parallel_delegation_isolation_models_and_spans(monkeypatch, spans):
    async def scenario():
        barrier = asyncio.Barrier(2)  # both sub-agents must be mid-flight at the same moment

        async def rendezvous_then(call):
            # If delegations ran one after another, the first would wait here until timeout.
            await asyncio.wait_for(barrier.wait(), timeout=5)
            return call

        sub_scripts = {
            "refund-policy": [
                lambda r: rendezvous_then(calls(("lookup_order", {"order_id": "A100"}))),
                calls(("check_refund_eligibility", {"order_id": "A100"})),
                say("ELIGIBLE: delivered 5 days ago, inside the 30 day window."),
            ],
            "sales-analysis": [
                lambda r: rendezvous_then(calls(("run_sales_query", {"region": "west"}))),
                say("West revenue grew 50 percent from Aug to Oct."),
            ],
        }
        root_steps = [
            calls(
                delegate_call("refund-policy", "Check refund for order A100", "User wants a refund on A100, bought last week."),
                delegate_call("sales-analysis", "Trend for west region Aug-Oct", "User also asked about regional sales."),
            ),
            say("merged answer"),
        ]
        return await Harness(monkeypatch, root_steps, sub_scripts).run("refund A100 and west sales trend")

    h = asyncio.run(scenario())

    # --- the merged answer came back, and parallel calls really overlapped (barrier would have timed out)
    assert h.final_text == "merged answer"
    results = [
        r.response for e in h.events for r in (e.get_function_responses() or []) if r.name == "delegate_task"
    ]
    assert sorted(r["status"] for r in results) == ["ok", "ok"]
    assert {r["skill"] for r in results} == {"refund-policy", "sales-analysis"}

    # --- model per skill: refund-policy has no `model:` (default), sales-analysis declares Pro
    models = {s["skill"]: s["model"] for s in h.seen}
    assert models == {"refund-policy": config.DEFAULT_MODEL, "sales-analysis": "gemini-2.5-pro"}

    # --- the summary reaches the sub-agent, and each sub-agent sees only its own task
    by_skill = {s["skill"]: s for s in h.seen if "Background from the orchestrator" in s["system"]}
    assert "bought last week" in by_skill["refund-policy"]["system"]
    assert "regional sales" in by_skill["sales-analysis"]["system"]
    assert "regional sales" not in by_skill["refund-policy"]["system"]
    assert "A100" not in by_skill["sales-analysis"]["system"]

    # --- isolation: the orchestrator got final answers only, never the raw tool output
    root_view = " ".join(contents_text(r) for r in h.root_llm.requests)
    assert "ELIGIBLE: delivered 5 days ago" in root_view
    assert "days_since_delivery" not in root_view  # raw lookup_order output stayed in the sub-agent
    assert "revenue" in root_view and '"rows"' not in root_view

    # --- tracing
    finished = spans.get_finished_spans()
    by_id = {s.context.span_id: s for s in finished}

    def ancestors(span):
        while span.parent is not None and span.parent.span_id in by_id:
            span = by_id[span.parent.span_id]
            yield span

    delegations = [s for s in finished if s.name == "harness.delegate"]
    assert len(delegations) == 2
    for d in delegations:
        a = d.attributes
        assert a["harness.correlation_id"] == h.session_id
        assert a["harness.status"] == "ok"
        assert a["harness.model"] in (config.DEFAULT_MODEL, "gemini-2.5-pro")
        assert a["harness.duration_ms"] > 0 and a["harness.setup_ms"] >= 0 and a["harness.run_ms"] > 0
        assert a["harness.duration_ms"] >= a["harness.run_ms"]
        # nests under the orchestrator's own tool-call span, in the same trace
        parents = [x.name for x in ancestors(d)]
        assert any(n.startswith("execute_tool") for n in parents), parents
        # every span the sub-agent produced carries the correlation id (and there are some)
        inside = [s for s in finished if d.context.span_id in {x.context.span_id for x in ancestors(s)}]
        assert inside, "sub-agent produced no child spans, so the stamping check would be vacuous"
        assert all(s.attributes.get("harness.correlation_id") == h.session_id for s in inside)
        assert {s.context.trace_id for s in inside} == {d.context.trace_id}
    skills = {d.attributes["harness.skill"]: d.attributes for d in delegations}
    assert list(skills["refund-policy"]["harness.tools_granted"]) == ["lookup_order", "check_refund_eligibility"]
    assert list(skills["sales-analysis"]["harness.tools_granted"]) == ["run_sales_query", "run_python"]
    a, b = delegations
    assert a.start_time < b.end_time and b.start_time < a.end_time  # intervals overlap


def test_unknown_skill_reports_failure_and_builds_nothing(monkeypatch, spans):
    root_steps = [calls(delegate_call("nope", "x", "y")), say("could not do that")]
    h = asyncio.run(Harness(monkeypatch, root_steps, {}).run())
    response = [r.response for e in h.events for r in (e.get_function_responses() or [])][0]
    assert response["status"] == "unknown_skill"
    assert h.seen == []  # no sub-agent was created
    span = [s for s in spans.get_finished_spans() if s.name == "harness.delegate"][0]
    assert span.attributes["harness.status"] == "unknown_skill"
    assert span.status.status_code.name == "ERROR"


def test_long_summary_is_truncated_and_braces_do_not_break_templating(monkeypatch, spans):
    long_summary = "{order_id} " + ("background " * 200)  # braces would crash ADK {var} templating
    sub = {"order-support": [say("shipped")]}
    root_steps = [calls(delegate_call("order-support", "status of A100", long_summary)), say("done")]
    h = asyncio.run(Harness(monkeypatch, root_steps, sub).run())
    response = [r.response for e in h.events for r in (e.get_function_responses() or [])][0]
    assert response["status"] == "ok"
    assert "truncated" in response["warnings"][0] or "cut" in response["warnings"][0]
    system = h.seen[0]["system"]
    background = system.split("Background from the orchestrator")[1]
    assert "{order_id}" in background  # passed through literally
    assert len(background) < config.MAX_CONTEXT_SUMMARY_CHARS + 300
    span = [s for s in spans.get_finished_spans() if s.name == "harness.delegate"][0]
    assert span.attributes["harness.summary_truncated"] is True


def test_sub_agent_failure_is_reported_not_raised(monkeypatch, spans):
    def boom(_request):
        raise RuntimeError("model exploded")

    sub = {"order-support": [boom]}
    root_steps = [calls(delegate_call("order-support", "status of A100", "ctx")), say("part failed")]
    h = asyncio.run(Harness(monkeypatch, root_steps, sub).run())
    response = [r.response for e in h.events for r in (e.get_function_responses() or [])][0]
    assert response["status"] == "error" and "model exploded" in response["error"]
    assert h.final_text == "part failed"  # the orchestrator turn survived


def test_timeout_is_reported(monkeypatch, spans):
    monkeypatch.setattr(config, "SUBTASK_TIMEOUT_S", 0.2)

    async def hang(_request):
        await asyncio.sleep(5)

    sub = {"order-support": [hang]}
    root_steps = [calls(delegate_call("order-support", "status", "ctx")), say("timed out part")]
    h = asyncio.run(Harness(monkeypatch, root_steps, sub).run())
    response = [r.response for e in h.events for r in (e.get_function_responses() or [])][0]
    assert response["status"] == "timeout"


def test_orchestrator_instruction_lists_skills_and_rules():
    text = " ".join(agent_module.build_instruction().split())  # ignore line wrapping
    for name in MARKERS:
        assert f"- {name}:" in text
    assert "WITHOUT delegating" in text  # trivial questions are answered directly
    assert "one delegate_task call per part" in text and "same turn" in text  # parallel fan-out
    assert "written fresh for THIS call" in text and "Never include secrets" in text  # tailored, minimal summary
    assert str(config.MAX_CONTEXT_SUMMARY_CHARS) in text


def test_summary_helper():
    text, cut = delegate._prepare_summary("  a \n b  ")
    assert (text, cut) == ("a b", False)
    assert delegate._prepare_summary("") == ("(none provided)", False)
    text, cut = delegate._prepare_summary("x" * (config.MAX_CONTEXT_SUMMARY_CHARS + 50))
    assert cut and len(text) == config.MAX_CONTEXT_SUMMARY_CHARS
    # exactly at the cap is NOT truncated
    exact = "y" * config.MAX_CONTEXT_SUMMARY_CHARS
    assert delegate._prepare_summary(exact) == (exact, False)
