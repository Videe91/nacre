"""
Functionality: The Nacre MCP server: transport and dispatch only. It authenticates every call, applies the body and
  rate limits, and hands the call to interface/run_operation.py.
Owns: the tool surface (names and parameters), the stdio and loopback-only HTTP transports and the refusal to bind
  elsewhere, per-call authentication (stdio: NACRE_TOKEN; HTTP: the Authorization bearer header), the 1 MiB argument
  limit, and the typed error text (`<code>: <detail>`).
Public entry: build_server(), check_bind(), serve_stdio(), serve_http(), main(), TOOLS, MAX_ARGUMENT_BYTES, BindRefused
Decisions: D-0026, D-0025, D-0018
Assumptions: A-0039, A-0040
Notes: D-0026 §4 (owner decisions 3 and 5).
  - Tools: recall_context and the five record_* captures. NOT exposed: erasure, keys, principals, tokens, scopes (they
    stay in the admin CLI). get_frame and record_statement wait for amendment 3 (PROPOSED).
  - Authentication runs on EVERY call, so revocation and expiry take effect immediately; the token is never logged
    or returned. stdio reads NACRE_TOKEN once at start; HTTP reads `Authorization: Bearer` on each request.
  - HTTP binds only to a loopback address in Phase 3: a non-loopback host refuses to start (there is no TLS path
    yet). The MCP SDK's DNS-rebinding protection is on for loopback hosts.
  - Results are structured (JSON objects). Error text is the MCP SDK's "Error executing tool <name>: " followed by
    `<code>: <detail>`; the code is stable.
  - The 1 MiB limit applies to the JSON arguments of every call on both transports (and to HTTP bodies).
  - `mcp` is imported only here and in sdk/client.py.
"""
import ipaddress
import json
import os
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from nacre.interface.authenticate_principal import AuthError, Principal
from nacre.interface.limit_rate import RateLimiter
from nacre.interface.run_operation import Caller, OperationError, Services, run_operation

MAX_ARGUMENT_BYTES = 1024 * 1024
TOOLS = ("recall_context", "record_decision", "record_prediction", "record_action", "record_outcome",
         "record_correction")


class BindRefused(RuntimeError):
    """HTTP may bind only to loopback in Phase 3 (D-0026 §4)."""


def check_bind(host: str) -> None:
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise BindRefused(f"refusing to serve HTTP on {host!r}: Phase 3 serves loopback only, with no TLS (D-0026 §4)")


def build_server(services: Services, authenticate: Callable[[str], Principal],
                 token_of: Callable[[Context], str | None], limiter: RateLimiter | None = None) -> MCPServer:
    """An MCPServer whose tools authenticate with `token_of(ctx)` via `authenticate(token)` on every call."""
    limiter = limiter or RateLimiter()
    server = MCPServer(name="nacre", instructions="Nacre memory: recall context and record decisions and outcomes.")

    def call(ctx: Context, op: str, args: dict) -> dict[str, Any]:
        if len(json.dumps(args, default=str).encode()) > MAX_ARGUMENT_BYTES:
            raise ToolError("invalid_request: arguments exceed 1 MiB (D-0026 §4)")
        token = token_of(ctx)
        if not token:
            raise ToolError("unauthenticated: no token")
        try:
            principal = authenticate(token)
        except AuthError as e:
            raise ToolError(f"unauthenticated: {e.code}") from None
        wait = limiter.admit(principal.principal_id)
        if wait:
            raise ToolError(f"rate_limited: retry after {wait:.1f} s")
        try:
            return run_operation(services, Caller(principal.principal_id, principal), op, args)
        except OperationError as e:
            raise ToolError(str(e)) from None

    def args_of(local: dict) -> dict[str, Any]:
        # locals() of a tool also holds this closure's free variables (call, args_of): keep only the parameters
        return {k: v for k, v in local.items() if k != "ctx" and not callable(v) and v is not None}

    @server.tool(structured_output=True, description="Recall a memory frame for a task. Returns frame_id, coverage and the memory section.")
    def recall_context(ctx: Context, issuing_stream: str, scopes: list[tuple[str, str]], query: str,
                       addresses: list[str] | None = None) -> dict[str, Any]:
        return call(ctx, "recall_context", args_of(locals()))

    @server.tool(structured_output=True, description="Record a decision (D-0018).")
    def record_decision(ctx: Context, stream_id: str, idempotency_key: str, decision_text: str,
                        decision_kind: str = "task_response", decided_from: str | None = None,
                        stakes: list[str] | None = None, cycle_id: str | None = None, task_id: str | None = None,
                        mode: str | None = None, actor_model: str | None = None,
                        actor_model_version: str | None = None, source: str = "chat", authorship: str = "external",
                        actor_kind: str = "agent") -> dict[str, Any]:
        return call(ctx, "record_decision", args_of(locals()))

    @server.tool(structured_output=True, description="Record a prediction about a decision's outcome (D-0018).")
    def record_prediction(ctx: Context, stream_id: str, idempotency_key: str, decision_id: str, expected_outcome: str,
                          expected_success: bool | None = None, predictor: str = "agent",
                          confidence_pct: int | None = None, expected_failing_check: str | None = None,
                          cycle_id: str | None = None, task_id: str | None = None, mode: str | None = None,
                          source: str = "chat", authorship: str = "external", actor_kind: str = "agent") -> dict[str, Any]:
        return call(ctx, "record_prediction", args_of(locals()))

    @server.tool(structured_output=True, description="Record an action taken for a decision (D-0018).")
    def record_action(ctx: Context, stream_id: str, idempotency_key: str, decision_id: str, action_kind: str,
                      description: str, stakes: list[str] | None = None, cycle_id: str | None = None,
                      task_id: str | None = None, mode: str | None = None, actor_tool: str | None = None,
                      source: str = "chat", authorship: str = "external", actor_kind: str = "agent") -> dict[str, Any]:
        return call(ctx, "record_action", args_of(locals()))

    @server.tool(structured_output=True, description="Record an outcome with role-tagged sections (D-0018).")
    def record_outcome(ctx: Context, stream_id: str, idempotency_key: str, outcome_for: str, success: bool | None,
                       sections: list[dict[str, str]], evaluates_prediction: str | None = None,
                       stakes: list[str] | None = None, failing_checks: list[str] | None = None,
                       cycle_id: str | None = None, task_id: str | None = None, mode: str | None = None,
                       actor_tool: str | None = None, source: str = "chat", authorship: str = "external",
                       actor_kind: str = "agent") -> dict[str, Any]:
        a = args_of(locals())
        a["success"] = success                    # None is a meaningful value here (unknown), keep it
        return call(ctx, "record_outcome", a)

    @server.tool(structured_output=True, description="Record a correction of an earlier event (only reviewer-granted persons and structured "
                             "CI/integration results may; D-0026).")
    def record_correction(ctx: Context, stream_id: str, idempotency_key: str, correction_of: str, text: str,
                          scope_of_correction: str | None = None, cycle_id: str | None = None,
                          task_id: str | None = None, mode: str | None = None, source: str = "review",
                          authorship: str = "scope_principal", actor_kind: str = "person") -> dict[str, Any]:
        return call(ctx, "record_correction", args_of(locals()))

    return server


