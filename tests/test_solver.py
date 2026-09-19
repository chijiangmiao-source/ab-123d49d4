"""Unit tests for the linearizability solver."""

from app.solver import Operation, OpKind, solve


def write(identifier, invoke, respond, value):
    return Operation(identifier, invoke, respond, OpKind.WRITE, value=value)


def read(identifier, invoke, respond, value):
    return Operation(identifier, invoke, respond, OpKind.READ, value=value)


def cas(identifier, invoke, respond, expected, update, success):
    return Operation(
        identifier,
        invoke,
        respond,
        OpKind.CAS,
        expected=expected,
        update=update,
        success=success,
    )


class TestSingleOperation:
    def test_single_write(self):
        result = solve(0, [write("w", 1, 2, 5)])
        assert result.linearizable
        assert result.unique
        assert result.order == ("w",)
        assert [(s.identifier, s.before, s.after) for s in result.steps] == [("w", 0, 5)]

    def test_matching_read(self):
        result = solve(7, [read("r", 0, 0, 7)])
        assert result.linearizable
        assert result.unique
        assert [(s.before, s.after) for s in result.steps] == [(7, 7)]

    def test_mismatching_read_is_unsat(self):
        result = solve(0, [read("r", 1, 2, 1)])
        assert not result.linearizable
        assert result.order is None
        assert result.steps is None


class TestRealTimePrecedence:
    def test_response_not_later_than_invoke_forces_order(self):
        # a.respond == b.invoke, so a must precede b even though the write of
        # b would otherwise also be legal first.
        a = write("a", 1, 2, 1)
        b = read("b", 2, 3, 1)
        result = solve(0, [b, a])
        assert result.linearizable
        assert result.unique
        assert result.order == ("a", "b")

    def test_read_observes_earlier_write(self):
        # w is entirely before r, so r must observe w's value.
        result = solve(0, [write("w", 1, 2, 9), read("r", 3, 4, 9)])
        assert result.linearizable
        assert result.order == ("w", "r")

    def test_read_contradicting_forced_order_is_unsat(self):
        # w writes 1 strictly before r, but r claims to return 0.
        result = solve(0, [write("w", 1, 2, 1), read("r", 3, 4, 0)])
        assert not result.linearizable

    def test_simultaneous_zero_length_operations_cycle(self):
        # Both intervals are the single instant 1, so each operation's
        # response is not later than the other's invocation: the precedence
        # requirements form a cycle and no total order can respect them.
        result = solve(0, [write("a", 1, 1, 1), write("b", 1, 1, 2)])
        assert not result.linearizable


class TestRegisterSemantics:
    def test_cas_success_updates_value(self):
        result = solve(5, [cas("c", 1, 2, expected=5, update=9, success=True)])
        assert result.linearizable
        assert [(s.before, s.after) for s in result.steps] == [(5, 9)]

    def test_cas_failure_keeps_value(self):
        result = solve(
            1,
            [
                cas("c", 1, 2, expected=2, update=3, success=False),
                read("r", 3, 4, 1),
            ],
        )
        assert result.linearizable
        assert result.order == ("c", "r")
        assert [(s.before, s.after) for s in result.steps] == [(1, 1), (1, 1)]

    def test_cas_success_flag_mismatch_is_unsat(self):
        # Current value equals expected, so the CAS must have succeeded.
        result = solve(5, [cas("c", 1, 2, expected=5, update=9, success=False)])
        assert not result.linearizable

    def test_cas_failure_flag_mismatch_is_unsat(self):
        # Current value differs from expected, so the CAS must have failed.
        result = solve(5, [cas("c", 1, 2, expected=4, update=9, success=True)])
        assert not result.linearizable

    def test_read_forces_relative_order_of_concurrent_writes(self):
        # w1 and w2 overlap; r overlaps both and returns 1, so r must be
        # placed after w1 and before w2's value becomes visible -- i.e. w2
        # either runs entirely before w1 or after r.
        ops = [
            write("w1", 1, 10, 1),
            write("w2", 1, 10, 2),
            read("r", 2, 3, 1),
        ]
        result = solve(0, ops)
        assert result.linearizable
        assert not result.unique
        # Legal orders: (w1, r, w2) and (w2, w1, r); the lexicographically
        # minimal identifier sequence is (r? no -- r cannot be first because
        # the initial value is 0 and r returns 1) -> (w1, r, w2).
        assert result.order == ("w1", "r", "w2")
        assert [(s.before, s.after) for s in result.steps] == [(0, 1), (1, 1), (1, 2)]


class TestMinimalityAndUniqueness:
    def test_lexicographically_smallest_order_is_returned(self):
        # Two fully concurrent writes: both orders are legal; "a" < "b".
        result = solve(0, [write("b", 1, 5, 2), write("a", 1, 5, 1)])
        assert result.linearizable
        assert not result.unique
        assert result.order == ("a", "b")

    def test_lexicographic_order_compares_elementwise(self):
        # "a" and "ab" are both legal first; "ab" < "b" is irrelevant, the
        # comparison is per-position: ("a", ...) < ("ab", ...) because
        # "a" < "ab".
        ops = [
            write("ab", 1, 9, 1),
            write("a", 1, 9, 2),
            write("b", 1, 9, 3),
        ]
        result = solve(0, ops)
        assert result.linearizable
        assert not result.unique
        assert result.order == ("a", "ab", "b")

    def test_unique_order_detected(self):
        ops = [
            write("w", 1, 2, 1),
            read("r", 3, 4, 1),
            cas("c", 5, 6, expected=1, update=0, success=True),
        ]
        result = solve(0, ops)
        assert result.linearizable
        assert result.unique
        assert result.order == ("w", "r", "c")
        assert [(s.before, s.after) for s in result.steps] == [(0, 1), (1, 1), (1, 0)]

    def test_unsat_returns_no_partial_order(self):
        ops = [read("r", 1, 2, 3), write("w", 4, 5, 3)]
        result = solve(0, ops)
        assert not result.linearizable
        assert result.order is None
        assert result.steps is None


class TestLargerHistories:
    def test_24_concurrent_writes(self):
        # Maximum size, maximally unconstrained: 24! legal orders exist.
        ops = [write(f"w{i:02d}", 0, 100, i) for i in range(24)]
        result = solve(0, ops)
        assert result.linearizable
        assert not result.unique
        assert result.order == tuple(f"w{i:02d}" for i in range(24))
        assert result.steps[-1].after == 23

    def test_long_forced_chain(self):
        # A chain of 24 operations each strictly after the previous one.
        ops = []
        for i in range(24):
            if i % 2 == 0:
                ops.append(write(f"op{i:02d}", i * 2, i * 2 + 1, i))
            else:
                ops.append(read(f"op{i:02d}", i * 2, i * 2 + 1, i - 1))
        result = solve(0, ops)
        assert result.linearizable
        assert result.unique
        assert result.order == tuple(f"op{i:02d}" for i in range(24))
