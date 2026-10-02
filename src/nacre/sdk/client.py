"""
Functionality: The Nacre Python SDK: a thin, typed, synchronous client with the same operations as the MCP tools,
  over Streamable HTTP or a stdio server command, plus an in-process mode for tests and the eval harness.
Owns: the transport choice, sending the token (an HTTP bearer header, or NACRE_TOKEN in the stdio child's env),
  refusing to send a token over plain HTTP to a non-loopback host, parsing the server's typed error into NacreError,
  and the typed operation methods. No recall logic lives here.
Public entry: Client, NacreError
Decisions: D-0026, D-0025, D-0018
Assumptions: A-0039
Notes: D-0026 §5.
  - Client(url, token) for HTTP; Client([command, *args], token) for stdio; Client.local(services, principal_id)
    calls interface/run_operation.py in-process and writes `asserted` (§3).
  - D1 (2026-10-02): each remote call opens its own MCP connection (simple and stateless; the server re-authenticates
    every call anyway). A pooled long-lived connection is a later optimisation if latency needs it.
  - Errors: the server's "Error executing tool <name>: <code>: <detail>" becomes NacreError(code, detail). The token
    is never part of an error or a repr.
"""
import ipaddress
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import anyio
import httpx2
from mcp import Client as McpClient
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from nacre.interface.run_operation import Caller, OperationError, Services, run_operation


class NacreError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _loopback(host: str) -> bool:
    try:
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _parse_error(text: str, tool: str) -> NacreError:
    prefix = f"Error executing tool {tool}: "
    body = text[len(prefix):] if text.startswith(prefix) else text
    code, _, detail = body.partition(": ")
    return NacreError(code if code.isidentifier() else "internal", detail)


class Client:
    def __init__(self, target: str | Sequence[str], token: str):
        if isinstance(target, str):
            u = urlsplit(target)
            if u.scheme not in ("http", "https") or (u.scheme == "http" and not _loopback(u.hostname or "")):
                raise NacreError("invalid_request", "a token is sent only over https, or http to a loopback host")
        elif not target:
            raise NacreError("invalid_request", "an empty stdio command")
        self._target, self._token, self._local = target, token, None

    @classmethod
    def local(cls, services: Services, principal_id: UUID) -> "Client":
        c = cls.__new__(cls)
        c._target, c._token, c._local = None, None, (services, Caller(principal_id))
        return c

    def __repr__(self) -> str:
        return f"Client({'local' if self._local else self._target!r})"

    def _server(self):
        if isinstance(self._target, str):
            http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {self._token}"})
            return streamable_http_client(self._target, http_client=http)
        return StdioServerParameters(command=self._target[0], args=list(self._target[1:]),
                                     env={"NACRE_TOKEN": self._token})

    def _call(self, op: str, args: dict[str, Any], keep_none: tuple[str, ...] = ()) -> dict[str, Any]:
        args = {k: (str(v) if isinstance(v, UUID) else list(v) if isinstance(v, tuple) else v)
                for k, v in args.items() if k != "self" and (v is not None or k in keep_none)}
        if self._local is not None:
            try:
                return run_operation(self._local[0], self._local[1], op, args)
            except OperationError as e:
                raise NacreError(e.code, e.detail) from None

        async def go() -> Any:
            async with McpClient(self._server()) as c:
                return await c.call_tool(op, args)
        result = anyio.run(go)
        if result.is_error:
            raise _parse_error(" ".join(getattr(p, "text", "") for p in result.content), op)
        return result.structured_content

    def recall_context(self, issuing_stream: UUID, scopes: Sequence[tuple[str, UUID]], query: str,
                       addresses: Sequence[str] = ()) -> dict[str, Any]:
        return self._call("recall_context", {"issuing_stream": issuing_stream,
                                             "scopes": [[lvl, str(s)] for lvl, s in scopes], "query": query,
                                             "addresses": list(addresses) or None})

    def record_decision(self, stream_id: UUID, idempotency_key: str, decision_text: str, *,
                        decision_kind: str = "task_response", decided_from: UUID | None = None,
                        stakes: Sequence[str] | None = None, cycle_id: UUID | None = None,
                        task_id: UUID | None = None, mode: str | None = None, actor_model: str | None = None,
                        actor_model_version: str | None = None, source: str = "chat", authorship: str = "external",
                        actor_kind: str = "agent") -> dict[str, Any]:
        return self._call("record_decision", dict(locals()))

    def record_prediction(self, stream_id: UUID, idempotency_key: str, decision_id: UUID, expected_outcome: str, *,
                          expected_success: bool | None = None, predictor: str = "agent",
                          confidence_pct: int | None = None, expected_failing_check: str | None = None,
                          cycle_id: UUID | None = None, task_id: UUID | None = None, mode: str | None = None,
                          source: str = "chat", authorship: str = "external", actor_kind: str = "agent"
                          ) -> dict[str, Any]:
        return self._call("record_prediction", dict(locals()))

    def record_action(self, stream_id: UUID, idempotency_key: str, decision_id: UUID, action_kind: str,
                      description: str, *, stakes: Sequence[str] | None = None, cycle_id: UUID | None = None,
                      task_id: UUID | None = None, mode: str | None = None, actor_tool: str | None = None,
                      source: str = "chat", authorship: str = "external", actor_kind: str = "agent"
                      ) -> dict[str, Any]:
        return self._call("record_action", dict(locals()))

    def record_outcome(self, stream_id: UUID, idempotency_key: str, outcome_for: UUID, success: bool | None,
                       sections: Sequence[tuple[str, str]], *, evaluates_prediction: UUID | None = None,
                       stakes: Sequence[str] | None = None, failing_checks: Sequence[str] | None = None,
                       cycle_id: UUID | None = None, task_id: UUID | None = None, mode: str | None = None,
                       actor_tool: str | None = None, source: str = "chat", authorship: str = "external",
                       actor_kind: str = "agent") -> dict[str, Any]:
        a = dict(locals())
        a["sections"] = [{"role": r, "text": t} for r, t in sections]
        return self._call("record_outcome", a, keep_none=("success",))     # None = outcome unknown, still sent

    def record_correction(self, stream_id: UUID, idempotency_key: str, correction_of: UUID, text: str, *,
                          scope_of_correction: str | None = None, cycle_id: UUID | None = None,
                          task_id: UUID | None = None, mode: str | None = None, source: str = "review",
                          authorship: str = "scope_principal", actor_kind: str = "person") -> dict[str, Any]:
        return self._call("record_correction", dict(locals()))
