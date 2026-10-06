import pandas as pd
from typing import List
from .base import BaseStrategy


class BollingerVolatilityStrategy(BaseStrategy):
    """
    布林带波动率通道选股
    逻辑：20日布林带，波动率压缩(带宽处于60日低位) + 回踩下轨后价格拐头向上，叠加长期均线过滤
    """
    def __init__(self):
        self.name = "布林带波动率通道"
        self.group = "volatility"

    def run(self, df: pd.DataFrame) -> List[str]:
        df = df.copy()
        # 布林带参数
        bb_period = 20
        df["ma_mid"] = df["close"].rolling(window=bb_period).mean()
        df["std"] = df["close"].rolling(window=bb_period).std()
        df["bb_upper"] = df["ma_mid"] + 2 * df["std"]
        df["bb_lower"] = df["ma_mid"] - 2 * df["std"]
        df["bb_band_width"] = df["bb_upper"] - df["bb_lower"]

        # 波动率压缩：带宽处于近60日30%分位以内
        df["band_60_quantile03"] = df["bb_band_width"].rolling(window=60).quantile(0.3)
        cond_compress = df["bb_band_width"] <= df["band_60_quantile03"]

        # 回踩下轨：最低价贴近下轨，允许3%容错
        cond_touch_lower = df["low"] <= df["bb_lower"] * 1.03
        # 价格拐头向上：今日收盘价 > 昨日收盘价
        cond_reverse = df["close"] > df["close"].shift(1)

        # 长期趋势过滤：站上120日均线，规避大熊市下跌标的
        df["ma120"] = df["close"].rolling(window=120).mean()
        cond_trend = df["close"] > df["ma120"]

        # 合并条件
        df["signal"] = cond_compress & cond_touch_lower & cond_reverse & cond_trend

        selected_codes = df[df["signal"]]["code"].unique().tolist()
        return selected_codes
