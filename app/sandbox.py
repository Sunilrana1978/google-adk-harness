"""Sandboxing demo (4.2) - local, educational version.

Boundaries from the page:
  * Filesystem: empty temp dir as cwd, isolated interpreter mode (-I), no file writes
  * Network:    NOT enforceable here. In production use Cloud Run/GKE with VPC egress
                allowlists / VPC Service Controls, or a managed sandbox executor.
  * Compute:    wall-clock timeout, CPU-seconds and memory caps, no new processes

This runs as an ASYNC subprocess on purpose. ADK runs sync tools directly on the event loop, so
a blocking subprocess.run() here would freeze every other delegation running in parallel.

The limits are set by a tiny launcher inside the child interpreter (not preexec_fn, which is
unsafe when other threads exist). The model's code arrives on stdin.

For production prefer ADK's container / GKE / managed code executors instead of this.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile

TIMEOUT_S = 5
CPU_S = 3
MEM_BYTES = 256 * 1024 * 1024
MAX_OUTPUT_BYTES = 2000

_LAUNCHER = f"""
import resource, sys
for _res, _lim in (
    (resource.RLIMIT_CPU, {CPU_S}),
    (resource.RLIMIT_AS, {MEM_BYTES}),
    (resource.RLIMIT_FSIZE, 0),   # cannot write files
    (resource.RLIMIT_NPROC, 0),   # cannot start processes or threads
):
    resource.setrlimit(_res, (_lim, _lim))
exec(compile(sys.stdin.read(), "<sandbox>", "exec"), {{"__name__": "__main__"}})
"""


async def _read_capped(stream: asyncio.StreamReader, cap: int) -> bytes:
    """Keep the first `cap` bytes, discard the rest without buffering it."""
    kept = await stream.read(cap)
    while await stream.read(65536):
        pass
    return kept


async def run_python_sandboxed(code: str) -> dict:
    with tempfile.TemporaryDirectory() as workdir:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", _LAUNCHER,
            cwd=workdir,
            env={},  # no inherited secrets
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def _run() -> tuple[bytes, bytes]:
            proc.stdin.write(code.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            out, err, _ = await asyncio.gather(
                _read_capped(proc.stdout, MAX_OUTPUT_BYTES),
                _read_capped(proc.stderr, MAX_OUTPUT_BYTES // 4),
                proc.wait(),
            )
            return out, err

        try:
            out, err = await asyncio.wait_for(_run(), TIMEOUT_S)
        except asyncio.TimeoutError:
            return {"error": f"timed out after {TIMEOUT_S}s"}
        except (BrokenPipeError, ConnectionResetError):
            out, err = b"", b"sandbox process exited before reading the code"
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    result = {
        "stdout": out.decode(errors="replace"),
        "stderr": err.decode(errors="replace"),
        "returncode": proc.returncode,
    }
    if proc.returncode < 0:  # killed by a signal: almost always the CPU or memory cap
        result["error"] = f"terminated by signal {-proc.returncode} (likely the CPU or memory limit)"
    return result
