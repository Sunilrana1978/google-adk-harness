"""Extra sandbox tests: the properties that matter once delegations run in parallel."""
import asyncio
import time

from app import sandbox, tools


def run(coro):
    return asyncio.run(coro)


def test_parallel_sandbox_runs_do_not_block_each_other():
    async def three_sleeps():
        started = time.perf_counter()
        await asyncio.gather(
            *[sandbox.run_python_sandboxed("import time; time.sleep(1)") for _ in range(3)]
        )
        return time.perf_counter() - started

    elapsed = run(three_sleeps())
    assert elapsed < 2.0, f"3 x 1s sleeps took {elapsed:.1f}s: the sandbox is blocking the event loop"


def test_event_loop_stays_responsive_while_sandbox_runs():
    async def scenario():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        await sandbox.run_python_sandboxed("import time; time.sleep(1)")
        beat.cancel()
        return ticks

    assert run(scenario()) >= 10  # a blocked loop would have ticked ~0 times


def test_output_is_capped():
    out = run(sandbox.run_python_sandboxed("print('x' * 100000)"))
    assert len(out["stdout"]) <= sandbox.MAX_OUTPUT_BYTES


def test_runaway_cpu_is_killed_with_an_explanation():
    out = run(sandbox.run_python_sandboxed("while True: pass"))
    assert out["returncode"] < 0 and "limit" in out["error"]


def test_run_python_tool_is_async():
    import inspect

    assert inspect.iscoroutinefunction(tools.run_python)
    assert run(tools.run_python("print(1 + 1)"))["stdout"].strip() == "2"