def _bearer(ctx: Context) -> str | None:
    value = (ctx.headers or {}).get("authorization", "")
    return value[7:].strip() if value.lower().startswith("bearer ") else None


def serve_stdio(services: Services, authenticate: Callable[[str], Principal]) -> None:
    token = os.environ.get("NACRE_TOKEN", "")
    build_server(services, authenticate, lambda ctx: token).run("stdio")


def serve_http(services: Services, authenticate: Callable[[str], Principal], *, host: str = "127.0.0.1",
               port: int = 8765) -> None:
    check_bind(host)
    build_server(services, authenticate, _bearer).run("streamable-http", host=host, port=port,
                                                      max_request_body_size=MAX_ARGUMENT_BYTES)


def main(argv: list[str] | None = None) -> int:
    """`python -m nacre.interface.mcp_server --tau-strong-q N --config-version V [--http --host H --port P]`.
    Connections and keys come from NACRE_DSN_APP, NACRE_DSN_AUTH, NACRE_ROOT_KEY_DIR and NACRE_TOKEN_KEY_FILE.
    tau is passed explicitly until it is fixed on the EXP-0004 dev split as a config_event."""
    import argparse
    from pathlib import Path

    from nacre.core.db import DbRole, open_pool
    from nacre.interface.authenticate_principal import authenticate_principal
    from nacre.interface.token_format import load_token_key
    from nacre.keys.local_file_root_key import LocalFileRootKeyProvider
    from nacre.recall.embed_local import DIM
    from nacre.recall.index_version import default_embedder
    from nacre.recall.load_index_cache import IndexCache

    ap = argparse.ArgumentParser(prog="nacre-mcp")
    ap.add_argument("--tau-strong-q", type=int, required=True)
    ap.add_argument("--config-version", required=True)
    ap.add_argument("--http", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    if a.http:
        check_bind(a.host)                                   # before opening anything
    kp = LocalFileRootKeyProvider(Path(os.environ["NACRE_ROOT_KEY_DIR"]))
    key = load_token_key()
    with open_pool(DbRole.APP) as pool, open_pool(DbRole.AUTH, max_size=4) as auth_pool:
        services = Services(pool, kp, IndexCache(kp, dim=DIM), default_embedder(), a.tau_strong_q, a.config_version)

        def authenticate(token: str) -> Principal:          # one pooled connection per call: tools run in threads
            with auth_pool.connection() as c:
                return authenticate_principal(c, key, token)
        if a.http:
            serve_http(services, authenticate, host=a.host, port=a.port)
        else:
            serve_stdio(services, authenticate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
