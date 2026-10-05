from sequoia_x.strategy.base import BaseStrategy
import pandas as pd
import talib

class RsiOversoldStrategy(BaseStrategy):
    def __init__(self, engine, settings):
        super().__init__(engine=engine, settings=settings)
        self.name = "RSI超跌反转策略"
        self.webhook_key = self.settings.get("webhook_rsi", "")

    def run(self) -> list[str]:
        selected_codes = []
        # 获取全市场标的列表
        symbol_list = self.engine.get_all_symbols()
        for code in symbol_list:
            df = self.engine.load_kline(code)
            # 最小K线校验
            if len(df) < 14:
                continue
            latest_row = df.iloc[-1]
            # 基础过滤：股价大于2元
            if latest_row["close"] < 2:
                continue
            # 计算6日RSI
            df["rsi6"] = talib.RSI(df["close"], timeperiod=6)
            latest_row = df.iloc[-1]
            # 空值判断
            if pd.isna(latest_row["rsi6"]):
                continue
            # 选股条件：6日RSI < 30
            if latest_row["rsi6"] < 30:
                selected_codes.append(code)
        return selected_codes
