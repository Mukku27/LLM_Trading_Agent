import threading

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

    def test_nonexistent_order_returns_false(self, tracker):
        assert tracker.update_status("missing", OrderStatus.SUBMITTED.value) is False


class TestAtomicUpdateStatus:
    def test_concurrent_updates_only_one_wins(self, tracker):
        """Simulate two threads racing to transition SUBMITTED → FILLED/CANCELLED."""
        tracker.record_order(
            order_id="race-1", symbol="BTC/USDC", side="buy",
            order_type="market", amount=0.01, price=50000,
            status=OrderStatus.PENDING.value,
        )
        tracker.update_status("race-1", OrderStatus.SUBMITTED.value)

        results = {}
        barrier = threading.Barrier(2)

        def try_update(name, new_status):
            barrier.wait()
            results[name] = tracker.update_status("race-1", new_status)

        t1 = threading.Thread(target=try_update, args=("fill", OrderStatus.FILLED.value))
        t2 = threading.Thread(target=try_update, args=("cancel", OrderStatus.CANCELLED.value))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one should succeed
        assert sum(results.values()) == 1
        final = tracker.get_order("race-1")["status"]
        assert final in (OrderStatus.FILLED.value, OrderStatus.CANCELLED.value)

    def test_second_transition_from_same_state_fails(self, tracker):
        """After a successful transition, a second attempt from the old state fails."""
        tracker.record_order(
            order_id="race-2", symbol="BTC/USDC", side="buy",
            order_type="market", amount=0.01, price=50000,
            status=OrderStatus.PENDING.value,
        )
        tracker.update_status("race-2", OrderStatus.SUBMITTED.value)
        assert tracker.update_status("race-2", OrderStatus.FILLED.value) is True
        # Now it's FILLED — trying to move from SUBMITTED again should fail
        assert tracker.update_status("race-2", OrderStatus.CANCELLED.value) is False


class TestRecordOrderPreservesHistory:
    def test_re_record_preserves_created_at(self, tracker):
        """INSERT ON CONFLICT should update mutable fields but preserve created_at."""
        tracker.record_order(
            order_id="hist-1", symbol="BTC/USDC", side="buy",
            order_type="market", amount=0.01, price=50000,
            status=OrderStatus.PENDING.value,
        )
        original = tracker.get_order("hist-1")
        original_created = original["created_at"]

        # Re-record with updated status (simulates a fill update)
        tracker.record_order(
            order_id="hist-1", symbol="BTC/USDC", side="buy",
            order_type="market", amount=0.01, price=50000,
            status=OrderStatus.FILLED.value,
            filled_amount=0.01, avg_price=50100, fee=0.05,
        )
        updated = tracker.get_order("hist-1")
        assert updated["created_at"] == original_created  # preserved
        assert updated["status"] == OrderStatus.FILLED.value  # updated
        assert updated["filled_amount"] == 0.01  # updated
        assert updated["updated_at"] != original_created  # refreshed

    def test_re_record_preserves_immutable_fields(self, tracker):
        """Side, symbol, order_type, amount should not change on conflict update."""
        tracker.record_order(
            order_id="hist-2", symbol="BTC/USDC", side="buy",
            order_type="market", amount=0.5, price=50000,
            status=OrderStatus.PENDING.value,
        )
        # Re-record with different side/amount (should be ignored by ON CONFLICT)
        tracker.record_order(
            order_id="hist-2", symbol="ETH/USDC", side="sell",
            order_type="limit", amount=999,  price=99999,
            status=OrderStatus.FILLED.value,
        )
        row = tracker.get_order("hist-2")
        assert row["symbol"] == "BTC/USDC"  # preserved
        assert row["side"] == "buy"  # preserved
        assert row["order_type"] == "market"  # preserved
        assert row["amount"] == 0.5  # preserved
        assert row["status"] == OrderStatus.FILLED.value  # updated


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
