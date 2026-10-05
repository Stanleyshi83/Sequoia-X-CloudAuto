from strategy.base import BaseStrategy
import pandas as pd
import talib


class RsiOversoldStrategy(BaseStrategy):
    def __init__(self):
        self.name = "RSI超跌反转策略"
        self.desc = "ashare-quant-strategies移植，6日RSI低于30，短期超卖"

    def run(self, df: pd.DataFrame) -> bool:
        # 最小K线数量校验：计算RSI至少需要14根K线，保守设置
        if len(df) < 14:
            return False

        # 计算6日RSI
        df["rsi6"] = talib.RSI(df["close"], timeperiod=6)

        # 取最新交易日数据
        latest_row = df.iloc[-1]

        # 选股条件：6日RSI < 30
        condition = latest_row["rsi6"] < 30

        return condition
