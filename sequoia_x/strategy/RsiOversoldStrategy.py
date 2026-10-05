from sequoia_x.strategy.base import BaseStrategy
import pandas as pd
import talib

class RsiOversoldStrategy(BaseStrategy):
    def __init__(self, engine, settings):
        super().__init__(engine=engine, settings=settings)
        self.name = "RSI超跌反转策略(严格版)"
        self.webhook_key = self.settings.get("webhook_rsi", "")

    def run(self) -> list[str]:
        selected_codes = []
        # 获取全市场标的列表
        symbol_list = self.engine.get_all_symbols()
        for code in symbol_list:
            df = self.engine.load_kline(code)
            # MA20需要至少20根K线
            if len(df) < 20:
                continue
            latest_row = df.iloc[-1]
            # 基础过滤：股价大于2元
            if latest_row["close"] < 2:
                continue

            # 计算指标：6日RSI + 20日均线
            df["rsi6"] = talib.RSI(df["close"], timeperiod=6)
            df["ma20"] = talib.SMA(df["close"], timeperiod=20)

            latest_row = df.iloc[-1]
            # 空值判断，RSI或者MA20为空直接跳过
            if pd.isna(latest_row["rsi6"]) or pd.isna(latest_row["ma20"]):
                continue

            # ✅严格阈值：6日RSI<25，同时收盘价站上20日均线
            if latest_row["rsi6"] < 25 and latest_row["close"] > latest_row["ma20"]:
                selected_codes.append(code)
        return selected_codes
