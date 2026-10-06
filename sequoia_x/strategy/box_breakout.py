import pandas as pd
from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy
logger = get_logger(__name__)

class BoxBreakoutStrategy(BaseStrategy):
    """放量箱体平台突破：突破20日箱体高点，成交量大于20日均量1.8倍
    Attributes:
        webhook_key: 路由到 'box_breakout' 专属飞书机器人。
        group: 策略分组 momentum_break
    """
    webhook_key: str = "box_breakout"
    group: str = "momentum_break"
    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []
        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < 20:
                    continue
                df["high_20"] = df["high"].rolling(20).max()
                df["vol_ma20"] = df["volume"].rolling(20).mean()
                last = df.iloc[-1]
                prev_high20 = df.iloc[-2]["high_20"]
                if pd.isna(last["vol_ma20"]) or pd.isna(prev_high20):
                    continue
                cond_break = last["close"] > prev_high20
                cond_vol = last["volume"] > last["vol_ma20"] * 1.8
                if cond_break and cond_vol:
                    selected.append(symbol)
            except Exception as exc:
                logger.warning(f"[{symbol}] BoxBreakout 计算失败：{exc}")
                continue
        logger.info(f"BoxBreakoutStrategy 选出 {len(selected)} 只股票")
        return selected
