import pandas as pd
from typing import List
from .base import BaseStrategy


class BoxBreakoutStrategy(BaseStrategy):
    """
    放量箱体平台突破策略
    逻辑：收盘价突破60日箱体高点，突破当日成交量大于20日均量1.8倍，代表放量资金进场
    """
    def __init__(self):
        self.name = "放量箱体平台突破"
        self.group = "momentum_break"

    def run(self, df: pd.DataFrame) -> List[str]:
        df = df.copy()
        box_period = 60
        vol_ma_period = 20
        vol_multiple = 1.8

        # 箱体上沿：60日最高收盘价
        df["box_high"] = df["close"].rolling(window=box_period).max()
        # 20日均量
        df["vol_ma20"] = df["volume"].rolling(window=vol_ma_period).mean()

        # 条件1：今日收盘价突破箱体上沿
        cond_breakout = df["close"] > df["box_high"]
        # 条件2：放量，当前成交量大于20日均量*倍数
        cond_volume = df["volume"] > df["vol_ma20"] * vol_multiple
        # 条件3：突破当日最低价不能大幅跳水，防止长上影假突破
        cond_no_long_upper_shadow = df["low"] > df["box_high"] * 0.97

        df["signal"] = cond_breakout & cond_volume & cond_no_long_upper_shadow
        selected_codes = df[df["signal"]]["code"].unique().tolist()
        return selected_codes
