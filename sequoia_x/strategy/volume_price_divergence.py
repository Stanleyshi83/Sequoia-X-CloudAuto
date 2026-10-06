import pandas as pd
from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy
logger = get_logger(__name__)

class VolumePriceDivergenceStrategy(BaseStrategy):
    """量价底背离反转：价格创20日新低，成交量不再创新低，站上60日均线
    Attributes:
        webhook_key: 路由到 'vol_price_div' 专属飞书机器人。
        group: 策略分组 bottom_reversal
    """
    webhook_key: str = "vol_price_div"
    group: str = "bottom_reversal"
    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []
        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < 60:
                    continue
                df["price_low_20"] = df["close"].rolling(20).min()
                df["vol_low_20"] = df["volume"].rolling(20).min()
                df["ma60"] = df["close"].rolling(60).mean()
                last = df.iloc[-1]
                if pd.isna(last["price_low_20"]) or pd.isna(last["vol_low_20"]) or pd.isna(last["ma60"]):
                    continue
                cond_price_new_low = last["close"] == last["price_low_20"]
                cond_vol_not_new_low = last["volume"] > last["vol_low_20"] * 1.02
                cond_ma60 = last["close"] > last["ma60"]
                if cond_price_new_low and cond_vol_not_new_low and cond_ma60:
                    selected.append(symbol)
            except Exception as exc:
                logger.warning(f"[{symbol}] VolumePriceDivergence 计算失败：{exc}")
                continue
        logger.info(f"VolumePriceDivergenceStrategy 选出 {len(selected)} 只股票")
        return selected
