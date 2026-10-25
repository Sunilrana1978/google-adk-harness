"""agents-cli custom metric `skill_selection`: did the orchestrator delegate to the right skills?

Score 1 only if the set of skills passed to delegate_task equals the expected set. That one rule
covers all three things we care about:
  * single-skill questions pick that skill
  * multi-intent questions pick every needed skill
  * nothing over-triggers: an extra skill, or any delegation for a trivial question, scores 0

agents-cli inlines this file into the config as source text, so it must stay self-contained:
standard-library imports only, no project imports and no __file__.

It fails closed: an unknown prompt or a trace it cannot read scores 0, never a silent pass.
Keep EXPECTED in step with datasets/skill-selection-dataset.json (tests/test_evals.py enforces it).
"""
import json
import re

KNOWN_SKILLS = {"refund-policy", "order-support", "sales-analysis"}

# prompt text -> skills that must be delegated to, and no others. [] means: answer directly.
EXPECTED = {
    "I received order A100 last week but it is the wrong size. Am I eligible for a refund?": ["refund-policy"],
    "Where is my order B200 right now?": ["order-support"],
    "How did revenue in the west region trend from August to October?": ["sales-analysis"],
    "Can order C300 still be refunded? Separately, which region grew faster from August to October, west or east?": [
        "refund-policy",
        "sales-analysis",
    ],
    "Order B200 is delayed. Tell me its shipping status and whether it qualifies for a refund.": [
        "order-support",
        "refund-policy",
    ],
    "What is the delivery status of order A100? I only need the status, nothing else.": ["order-support"],
    "Hi! What can you help me with?": [],
    "What is the capital of France?": [],
}

# Matches the skill_name argument of a delegate_task call whether the trace is JSON text, a Python
# repr, or JSON nested inside a string. Only names in KNOWN_SKILLS count, so the words that follow
# "skill_name" in the orchestrator's own instructions ("skill_name, task, ...") are ignored.
_SKILL_ARG = re.compile(r"skill_name\W{1,8}([a-z0-9][a-z0-9-]*)")


def _text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def delegated_skills(agent_data):
    return {name for name in _SKILL_ARG.findall(_text(agent_data)) if name in KNOWN_SKILLS}


def evaluate(instance):
    prompt = _text(instance.get("prompt", ""))
    expected = next((skills for text, skills in EXPECTED.items() if text in prompt), None)
    if expected is None:
        return {"score": 0, "explanation": "no expectation registered for this prompt; add it to EXPECTED"}
    agent_data = instance.get("agent_data")
    if not agent_data:
        return {"score": 0, "explanation": "the trace has no agent_data, so delegation cannot be checked"}

    got = delegated_skills(agent_data)
    missing, extra = set(expected) - got, got - set(expected)
    if not missing and not extra:
        return {"score": 1, "explanation": f"delegated to {sorted(got) or 'no skill'} as expected"}
    return {
        "score": 0,
        "explanation": (
            f"expected {sorted(expected) or 'no delegation'}, got {sorted(got) or 'no delegation'} "
            f"(missing {sorted(missing)}, over-triggered {sorted(extra)})"
        ),
    }
