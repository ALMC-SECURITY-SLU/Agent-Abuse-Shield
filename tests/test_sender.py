"""Tests for sender.py — uses respx to mock httpx HTTP calls."""
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from almc_shield.config import ApiConfig, Config, OutboxConfig, SenderConfig
from almc_shield.outbox import Outbox
from almc_shield.parser import BanEvent
from almc_shield.sender import Sender


@pytest.fixture
def cfg() -> Config:
    return Config(
        api=ApiConfig(url="https://almc.example/api/v1/abuse", api_key="ab_live_test"),
        outbox=OutboxConfig(),
        sender=SenderConfig(flush_interval_seconds=1, backoff_min=0, backoff_max=1, circuit_breaker_failures_to_open=3),
    )


@pytest.fixture
def outbox(tmp_path: Path) -> Outbox:
    return Outbox(str(tmp_path / "outbox.db"))


def test_drains_outbox_on_2xx(outbox: Outbox, cfg: Config) -> None:
    outbox.enqueue(BanEvent("sshd", "1.2.3.4", datetime.utcnow()))
    outbox.enqueue(BanEvent("sshd", "5.6.7.8", datetime.utcnow()))

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_response.json.return_value = {"accepted": 2}
    mock_client.post.return_value = mock_response

    sender = Sender(cfg, outbox, hostname="testhost", http_client=mock_client)
    sender.flush_once()

    assert outbox.size() == 0
    mock_client.post.assert_called_once()
    args, kwargs = mock_client.post.call_args
    assert "/report" in args[0]
    assert kwargs["headers"]["Authorization"] == "Bearer ab_live_test"
    assert "Idempotency-Key" in kwargs["headers"]


def test_keeps_in_outbox_on_5xx(outbox: Outbox, cfg: Config) -> None:
    outbox.enqueue(BanEvent("sshd", "1.2.3.4", datetime.utcnow()))

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"
    mock_client.post.return_value = mock_response

    sender = Sender(cfg, outbox, hostname="testhost", http_client=mock_client)
    sender.flush_once()

    assert outbox.size() == 1   # NOT drained


def test_drops_batch_on_400_payload(outbox: Outbox, cfg: Config) -> None:
    outbox.enqueue(BanEvent("sshd", "1.2.3.4", datetime.utcnow()))

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 422
    mock_response.text = "validation failed"
    mock_response.json.return_value = {"error": "invalid_payload"}
    mock_client.post.return_value = mock_response

    sender = Sender(cfg, outbox, hostname="testhost", http_client=mock_client)
    sender.flush_once()

    # Batch dropped to avoid stuck queue
    assert outbox.size() == 0


def test_pauses_on_401(outbox: Outbox, cfg: Config) -> None:
    outbox.enqueue(BanEvent("sshd", "1.2.3.4", datetime.utcnow()))

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.json.return_value = {"error": "invalid_api_key"}
    mock_client.post.return_value = mock_response

    sender = Sender(cfg, outbox, hostname="testhost", http_client=mock_client)
    sender.flush_once()

    # Outbox preserved (waiting for admin to fix API key)
    assert outbox.size() == 1
    assert sender.auth_blocked is True


def test_circuit_breaker_opens_after_failures(outbox: Outbox, cfg: Config) -> None:
    for i in range(5):
        outbox.enqueue(BanEvent("sshd", f"10.0.0.{i}", datetime.utcnow()))

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = ""
    mock_client.post.return_value = mock_response

    sender = Sender(cfg, outbox, hostname="testhost", http_client=mock_client)
    for _ in range(5):
        sender.flush_once()

    # Should have opened breaker after 3 consecutive failures (cfg.failures_to_open=3)
    from almc_shield.circuit_breaker import State
    assert sender.breaker.state == State.OPEN


def test_idempotency_key_is_deterministic_from_outbox_ids(outbox, cfg) -> None:
    """If the agent retries the same batch (same outbox row IDs), it MUST use
    the same Idempotency-Key so the server cache deduplicates."""
    outbox.enqueue(BanEvent("sshd", "1.2.3.4", datetime.utcnow()))
    outbox.enqueue(BanEvent("sshd", "5.6.7.8", datetime.utcnow()))

    # First attempt: server fails (5xx), so outbox is NOT drained
    mock_client_1 = MagicMock()
    mock_response_1 = MagicMock()
    mock_response_1.status_code = 503
    mock_response_1.text = ""
    mock_client_1.post.return_value = mock_response_1

    sender_1 = Sender(cfg, outbox, hostname="testhost", http_client=mock_client_1)
    sender_1.flush_once()
    key_1 = mock_client_1.post.call_args.kwargs["headers"]["Idempotency-Key"]

    # Second attempt: same outbox state (rows still there)
    mock_client_2 = MagicMock()
    mock_response_2 = MagicMock()
    mock_response_2.status_code = 202
    mock_response_2.json.return_value = {"accepted": 2}
    mock_client_2.post.return_value = mock_response_2

    sender_2 = Sender(cfg, outbox, hostname="testhost", http_client=mock_client_2)
    sender_2.flush_once()
    key_2 = mock_client_2.post.call_args.kwargs["headers"]["Idempotency-Key"]

    # Both retries must use the SAME key (deterministic from row IDs)
    assert key_1 == key_2, f"Idempotency-Key must be deterministic, got {key_1} != {key_2}"
