"""Scripted stand-ins for the model, so the real harness runs end to end with no network."""
import inspect

from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr


def say(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def calls(*function_calls) -> LlmResponse:
    """One model turn that makes several tool calls at once: calls(("tool", {"arg": 1}), ...)."""
    parts = [types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in function_calls]
    return LlmResponse(content=types.Content(role="model", parts=parts))


def system_text(llm_request) -> str:
    return str(llm_request.config.system_instruction)


def contents_text(llm_request) -> str:
    return str(llm_request.contents)


async def _resolve(step, request):
    out = step(request) if callable(step) else step
    return await out if inspect.isawaitable(out) else out


class ScriptedLlm(BaseLlm):
    """Plays back a fixed list of steps (a response, or a function of the request)."""

    model: str = "scripted"
    _steps: list = PrivateAttr(default_factory=list)
    _requests: list = PrivateAttr(default_factory=list)

    def __init__(self, steps, model: str = "scripted"):
        super().__init__(model=model)
        self._steps = list(steps)

    @property
    def requests(self):
        return self._requests

    async def generate_content_async(self, llm_request, stream=False):
        self._requests.append(llm_request)
        yield await _resolve(self._steps.pop(0), llm_request)


class SkillRouterLlm(BaseLlm):
    """One fake for ALL sub-agents: picks the script by which skill's body is in the instruction.

    `model` holds the model name the harness asked for, so tests can check per-skill models.
    """

    model: str = "scripted"
    _scripts: dict = PrivateAttr(default_factory=dict)
    _seen: list = PrivateAttr(default_factory=list)
    _markers: dict = PrivateAttr(default_factory=dict)

    def __init__(self, model: str, scripts: dict, seen: list, markers: dict):
        super().__init__(model=model)
        self._scripts, self._seen, self._markers = scripts, seen, markers

    async def generate_content_async(self, llm_request, stream=False):
        text = system_text(llm_request)
        skill = next(name for name, marker in self._markers.items() if marker in text)
        self._seen.append({"skill": skill, "model": self.model, "system": text, "contents": contents_text(llm_request)})
        yield await _resolve(self._scripts[skill].pop(0), llm_request)
