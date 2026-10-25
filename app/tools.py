"""Mock business tools. Replace bodies with real API/DB calls.

Skills refer to these by name in their `tools:` frontmatter line.

Each tool is a standalone function with no dependence on agent code, so it can later be wrapped
in an MCP server without changing any skill (skills only reference tool names).

Blocking I/O: ADK runs SYNC tools directly on the event loop by default, so a slow sync tool
freezes every parallel delegation. The mocks below are instant; real tools that call databases
or APIs should be `async def` (or set RunConfig.tool_thread_pool_config), like run_python.

Credentials: when you replace these mocks with real Google Cloud calls, use Application
Default Credentials (e.g. `bigquery.Client()` with no key arguments). On Agent Runtime /
Cloud Run the agent's Agent Identity is picked up automatically, so there are no keys in code
or prompts. Grant its principal only the roles it needs (see deploy/iam.sh).
"""
from __future__ import annotations

from .sandbox import run_python_sandboxed

_ORDERS = {
    "A100": {"status": "delivered", "carrier": "UPS", "days_since_delivery": 5, "total": 89.0},
    "B200": {"status": "delayed", "carrier": "FedEx", "days_since_delivery": None, "total": 42.5},
    "C300": {"status": "delivered", "carrier": "USPS", "days_since_delivery": 45, "total": 120.0},
}

_SALES = {
    "west": [{"month": "Aug", "revenue": 100}, {"month": "Sep", "revenue": 120}, {"month": "Oct", "revenue": 150}],
    "east": [{"month": "Aug", "revenue": 90}, {"month": "Sep", "revenue": 85}, {"month": "Oct", "revenue": 95}],
}


def lookup_order(order_id: str) -> dict:
    """Return status, carrier and delivery info for an order id."""
    return _ORDERS.get(order_id.upper(), {"error": f"order {order_id} not found"})


def check_refund_eligibility(order_id: str) -> dict:
    """Refunds are allowed within 30 days of delivery."""
    order = _ORDERS.get(order_id.upper())
    if not order:
        return {"eligible": False, "reason": "order not found"}
    days = order["days_since_delivery"]
    if days is None:
        return {"eligible": False, "reason": "order not delivered yet"}
    if days <= 30:
        return {"eligible": True, "reason": f"delivered {days} days ago (limit 30)"}
    return {"eligible": False, "reason": f"delivered {days} days ago (limit 30)"}


def run_sales_query(region: str) -> dict:
    """Return monthly revenue rows for a region (west or east)."""
    return {"rows": _SALES.get(region.lower(), [])}


async def run_python(code: str) -> dict:
    """Run a short Python snippet in a restricted subprocess and return stdout."""
    return await run_python_sandboxed(code)


# Name -> callable. The delegator uses this to give a sub-agent ONLY the tools
# its skill declares (least privilege).
TOOL_REGISTRY = {
    f.__name__: f
    for f in (lookup_order, check_refund_eligibility, run_sales_query, run_python)
}
