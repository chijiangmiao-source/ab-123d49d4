"""Linearizability solver for a single read/write/compare-and-swap register.

Given an initial register value and a set of completed operations (each with an
invocation time and a response time), the solver searches for a total order of
the operations that

1. respects the real-time precedence rule: if the response time of operation A
   is not later than the invocation time of operation B (``A.respond <=
   B.invoke``), then A must appear before B, and
2. is consistent with the register semantics when executed step by step
   starting from the initial value:

   * a write always takes effect and sets the register to its write value;
   * a read must return exactly the current register value;
   * a compare-and-swap succeeds iff the current value equals its expected
     value, and its recorded success flag must match that outcome; on success
     the register takes the update value, otherwise it keeps its value.

Because every sequence is validated incrementally, the depth-first search that
always tries the not-yet-placed operation with the lexicographically smallest
identifier first enumerates all legal total orders in lexicographic order of
operation identifiers.  The first solution found is therefore the minimal
legal total order, and searching for at most two solutions decides whether the
legal total order is unique.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: The search only ever needs to distinguish "no solution", "exactly one
#: solution" and "more than one solution", so it stops after this many.
_SOLUTION_LIMIT = 2


class OpKind(str, Enum):
    """Supported register operation kinds."""

    WRITE = "write"
    READ = "read"
    CAS = "cas"


@dataclass(frozen=True)
class Operation:
    """A single completed register operation.

    Only the fields relevant to ``kind`` are populated:

    * ``write``: ``value`` is the value being written;
    * ``read``: ``value`` is the value the read returned;
    * ``cas``: ``expected``/``update``/``success`` describe the
      compare-and-swap.
    """

    identifier: str
    invoke: int
    respond: int
    kind: OpKind
    value: int | None = None
    expected: int | None = None
    update: int | None = None
    success: bool | None = None


@dataclass(frozen=True)
class Step:
    """One executed step of a legal total order."""

    identifier: str
    before: int
    after: int


@dataclass(frozen=True)
class SolveResult:
    """Outcome of the linearizability audit."""

    linearizable: bool
    #: Only meaningful when ``linearizable`` is True.
    unique: bool
    #: The minimal legal total order as operation identifiers, or None.
    order: tuple[str, ...] | None
    #: Register value before/after every step of ``order``, or None.
    steps: tuple[Step, ...] | None


def _next_value(op: Operation, current: int) -> int | None:
    """Return the register value after ``op``, or None if ``op`` is impossible.

    ``op`` is impossible exactly when its observed result (read return value
    or CAS success flag) contradicts the current register value.
    """
    if op.kind is OpKind.WRITE:
        # A write always takes effect.
        return op.value
    if op.kind is OpKind.READ:
        # A read must return the value current at its position.
        return current if op.value == current else None
    # Compare-and-swap: the success flag must reflect whether the current
    # value equals the expected value.
    matched = current == op.expected
    if matched != op.success:
        return None
    return op.update if matched else current


def _predecessor_masks(ops: list[Operation]) -> list[int]:
    """Bit mask of operations that must precede each operation.

    Operation ``i`` must precede operation ``j`` (``i != j``) whenever
    ``ops[i].respond <= ops[j].invoke``.
    """
    n = len(ops)
    predecessors = [0] * n
    for i in range(n):
        respond_i = ops[i].respond
        bit_i = 1 << i
        for j in range(n):
            if i != j and respond_i <= ops[j].invoke:
                predecessors[j] |= bit_i
    return predecessors


def _find_legal_orders(
    initial: int, ops: list[Operation], limit: int
) -> list[tuple[int, ...]]:
    """Find up to ``limit`` legal total orders, in lexicographic order.

    Returns a list of index tuples into ``ops``.  Because candidates are
    always tried in ascending identifier order, the solutions are produced in
    lexicographic order of their identifier sequences, so the first element
    is the minimal legal total order.
    """
    n = len(ops)
    # Indices sorted by identifier define the candidate exploration order.
    by_identifier = sorted(range(n), key=lambda i: ops[i].identifier)
    predecessors = _predecessor_masks(ops)
    full_mask = (1 << n) - 1

    solutions: list[tuple[int, ...]] = []
    # States (placed mask, current value) proven to have no legal completion.
    # The set of legal completions of a state depends only on these two
    # values, so this memoisation is sound and keeps the search tractable.
    dead: set[tuple[int, int]] = set()

    def dfs(mask: int, current: int, path: list[int]) -> None:
        if len(solutions) >= limit:
            return
        if mask == full_mask:
            solutions.append(tuple(path))
            return
        state = (mask, current)
        if state in dead:
            return
        solutions_before = len(solutions)
        for i in by_identifier:
            bit = 1 << i
            if mask & bit or predecessors[i] & ~mask:
                continue  # already placed, or a predecessor is still missing
            nxt = _next_value(ops[i], current)
            if nxt is None:
                continue  # contradicts the register semantics at this point
            path.append(i)
            dfs(mask | bit, nxt, path)
            path.pop()
            if len(solutions) >= limit:
                return
        if len(solutions) == solutions_before:
            # No legal completion exists from this state; never revisit it.
            dead.add(state)

    dfs(0, initial, [])
    return solutions


def solve(initial: int, ops: list[Operation]) -> SolveResult:
    """Audit the given history for linearizability.

    Returns the minimal legal total order (by lexicographic order of
    operation identifiers), the register value before and after every step,
    and whether the legal total order is unique.  When no legal total order
    exists, ``order`` and ``steps`` are None -- no partial order is fabricated.
    """
    solutions = _find_legal_orders(initial, ops, _SOLUTION_LIMIT)
    if not solutions:
        return SolveResult(linearizable=False, unique=False, order=None, steps=None)

    best = solutions[0]
    steps: list[Step] = []
    current = initial
    for i in best:
        nxt = _next_value(ops[i], current)
        # ``best`` is a legal order, so every step must be executable.
        assert nxt is not None  # noqa: S101 - internal invariant
        steps.append(Step(identifier=ops[i].identifier, before=current, after=nxt))
        current = nxt

    return SolveResult(
        linearizable=True,
        unique=len(solutions) == 1,
        order=tuple(ops[i].identifier for i in best),
        steps=tuple(steps),
    )
