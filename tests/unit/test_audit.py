"""
Unit tests for MEDIUM-10: audit log atomicity and read resilience.
"""

import json

import pytest

from execution.audit import AuditLog


@pytest.fixture
def audit(mock_logger, tmp_path):
    return AuditLog(mock_logger, data_dir=str(tmp_path))


def _write_entry(audit, event="order_placed"):
    audit.record(
        event_type=event, mode="paper", symbol="BTC/USDC",
        side="buy", amount=0.01, price=50000,
    )


class TestAtomicWrite:
    def test_record_creates_valid_jsonl(self, audit, tmp_path):
        _write_entry(audit)
        log_file = tmp_path / "audit_log.jsonl"
        assert log_file.exists()
        entries = [json.loads(line) for line in log_file.read_text().splitlines()]
        assert len(entries) == 1
        assert entries[0]["event"] == "order_placed"

    def test_no_temp_files_left_behind(self, audit, tmp_path):
        _write_entry(audit)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_multiple_writes_append(self, audit, tmp_path):
        _write_entry(audit, "order_placed")
        _write_entry(audit, "order_filled")
        log_file = tmp_path / "audit_log.jsonl"
        lines = log_file.read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "order_placed"
        assert json.loads(lines[1])["event"] == "order_filled"


class TestReadResilience:
    def test_read_recent_returns_entries(self, audit):
        _write_entry(audit)
        _write_entry(audit)
        entries = audit.read_recent(10)
        assert len(entries) == 2

    def test_corrupt_line_skipped(self, audit, tmp_path):
        """A single corrupt line should not kill the entire read."""
        log_file = tmp_path / "audit_log.jsonl"
        log_file.write_text(
            '{"event": "good1"}\n'
            'THIS IS NOT JSON\n'
            '{"event": "good2"}\n'
        )
        entries = audit.read_recent(10)
        assert len(entries) == 2
        assert entries[0]["event"] == "good1"
        assert entries[1]["event"] == "good2"

    def test_all_corrupt_returns_empty(self, audit, tmp_path):
        log_file = tmp_path / "audit_log.jsonl"
        log_file.write_text("bad line 1\nbad line 2\n")
        entries = audit.read_recent(10)
        assert entries == []

    def test_missing_file_returns_empty(self, audit):
        assert audit.read_recent() == []

    def test_read_recent_respects_limit(self, audit):
        for i in range(5):
            _write_entry(audit, f"event_{i}")
        entries = audit.read_recent(2)
        assert len(entries) == 2
        assert entries[0]["event"] == "event_3"
        assert entries[1]["event"] == "event_4"
