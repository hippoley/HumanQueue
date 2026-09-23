from __future__ import annotations

import os
import time
from typing import Any

import httpx


class HumanQueueError(RuntimeError):
    pass


class HumanQueue:
    """Tiny synchronous client for the human:// protocol.

    For agents that can block, ``wait=True`` turns an interrupt into a simple
    pause/resume call. Long-running systems should prefer ``resume`` webhooks.
    """

    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        self.base_url = (base_url or os.environ.get("HUMAN_QUEUE_URL") or "http://127.0.0.1:7482").rstrip("/")
        self.timeout = timeout

    def ask(
        self,
        uri: str,
        *,
        source: str,
        ref: str,
        title: str,
        summary: str = "",
        why_now: str | None = None,
        context: dict[str, Any] | None = None,
        options: list[dict[str, Any]] | None = None,
        fields_schema: dict[str, Any] | None = None,
        urgency: float = 0.5,
        unblock: float = 0.7,
        risk: float = 0.5,
        risk_if_delayed: float = 0.2,
        blast_radius: float = 0.2,
        seconds: int = 20,
        downstream: int = 1,
        idempotency_key: str | None = None,
        batch_key: str | None = None,
        supersession_key: str | None = None,
        policy_key: str | None = None,
        attention_group: str = "default",
        resume_url: str | None = None,
        resume_secret: str | None = None,
        wait: bool = False,
        wait_timeout: float | None = None,
        poll_interval: float = 1.0,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "uri": uri,
            "source": source,
            "ref": ref,
            "title": title,
            "summary": summary,
            "context": context or {},
            "options": options or [],
            "urgency": urgency,
            "unblock": unblock,
            "risk": risk,
            "risk_if_delayed": risk_if_delayed,
            "blast_radius": blast_radius,
            "seconds": seconds,
            "downstream": downstream,
            "attention_group": attention_group,
        }
        optional = {
            "why_now": why_now,
            "fields_schema": fields_schema,
            "idempotency_key": idempotency_key,
            "batch_key": batch_key,
            "supersession_key": supersession_key,
            "policy_key": policy_key,
        }
        payload.update({k: v for k, v in optional.items() if v is not None})
        if resume_url:
            payload["resume"] = {"mode": "webhook", "url": resume_url, "secret": resume_secret}

        response = httpx.post(f"{self.base_url}/v1/human", json=payload, timeout=self.timeout)
        self._raise(response)
        item = response.json()["request"]
        if not wait:
            return item
        return self.wait(item["id"], timeout=wait_timeout, poll_interval=poll_interval)

    def wait(self, request_id: str, *, timeout: float | None = None, poll_interval: float = 1.0) -> dict[str, Any]:
        started = time.monotonic()
        while True:
            response = httpx.get(f"{self.base_url}/v1/requests/{request_id}", timeout=self.timeout)
            self._raise(response)
            item = response.json()["request"]
            status = item["status"]
            if status == "resolved":
                return item["resolution"] or {}
            if status in {"cancelled", "expired", "superseded"}:
                raise HumanQueueError(f"human request ended with status={status}")
            if timeout is not None and time.monotonic() - started >= timeout:
                raise TimeoutError(f"timed out waiting for {request_id}")
            time.sleep(poll_interval)

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise HumanQueueError(f"Human Queue returned HTTP {response.status_code}: {detail}")
