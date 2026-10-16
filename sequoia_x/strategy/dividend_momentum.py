"""股息动量价值复合策略：整合ashare-quant-strategies 策略28/95/115。"""
import pandas as pd
from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)

class DividendMomentumStrategy(BaseStrategy):
    """股息动量价值复合策略。
    数据源：akshare stock_dividend_sina() 新浪分红数据
    逻辑：股息作为安全垫，叠加动量择时、估值位置校验、量能与流动性过滤。
    Attributes:
        webhook_key: 路由到 'dividend_momentum' 飞书机器人。
        group: 策略分组 value_defense
    """
    webhook_key: str = "dividend_momentum"
    group: str = "value_defense"
    _MIN_BARS: int = 120
    _MIN_DIVIDEND_RATE: float = 2.5
    _MIN_TURNOVER: int = 80000000
    _MIN_PRICE: float = 3.0

    def run(self) -> list[str]:
        """遍历全市场标的，筛选股息+动量+价值复合条件股票代码列表。"""
        try:
            import akshare as ak
        except Exception as exc:
            logger.error(f"DividendMomentumStrategy 导入akshare失败：{exc}")
            return []

        selected_symbols = []
        stock_list = self.engine.get_all_symbols()

        for code in stock_list:
            try:
                df = self.engine.get_ohlcv(code)
                if df is None or len(df) < self._MIN_BARS:
                    continue
                close = df["close"].iloc[-1]
                ma20 = df["close"].rolling(20).mean().iloc[-1]
                ma60 = df["close"].rolling(60).mean().iloc[-1]
                vol_ma5 = df["volume"].rolling(5).mean().iloc[-1]
                vol_ma20 = df["volume"].rolling(20).mean().iloc[-1]
                # 分红数据查询
                if code.startswith("6"):
                    ak_code = f"sh{code}"
                else:
                    ak_code = f"sz{code}"
                div_df = ak.stock_dividend_sina(symbol=ak_code)
                if div_df is None or len(div_df) < 1:
                    continue
                latest_div_rate = float(div_df.iloc[0]["分红率"])
                if latest_div_rate < self._MIN_DIVIDEND_RATE:
                    continue
                # 动量校验：20日收益为正 + 均线多头
                ret_20 = (close - df["close"].iloc[-20]) / df["close"].iloc[-20]
                if ret_20 <= 0 or ma20 <= ma60:
                    continue
                # 120日价格位置过滤
                high_120 = df["high"].iloc[-120:].max()
                low_120 = df["low"].iloc[-120:].min()
                price_pos = (close - low_120) / (high_120 - low_120)
                if price_pos > 0.7:
                    continue
                # 量能校验
                if vol_ma5 < vol_ma20 * 0.9:
                    continue
                # 流动性+低价过滤
                turnover = close * vol_ma20
                if turnover < self._MIN_TURNOVER or close < self._MIN_PRICE:
                    continue
                selected_symbols.append(code)
            except Exception as exc:
                logger.warning(f"DividendMomentumStrategy [{code}] 计算异常：{exc}")
                continue

        logger.info(f"DividendMomentumStrategy 选出 {len(selected_symbols)} 只股票")
        return selected_symbols
