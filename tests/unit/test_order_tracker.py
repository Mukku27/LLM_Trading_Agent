import tempfile

import pytest

from execution.order_tracker import OrderTracker
from utils.dataclass import OrderStatus


@pytest.fixture
def tracker(mock_logger, tmp_path):
    return OrderTracker(mock_logger, data_dir=str(tmp_path))


class TestRecordAndRetrieve:
    def test_record_and_get(self, tracker):
        tracker.record_order(
            order_id="ord-1", symbol="BTC/USDC", side="buy",
            order_type="market", amount=0.01, price=50000,
            status=OrderStatus.PENDING.value,
        )
        row = tracker.get_order("ord-1")
        assert row is not None
        assert row["symbol"] == "BTC/USDC"
        assert row["status"] == OrderStatus.PENDING.value

    def test_get_nonexistent_returns_none(self, tracker):
        assert tracker.get_order("missing") is None


class TestStateTransitions:
    def test_valid_transition(self, tracker):
        tracker.record_order(
            order_id="ord-2", symbol="BTC/USDC", side="buy",
            order_type="market", amount=0.01, price=50000,
            status=OrderStatus.PENDING.value,
        )
        assert tracker.update_status("ord-2", OrderStatus.SUBMITTED.value) is True
        assert tracker.get_order("ord-2")["status"] == OrderStatus.SUBMITTED.value

    def test_invalid_transition_rejected(self, tracker):
        tracker.record_order(
            order_id="ord-3", symbol="BTC/USDC", side="buy",
            order_type="market", amount=0.01, price=50000,
            status=OrderStatus.PENDING.value,
        )
        assert tracker.update_status("ord-3", OrderStatus.FILLED.value) is False

    def test_submitted_to_filled(self, tracker):
        tracker.record_order(
            order_id="ord-4", symbol="BTC/USDC", side="buy",
            order_type="market", amount=0.01, price=50000,
            status=OrderStatus.PENDING.value,
        )
        tracker.update_status("ord-4", OrderStatus.SUBMITTED.value)
        assert tracker.update_status("ord-4", OrderStatus.FILLED.value) is True

    def test_submitted_to_cancelled(self, tracker):
        tracker.record_order(
            order_id="ord-5", symbol="BTC/USDC", side="sell",
            order_type="limit", amount=0.5, price=60000,
            status=OrderStatus.PENDING.value,
        )
        tracker.update_status("ord-5", OrderStatus.SUBMITTED.value)
        assert tracker.update_status("ord-5", OrderStatus.CANCELLED.value) is True


class TestOpenOrders:
    def test_lists_only_active_orders(self, tracker):
        tracker.record_order(
            order_id="a1", symbol="BTC/USDC", side="buy",
            order_type="market", amount=0.01, price=50000,
            status=OrderStatus.SUBMITTED.value,
        )
        tracker.record_order(
            order_id="a2", symbol="BTC/USDC", side="sell",
            order_type="market", amount=0.01, price=51000,
            status=OrderStatus.FILLED.value,
        )
        open_orders = tracker.get_open_orders()
        assert len(open_orders) == 1
        assert open_orders[0]["order_id"] == "a1"
