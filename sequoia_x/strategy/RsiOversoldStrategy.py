import pandas as pd
from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy
logger = get_logger(__name__)
def calculate_rsi(series, period=6):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi
class RsiOversoldStrategy(BaseStrategy):
    """RSI超跌反转策略：RSI6低于25，同时收盘价站上20日均线。
    Attributes:
        webhook_key: 路由到 'rsi_oversold' 专属飞书机器人。
        group: 策略分组 bottom_reversal
    """
    webhook_key: str = "rsi_oversold"
    group: str = "bottom_reversal"
    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []
        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < 20:
                    continue
                df["rsi6"] = calculate_rsi(df["close"], period=6)
                df["ma20"] = df["close"].rolling(window=20).mean()
                last = df.iloc[-1]
                if pd.isna(last["rsi6"]) or pd.isna(last["ma20"]):
                    continue
                # 选股条件：RSI6 < 25 且 收盘价 > MA20
                if last["rsi6"] < 25 and last["close"] > last["ma20"]:
                    selected.append(symbol)
            except Exception as exc:
                logger.warning(f"[{symbol}] RsiOversold 计算失败：{exc}")
                continue
        logger.info(f"RsiOversoldStrategy 选出 {len(selected)} 只股票")
        return selected
