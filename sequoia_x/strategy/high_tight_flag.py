import pandas as pd
from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy
logger = get_logger(__name__)

class HighTightFlagStrategy(BaseStrategy):
    """高紧旗形HTF动量策略
    Attributes:
        webhook_key: 路由到 'htf' 专属飞书机器人。
        group: 策略分组 momentum_break
    """
    webhook_key: str = "htf"
    group: str = "momentum_break"

    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []
        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < 60:
                    continue
                last = df.iloc[-1]
                # 填入你的原有HTF选股条件
                # if cond: selected.append(symbol)
            except Exception as exc:
                logger.warning(f"[{symbol}] HighTightFlag 计算失败：{exc}")
                continue
        logger.info(f"HighTightFlagStrategy 选出 {len(selected)} 只股票")
        return selected
