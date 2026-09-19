"""Integration tests for the audit HTTP API."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class TestLinearizeHappyPath:
    def test_linearizable_unique(self):
        payload = {
            "initial_value": 0,
            "operations": [
                {"id": "w", "type": "write", "invoke": 1, "respond": 2, "value": 5},
                {"id": "r", "type": "read", "invoke": 3, "respond": 4, "value": 5},
                {
                    "id": "c",
                    "type": "cas",
                    "invoke": 5,
                    "respond": 6,
                    "expected": 5,
                    "update": 7,
                    "success": True,
                },
            ],
        }
        response = client.post("/linearize", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["linearizable"] is True
        assert body["unique"] is True
        assert body["order"] == ["w", "r", "c"]
        assert body["steps"] == [
            {"id": "w", "before": 0, "after": 5},
            {"id": "r", "before": 5, "after": 5},
            {"id": "c", "before": 5, "after": 7},
        ]

    def test_linearizable_not_unique_returns_minimal_order(self):
        payload = {
            "initial_value": 0,
            "operations": [
                {"id": "b", "type": "write", "invoke": 1, "respond": 9, "value": 2},
                {"id": "a", "type": "write", "invoke": 1, "respond": 9, "value": 1},
            ],
        }
        body = client.post("/linearize", json=payload).json()
        assert body["linearizable"] is True
        assert body["unique"] is False
        assert body["order"] == ["a", "b"]

    def test_compare_and_swap_alias_accepted(self):
        payload = {
            "initial_value": 3,
            "operations": [
                {
                    "id": "c",
                    "type": "compare-and-swap",
                    "invoke": 0,
                    "respond": 1,
                    "expected": 3,
                    "update": 4,
                    "success": True,
                }
            ],
        }
        body = client.post("/linearize", json=payload).json()
        assert body["linearizable"] is True
        assert body["steps"] == [{"id": "c", "before": 3, "after": 4}]

    def test_negative_timestamps_and_values_accepted(self):
        payload = {
            "initial_value": -10,
            "operations": [
                {"id": "w", "type": "write", "invoke": -5, "respond": -2, "value": -3},
                {"id": "r", "type": "read", "invoke": -1, "respond": 0, "value": -3},
            ],
        }
        body = client.post("/linearize", json=payload).json()
        assert body["linearizable"] is True


class TestLinearizeUnsat:
    def test_not_linearizable_returns_no_order(self):
        payload = {
            "initial_value": 0,
            "operations": [
                {"id": "r", "type": "read", "invoke": 1, "respond": 2, "value": 1},
            ],
        }
        response = client.post("/linearize", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["linearizable"] is False
        assert body["unique"] is None
        assert body["order"] is None
        assert body["steps"] is None
        assert "not linearizable" in body["detail"]


class TestRejections:
    def test_duplicate_identifiers_rejected(self):
        payload = {
            "initial_value": 0,
            "operations": [
                {"id": "x", "type": "write", "invoke": 1, "respond": 2, "value": 1},
                {"id": "x", "type": "read", "invoke": 3, "respond": 4, "value": 1},
            ],
        }
        response = client.post("/linearize", json=payload)
        assert response.status_code == 422
        assert "duplicate" in response.text

    def test_invalid_interval_rejected(self):
        payload = {
            "initial_value": 0,
            "operations": [
                {"id": "w", "type": "write", "invoke": 5, "respond": 2, "value": 1},
            ],
        }
        response = client.post("/linearize", json=payload)
        assert response.status_code == 422

    def test_type_field_mismatch_rejected(self):
        # Declared as write but carries CAS-only fields.
        payload = {
            "initial_value": 0,
            "operations": [
                {
                    "id": "w",
                    "type": "write",
                    "invoke": 1,
                    "respond": 2,
                    "value": 1,
                    "expected": 0,
                },
            ],
        }
        assert client.post("/linearize", json=payload).status_code == 422

    def test_missing_type_specific_field_rejected(self):
        # A read without its return value.
        payload = {
            "initial_value": 0,
            "operations": [{"id": "r", "type": "read", "invoke": 1, "respond": 2}],
        }
        assert client.post("/linearize", json=payload).status_code == 422

    def test_unknown_type_rejected(self):
        payload = {
            "initial_value": 0,
            "operations": [
                {"id": "x", "type": "increment", "invoke": 1, "respond": 2, "value": 1},
            ],
        }
        assert client.post("/linearize", json=payload).status_code == 422

    def test_zero_operations_rejected(self):
        payload = {"initial_value": 0, "operations": []}
        assert client.post("/linearize", json=payload).status_code == 422

    def test_twenty_five_operations_rejected(self):
        payload = {
            "initial_value": 0,
            "operations": [
                {"id": f"op{i}", "type": "write", "invoke": i, "respond": i, "value": i}
                for i in range(25)
            ],
        }
        assert client.post("/linearize", json=payload).status_code == 422

    def test_twenty_four_operations_accepted(self):
        payload = {
            "initial_value": 0,
            "operations": [
                {"id": f"op{i:02d}", "type": "write", "invoke": i, "respond": i, "value": i}
                for i in range(24)
            ],
        }
        response = client.post("/linearize", json=payload)
        assert response.status_code == 200
        assert response.json()["linearizable"] is True

    @pytest.mark.parametrize("bad_invoke", ["1", 1.5, True, None])
    def test_non_integer_timestamps_rejected(self, bad_invoke):
        payload = {
            "initial_value": 0,
            "operations": [
                {"id": "w", "type": "write", "invoke": bad_invoke, "respond": 2, "value": 1},
            ],
        }
        assert client.post("/linearize", json=payload).status_code == 422

    def test_non_boolean_success_rejected(self):
        payload = {
            "initial_value": 0,
            "operations": [
                {
                    "id": "c",
                    "type": "cas",
                    "invoke": 1,
                    "respond": 2,
                    "expected": 0,
                    "update": 1,
                    "success": "yes",
                },
            ],
        }
        assert client.post("/linearize", json=payload).status_code == 422

    def test_unexpected_top_level_field_rejected(self):
        payload = {
            "initial_value": 0,
            "operations": [
                {"id": "w", "type": "write", "invoke": 1, "respond": 2, "value": 1},
            ],
            "comment": "should not be here",
        }
        assert client.post("/linearize", json=payload).status_code == 422
