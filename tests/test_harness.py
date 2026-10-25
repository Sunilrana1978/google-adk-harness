"""The original offline tests (skills + sandbox), updated for the async sandbox."""
import asyncio

from app import sandbox, skill_loader


def run(coro):
    return asyncio.run(coro)


def test_index_is_short_and_lists_all_skills():
    index = skill_loader.skill_index()
    for name in ("refund-policy", "order-support", "sales-analysis"):
        assert name in index
    assert "Call `lookup_order`" not in index  # bodies are NOT in the index


def test_skill_body_loads_on_demand():
    skill = skill_loader.get_skill("refund-policy")
    assert skill.tools == ["lookup_order", "check_refund_eligibility"]
    assert "Never promise a refund" in skill.body


def test_sandbox_runs_simple_code():
    out = run(sandbox.run_python_sandboxed("print(sum([1, 2, 3]))"))
    assert out["stdout"].strip() == "6"


def test_sandbox_blocks_file_writes():
    out = run(sandbox.run_python_sandboxed("with open('x.txt','w') as f:\n    f.write('hi')"))
    assert out["returncode"] != 0


def test_sandbox_does_not_inherit_secrets(monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "super-secret")
    out = run(sandbox.run_python_sandboxed("import os; print(os.environ.get('FAKE_API_KEY'))"))
    assert out["stdout"].strip() == "None"


def test_sandbox_times_out():
    out = run(sandbox.run_python_sandboxed("while True: pass"))
    assert "error" in out or out["returncode"] != 0
