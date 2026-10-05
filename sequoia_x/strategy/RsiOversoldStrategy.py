from sequoia_x.strategy.base import BaseStrategy
import pandas as pd
import os

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
    def __init__(self, engine, settings):
        super().__init__(engine=engine, settings=settings)
        self.name = "RSI超跌反转策略(严格版)"
        # ✅ 修复：从环境变量读取，和你其他策略保持一致，避开pydantic settings .get报错
        self.webhook_key = os.getenv("webhook_rsi", "")

    def run(self) -> list[str]:
        selected_codes = []
        symbol_list = self.engine.get_all_symbols()
        for code in symbol_list:
            df = self.engine.load_kline(code)
            if len(df) < 20:
                continue
            latest_row = df.iloc[-1]
            if latest_row["close"] < 2:
                continue

            # pandas原生计算，完全不依赖TA-Lib
            df["rsi6"] = calculate_rsi(df["close"], period=6)
            df["ma20"] = df["close"].rolling(window=20).mean()

            latest_row = df.iloc[-1]
            if pd.isna(latest_row["rsi6"]) or pd.isna(latest_row["ma20"]):
                continue

            # 严格阈值 RSI<25 + 收盘价站上MA20
            if latest_row["rsi6"] < 25 and latest_row["close"] > latest_row["ma20"]:
                selected_codes.append(code)
        return selected_codes
