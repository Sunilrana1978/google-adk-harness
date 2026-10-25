"""Shared helper: run one user turn through the REAL root agent with scripted models."""
from google.adk.runners import InMemoryRunner
from google.genai import types

from app import agent as agent_module
from app import delegate
from fakes import ScriptedLlm, SkillRouterLlm

MARKERS = {
    "refund-policy": "# Refund policy skill",
    "order-support": "# Order support skill",
    "sales-analysis": "# Sales analysis skill",
}


def delegate_call(skill, task, summary):
    return ("delegate_task", {"skill_name": skill, "task": task, "context_summary": summary})


class Harness:
    def __init__(self, monkeypatch, root_steps, sub_scripts):
        self.sub_scripts = sub_scripts
        self.seen: list[dict] = []
        self.root_llm = ScriptedLlm(root_steps, model="router-fake")
        monkeypatch.setattr(
            delegate,
            "_resolve_model",
            lambda name: SkillRouterLlm(name, self.sub_scripts, self.seen, MARKERS),
        )

    async def run(self, user_text="hello"):
        runner = InMemoryRunner(agent=agent_module.build_root_agent(model=self.root_llm), app_name="test")
        session = await runner.session_service.create_session(app_name="test", user_id="u1")
        message = types.Content(role="user", parts=[types.Part(text=user_text)])
        self.events = [
            e
            async for e in runner.run_async(user_id="u1", session_id=session.id, new_message=message)
        ]
        self.session_id = session.id
        return self

    @property
    def final_text(self):
        return "".join(p.text or "" for p in self.events[-1].content.parts)
