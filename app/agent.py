"""The generic harness: written ONCE, never edited per workflow (4.3 point 1).

New business workflows = new skills/<name>/SKILL.md files, not new Python.
"""
from google.adk.agents import Agent
from google.adk.apps.app import App, EventsCompactionConfig

from . import config
from .delegate import delegate_task
from .guardrails import before_tool_guard
from .skill_loader import load_all, skill_index

# Fail fast: a skill with an unknown tool, a disallowed model or a typo'd key stops startup here,
# instead of surfacing as a confusing error in the middle of a conversation.
load_all()

_INSTRUCTION = """You are a fast orchestrator for an internal productivity assistant.

Available skills (name: description):
{index}

How to work:
1. Answer directly, WITHOUT delegating, when the message is trivial or conversational: greetings,
   thanks, "what can you do?", or questions about the skills above. Delegating costs extra model
   round trips, so only do it when a skill is actually needed.
2. Otherwise pick the skill(s) that fit. If none fits, say so plainly. Never invent a skill name
   and never use a skill the request does not need.
3. Delegate with delegate_task(skill_name, task, context_summary):
   - task: self-contained. Put every id or value the sub-agent needs in it; it cannot see this
     conversation.
   - context_summary: one to three sentences (at most {cap} characters), written fresh for THIS
     call and not reused between calls. Say what the user is ultimately trying to do, what is
     already established that matters for this sub-task, and why you are calling this skill. Include
     only what this skill needs. Never include secrets, personal data or anything unrelated to the
     sub-task.
4. If the request has independent parts, make one delegate_task call per part, all in the same
   turn, so they run in parallel. Chain calls (one per turn) only when a call needs an earlier
   call's result.
5. Merge the returned results into one answer. A result whose status is not "ok" means that part
   failed: say so and do not guess its answer.
"""


def build_instruction(_ctx=None) -> str:
    """Rebuilt on every turn so the index always matches the skill files on disk (cached parses)."""
    return _INSTRUCTION.format(index=skill_index(), cap=config.MAX_CONTEXT_SUMMARY_CHARS)


def build_root_agent(model=config.ROUTER_MODEL) -> Agent:
    return Agent(
        name="harness_root",
        model=model,
        # A callable, so ADK does not try to template the text and the index stays fresh.
        instruction=build_instruction,
        tools=[delegate_task],
        before_tool_callback=before_tool_guard,
    )


root_agent = build_root_agent()

# Context compaction (4.1): summarize older events in a sliding window.
app = App(
    name="app",
    root_agent=root_agent,
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=5,  # compact after every 5 completed events
        overlap_size=1,         # keep 1 event of overlap for continuity
    ),
)
