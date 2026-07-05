"""Job handlers: map a job's `handler` string to an actual async function.

Add your own real handlers here (e.g. send_email, generate_report,
call_external_api) and register them in HANDLERS below. Each handler
receives the job's `payload` dict and should return a JSON-serializable
result, or raise an exception to trigger a retry.
"""
import asyncio
import random

import httpx


async def noop(payload: dict) -> dict:
    """Does nothing, always succeeds. Useful for testing the pipeline."""
    return {"ok": True}


async def sleep_job(payload: dict) -> dict:
    seconds = float(payload.get("seconds", 1))
    await asyncio.sleep(seconds)
    return {"slept_seconds": seconds}


async def flaky_job(payload: dict) -> dict:
    """Fails randomly based on `failure_rate` (default 50%) — useful for
    exercising the retry / backoff / dead-letter pipeline end to end."""
    failure_rate = float(payload.get("failure_rate", 0.5))
    if random.random() < failure_rate:
        raise RuntimeError("Simulated transient failure")
    return {"ok": True}


async def http_request(payload: dict) -> dict:
    url = payload["url"]
    method = payload.get("method", "GET")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(method, url, json=payload.get("body"))
        response.raise_for_status()
        return {"status_code": response.status_code}


HANDLERS = {
    "noop": noop,
    "sleep": sleep_job,
    "flaky": flaky_job,
    "http_request": http_request,
}


class HandlerNotFoundError(Exception):
    pass


async def run_handler(handler_name: str, payload: dict) -> dict:
    handler = HANDLERS.get(handler_name)
    if handler is None:
        raise HandlerNotFoundError(f"No handler registered for '{handler_name}'")
    return await handler(payload)