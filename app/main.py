"""FastAPI application exposing the register linearizability audit service."""

from __future__ import annotations

import os

from fastapi import FastAPI

from .schemas import (
    AuditRequest,
    AuditResponse,
    CasOperation,
    HealthResponse,
    ReadOperation,
    StepOut,
    WriteOperation,
)
from .solver import Operation, OpKind, solve

app = FastAPI(
    title="Register Linearizability Audit Service",
    version="1.0.0",
    description=(
        "Audits a history of completed operations on a single "
        "read/write/compare-and-swap register for linearizability. "
        "Submit the initial register value together with 1 to 24 completed "
        "operations; the service returns the minimal legal total order "
        "(lexicographic by operation identifier) with the register value "
        "before and after every step, or reports that no legal total order "
        "exists."
    ),
)


def _to_domain(op: WriteOperation | ReadOperation | CasOperation) -> Operation:
    """Translate a validated API operation into the solver's domain type."""
    if isinstance(op, WriteOperation):
        return Operation(
            identifier=op.id,
            invoke=op.invoke,
            respond=op.respond,
            kind=OpKind.WRITE,
            value=op.value,
        )
    if isinstance(op, ReadOperation):
        return Operation(
            identifier=op.id,
            invoke=op.invoke,
            respond=op.respond,
            kind=OpKind.READ,
            value=op.value,
        )
    return Operation(
        identifier=op.id,
        invoke=op.invoke,
        respond=op.respond,
        kind=OpKind.CAS,
        expected=op.expected,
        update=op.update,
        success=op.success,
    )


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    """Liveness probe used by Docker Compose's healthcheck."""
    return HealthResponse()


@app.post("/linearize", response_model=AuditResponse, tags=["audit"])
def linearize(request: AuditRequest) -> AuditResponse:
    """Audit one submitted history for linearizability."""
    operations = [_to_domain(op) for op in request.operations]
    result = solve(request.initial_value, operations)

    if not result.linearizable:
        return AuditResponse(
            linearizable=False,
            unique=None,
            order=None,
            steps=None,
            detail=(
                "not linearizable: no total order respects the real-time "
                "precedence constraints and the register semantics"
            ),
        )

    assert result.order is not None and result.steps is not None
    detail = (
        "linearizable: the legal total order is unique"
        if result.unique
        else "linearizable: multiple legal total orders exist; "
        "returning the minimal one (lexicographic by operation identifier)"
    )
    return AuditResponse(
        linearizable=True,
        unique=result.unique,
        order=list(result.order),
        steps=[StepOut(id=s.identifier, before=s.before, after=s.after) for s in result.steps],
        detail=detail,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )
