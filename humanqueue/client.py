from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx

from .config import gateway_token, gateway_url


class HumanQueueError(RuntimeError):
    pass


class HumanQueueCreateError(HumanQueueError):
    """Create outcome is still ambiguous after retry + recovery lookup."""

    def __init__(
        self,
        message: str,
        *,
        source: str,
        idempotency_key: str,
        cause: Exception | None = None,
    ):
        super().__init__(message)
        self.source = source
        self.idempotency_key = idempotency_key
        self.cause = cause


class HumanQueue:
    """Tiny synchronous client for the human:// protocol.

    For agents that can block, ``wait=True`` turns an interrupt into a simple
    pause/resume call. Long-running systems should prefer ``resume`` webhooks.
    """

    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        self.base_url = (base_url or os.environ.get("HUMAN_QUEUE_URL") or gateway_url()).rstrip("/")
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

        # A transport failure after the Gateway commits the canonical request
        # leaves the caller in an ambiguous state: the human obligation may exist
        # even though its request_id never reached the client. Every SDK ask uses
        # one stable idempotency key for the whole create attempt so a retry can
        # recover the already-committed request instead of creating a duplicate.
        effective_idempotency_key = idempotency_key or f"humanq-client:{uuid.uuid4().hex}"
        payload["idempotency_key"] = effective_idempotency_key

        response = None
        last_transport_error: httpx.TransportError | None = None
        ambiguous_server_error: httpx.Response | None = None
        create_acknowledged = False
        for attempt in range(3):
            try:
                response = httpx.post(
                    f"{self.base_url}/v1/human",
                    json=payload,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
            except httpx.TransportError as exc:
                last_transport_error = exc
                response = None
                if attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                break

            # 5xx is also an ambiguous create outcome: the request may have been
            # committed before a later server-side failure. Retrying the same
            # idempotency key is safe; 4xx remains a caller/protocol error.
            if response.status_code >= 500:
                ambiguous_server_error = response
                if attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                break

            self._raise(response)
            create_acknowledged = True
            break

        if create_acknowledged:
            assert response is not None
            item = response.json()["request"]
        else:
            item = self._recover_ambiguous_create(
                source=source,
                idempotency_key=effective_idempotency_key,
            )
            if item is None:
                detail = (
                    f"last transport error: {type(last_transport_error).__name__}: "
                    f"{last_transport_error}"
                    if last_transport_error is not None
                    else (
                        f"last server response: HTTP {ambiguous_server_error.status_code}"
                        if ambiguous_server_error is not None
                        else "no create acknowledgement"
                    )
                )
                raise HumanQueueCreateError(
                    "human:// create acknowledgement remained ambiguous after retries "
                    f"and recovery lookup ({detail}); retry later with "
                    f"idempotency_key={effective_idempotency_key!r}",
                    source=source,
                    idempotency_key=effective_idempotency_key,
                    cause=last_transport_error,
                )
        if not wait:
            return item
        return self.wait(item["id"], timeout=wait_timeout, poll_interval=poll_interval)

    def wait(self, request_id: str, *, timeout: float | None = None, poll_interval: float = 1.0) -> dict[str, Any]:
        started = time.monotonic()
        last_transport_error: httpx.TransportError | None = None
        while True:
            if timeout is not None and time.monotonic() - started >= timeout:
                detail = (
                    f"; last transport error: {type(last_transport_error).__name__}: {last_transport_error}"
                    if last_transport_error is not None
                    else ""
                )
                raise TimeoutError(f"timed out waiting for {request_id}{detail}")

            try:
                response = httpx.get(
                    f"{self.base_url}/v1/requests/{request_id}",
                    headers=self._headers(),
                    timeout=self.timeout,
                )
            except httpx.TransportError as exc:
                # The canonical request already exists. A temporary Gateway
                # restart/network interruption must not turn an unresolved
                # human obligation into a failed caller. Retry only transport
                # failures; HTTP responses still go through _raise below.
                last_transport_error = exc
                if timeout is not None:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        continue
                    time.sleep(min(poll_interval, remaining))
                else:
                    time.sleep(poll_interval)
                continue

            last_transport_error = None
            self._raise(response)
            item = response.json()["request"]
            status = item["status"]
            if status == "resolved":
                return item["resolution"] or {}
            if status in {"cancelled", "expired", "superseded"}:
                raise HumanQueueError(f"human request ended with status={status}")
            if timeout is not None:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    continue
                time.sleep(min(poll_interval, remaining))
            else:
                time.sleep(poll_interval)

    def _recover_ambiguous_create(
        self,
        *,
        source: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        """Best-effort recovery after every create acknowledgement was ambiguous."""

        for attempt in range(3):
            try:
                response = httpx.get(
                    f"{self.base_url}/v1/idempotency/lookup",
                    params={
                        "source": source,
                        "idempotency_key": idempotency_key,
                    },
                    headers=self._headers(),
                    timeout=self.timeout,
                )
            except httpx.TransportError:
                if attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
                continue

            if response.status_code == 404:
                if attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                return None
            self._raise(response)
            return response.json()["request"]
        return None

    @staticmethod
    def _headers() -> dict[str, str]:
        token = gateway_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise HumanQueueError(f"human:// returned HTTP {response.status_code}: {detail}")
