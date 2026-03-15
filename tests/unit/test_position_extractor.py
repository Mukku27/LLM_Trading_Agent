import pytest

from utils.position_extractor import PositionExtractor


@pytest.fixture
def extractor():
    return PositionExtractor()


class TestSignalExtraction:
    def test_buy_signal(self, extractor):
        text = "Signal: BUY\nConfidence: HIGH\nStop Loss: $48,000\nTake Profit: $55,000\nPosition Size: 5%"
        signal, confidence, sl, tp, size = extractor.extract_trading_info(text)
        assert signal == "BUY"
        assert confidence == "HIGH"
        assert sl == 48000.0
        assert tp == 55000.0
        assert size == 0.05

    def test_sell_signal(self, extractor):
        text = "Signal: SELL\nConfidence: MEDIUM"
        signal, confidence, sl, tp, size = extractor.extract_trading_info(text)
        assert signal == "SELL"
        assert confidence == "MEDIUM"
        assert sl is None
        assert tp is None

    def test_hold_signal(self, extractor):
        text = "Signal: HOLD\nConfidence: LOW"
        signal, confidence, *_ = extractor.extract_trading_info(text)
        assert signal == "HOLD"

    def test_close_signal(self, extractor):
        text = "Signal: CLOSE\nConfidence: HIGH"
        signal, *_ = extractor.extract_trading_info(text)
        assert signal == "CLOSE"

    def test_no_signal_defaults_hold(self, extractor):
        text = "Market looks uncertain."
        signal, confidence, *_ = extractor.extract_trading_info(text)
        assert signal == "HOLD"
        assert confidence == "MEDIUM"
