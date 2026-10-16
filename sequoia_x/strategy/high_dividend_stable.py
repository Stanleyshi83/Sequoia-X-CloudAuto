"""高股息稳健优选策略：整合ashare-quant-strategies 策略6/81/103。"""
import pandas as pd
from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy
logger = get_logger(__name__)
class HighDividendStableStrategy(BaseStrategy):
    """高股息稳健优选策略。
    数据源：akshare stock_dividend_cninfo() 巨潮分红数据
    逻辑：高股息率为核心，叠加低波动、盈利稳定、分红持续性、流动性过滤。
    Attributes:
        webhook_key: 路由到 'high_dividend' 飞书机器人。
        group: 策略分组 value_defense
    """
    webhook_key: str = "high_dividend"
    group: str = "value_defense"
    _MIN_BARS: int = 120       # K线最小数据长度
    _MIN_DIVIDEND_RATE: float = 3.0  # 最低股息率
    _MAX_VOL: float = 0.025    # 120日波动率上限
    _MIN_TURNOVER: int = 50000000  # 最低日均成交额
    _MIN_PRICE: float = 2.0    # 最低股价
    def run(self) -> list[str]:
        """遍历全市场标的，筛选满足高股息+低波动稳健条件股票代码列表。"""
        try:
            import akshare as ak
        except Exception as exc:
            logger.error(f"HighDividendStableStrategy 导入akshare失败：{exc}")
            return []
        selected_symbols = []
        stock_list = self.engine.get_all_symbols()
        for code in stock_list:
            try:
                df = self.engine.get_ohlcv(code)
                if df is None or len(df) < self._MIN_BARS:
                    continue
                close = df["close"].iloc[-1]
                vol_ma20 = df["volume"].rolling(20).mean().iloc[-1]
                # 流动性过滤：日均成交额
                turnover = close * vol_ma20
                if turnover < self._MIN_TURNOVER:
                    continue
                # 低波动校验
                df["daily_return"] = df["close"].pct_change()
                vol_120 = df["daily_return"].iloc[-120:].std()
                if vol_120 > self._MAX_VOL:
                    continue
                # 分红数据查询 cninfo，直接传6位代码
                div_df = ak.stock_dividend_cninfo(symbol=code)
                if div_df is None or len(div_df) < 2:
                    continue
                # 重点：cninfo字段为【派息比例】，单位：每10股派X元
                # 取最近2期分红
                recent_div = div_df.head(2)
                # 派息比例：每10股派发现金，计算股息率 = (派息/10)/当前股价 *100
                payout_text_series = recent_div["派息比例"]
                div_rates = []
                for payout_text in payout_text_series:
                    if pd.isna(payout_text) or "派" not in str(payout_text):
                        continue
                    # 提取数字，例如 "派2.5元" → 2.5
                    num_str = "".join([c for c in str(payout_text) if c in "0123456789."])
                    if not num_str:
                        continue
                    cash_per_10 = float(num_str)
                    dy = (cash_per_10 / 10.0) / close * 100
                    div_rates.append(dy)
                if len(div_rates) < 2 or min(div_rates) < self._MIN_DIVIDEND_RATE:
                    continue
                # 低价股过滤
                if close < self._MIN_PRICE:
                    continue
                # 均线校验，排除持续大跌标的
                ma20 = df["close"].rolling(20).mean().iloc[-1]
                ma60 = df["close"].rolling(60).mean().iloc[-1]
                if ma20 < ma60 * 0.85:
                    continue
                selected_symbols.append(code)
            except Exception as exc:
                logger.warning(f"HighDividendStableStrategy [{code}] 计算异常：{exc}")
                continue
        logger.info(f"HighDividendStableStrategy 选出 {len(selected_symbols)} 只股票")
        return selected_symbols
