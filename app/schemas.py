"""Request and response schemas for the linearizability audit API.

All validation is performed by Pydantic, so any malformed submission --
invalid time interval, duplicated identifier, fields inconsistent with the
declared operation type, too few/many operations, non-integer timestamps or
values -- is rejected as a whole with HTTP 422 before any solving happens.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

#: An audit submission must contain between 1 and 24 completed operations.
MIN_OPERATIONS = 1
MAX_OPERATIONS = 24


class _BaseOperation(BaseModel):
    """Fields shared by every operation kind.

    ``extra="forbid"`` makes a submission inconsistent with its declared
    ``type`` (e.g. a ``write`` carrying a ``expected`` field) a validation
    error, so the whole request is rejected.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, description="Unique operation identifier.")
    invoke: StrictInt = Field(description="Invocation time (integer).")
    respond: StrictInt = Field(description="Response time (integer).")

    @model_validator(mode="after")
    def _check_interval(self) -> "_BaseOperation":
        if self.respond < self.invoke:
            raise ValueError(
                f"operation {self.id!r}: respond ({self.respond}) is earlier "
                f"than invoke ({self.invoke})"
            )
        return self


class WriteOperation(_BaseOperation):
    """A write carrying the value it writes."""

    type: Literal["write"]
    value: StrictInt = Field(description="Written value.")


class ReadOperation(_BaseOperation):
    """A read carrying the value it returned."""

    type: Literal["read"]
    value: StrictInt = Field(description="Returned value.")


class CasOperation(_BaseOperation):
    """A compare-and-swap with expected value, update value and success flag."""

    type: Literal["cas", "compare-and-swap"]
    expected: StrictInt = Field(description="Expected current value.")
    update: StrictInt = Field(description="Value written on success.")
    success: StrictBool = Field(description="Observed success flag.")


#: Discriminated union over the operation kinds.
OperationIn = Annotated[
    Union[WriteOperation, ReadOperation, CasOperation],
    Field(discriminator="type"),
]


class AuditRequest(BaseModel):
    """A complete audit submission: initial value plus 1..24 operations."""

    model_config = ConfigDict(extra="forbid")

    initial_value: StrictInt = Field(description="Initial register value.")
    operations: list[OperationIn] = Field(
        min_length=MIN_OPERATIONS,
        max_length=MAX_OPERATIONS,
        description="Completed operations (1 to 24).",
    )

    @model_validator(mode="after")
    def _check_unique_identifiers(self) -> "AuditRequest":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for op in self.operations:
            if op.id in seen:
                duplicates.add(op.id)
            seen.add(op.id)
        if duplicates:
            raise ValueError(
                "duplicate operation identifiers: " + ", ".join(sorted(duplicates))
            )
        return self


class StepOut(BaseModel):
    """Register value before and after one executed step."""

    id: str = Field(description="Identifier of the operation executed at this step.")
    before: int = Field(description="Register value before the step.")
    after: int = Field(description="Register value after the step.")


class AuditResponse(BaseModel):
    """Audit outcome.

    When ``linearizable`` is false, ``order`` and ``steps`` are null: no
    partial order is fabricated for a non-linearizable history.
    """

    linearizable: bool = Field(description="Whether a legal total order exists.")
    unique: bool | None = Field(
        description="Whether the legal total order is unique (null when not linearizable)."
    )
    order: list[str] | None = Field(
        description="Minimal legal total order (lexicographic by operation id), or null."
    )
    steps: list[StepOut] | None = Field(
        description="Register value before/after every step of ``order``, or null."
    )
    detail: str = Field(description="Human-readable summary of the outcome.")


class HealthResponse(BaseModel):
    """Liveness probe payload."""

    status: str = "ok"
