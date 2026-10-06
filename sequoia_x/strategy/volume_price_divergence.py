import pandas as pd
from typing import List
from .base import BaseStrategy


class VolumePriceDivergenceStrategy(BaseStrategy):
    """
    量价底背离反转策略
    逻辑：股价创N日新低，但成交量没有同步创新低，抛压衰竭，底部量价背离；叠加均线过滤
    """
    def __init__(self):
        self.name = "量价底背离反转"
        self.group = "bottom_reversal"

    def run(self, df: pd.DataFrame) -> List[str]:
        df = df.copy()
        lookback = 60
        # 60日价格低点
        df["low_60_low"] = df["low"].rolling(window=lookback).min()
        # 60日成交量低点
        df["vol_60_low"] = df["volume"].rolling(window=lookback).min()

        # 条件1：今日价格创60日新低
        cond_price_newlow = df["low"] == df["low_60_low"]
        # 条件2：当前成交量 > 60日最低成交量（价格新低，量不再创新低 → 底背离）
        cond_vol_not_newlow = df["volume"] > df["vol_60_low"] * 0.85
        # 条件3：价格不再继续创新低，出现止跌（今日收盘价高于最低价）
        cond_stop_fall = df["close"] > df["low"] * 1.01

        # 趋势过滤：收盘价大于60日均线，过滤持续单边暴跌
        df["ma60"] = df["close"].rolling(60).mean()
        cond_trend = df["close"] > df["ma60"]

        df["signal"] = cond_price_newlow & cond_vol_not_newlow & cond_stop_fall & cond_trend
        selected_codes = df[df["signal"]]["code"].unique().tolist()
        return selected_codes
