"""股息动量价值复合策略：整合ashare‑quant‑strategies 策略28/95/115。"""
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy
logger = get_logger(__name__)
# 模块级内存缓存：避免两个高股息策略重复请求同一只股票分红数据
_dividend_cache = dict()
# 并发请求配置，可按需调，10比较稳妥，太高容易触发ak限流
MAX_WORKERS = 10
AKSHARE_TIMEOUT = 15

def _fetch_single_dividend(code: str):
    """单只股票分红拉取函数，用于线程池"""
    try:
        import akshare as ak
        # 增加超时控制
        div_df = ak.stock_dividend_cninfo(symbol=code)
        return code, div_df
    except Exception:
        return code, None

def _batch_fetch_dividends(stock_code_list: list[str]):
    """批量并发拉取分红数据，填充全局缓存，跳过已经缓存的标的"""
    need_fetch_codes = [c for c in stock_code_list if c not in _dividend_cache]
    if not need_fetch_codes:
        logger.info("所有股票分红数据已存在缓存，无需拉取")
        return
    logger.info(f"开始并发批量拉取分红数据，待获取数量：{len(need_fetch_codes)}，并发数：{MAX_WORKERS}")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        task_map = {executor.submit(_fetch_single_dividend, code): code for code in need_fetch_codes}
        for task in as_completed(task_map):
            code, div_df = task.result()
            if div_df is not None and not div_df.empty:
                _dividend_cache[code] = div_df
    logger.info(f"批量分红拉取完成，当前缓存标的总数：{len(_dividend_cache)}")


class DividendMomentumStrategy(BaseStrategy):
    """股息动量价值复合策略。
    数据源：akshare stock_dividend_cninfo() 巨潮分红数据
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
    def run(self, stock_code_list: list[str]) -> list[str]:
        """遍历全市场标的，筛选股息+动量+价值复合条件股票代码列表。"""
        try:
            import akshare as ak
        except Exception as exc:
            logger.error(f"DividendMomentumStrategy 导入akshare失败：{exc}")
            return []
        selected_symbols = []
        stock_list = stock_code_list
        # =====【核心优化点】前置批量并发拉取分红，一次性填充全局缓存=====
        _batch_fetch_dividends(stock_list)

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
                # 直接读取全局缓存，不再网络请求
                div_df = _dividend_cache.get(code)
                if div_df is None or div_df.empty:
                    continue
                # 动态匹配派息列，不写死中文列名
                payout_col = None
                for col in div_df.columns:
                    if "派息" in str(col) or "现金分红" in str(col):
                        payout_col = col
                        break
                if payout_col is None:
                    continue
                latest_payout_text = div_df.iloc[0][payout_col]
                if pd.isna(latest_payout_text) or "派" not in str(latest_payout_text):
                    continue
                # 提取每10股派现金额，计算股息率
                num_str = "".join([c for c in str(latest_payout_text) if c in "0123456789."])
                if not num_str:
                    continue
                cash_per_10 = float(num_str)
                latest_div_rate = (cash_per_10 / 10.0) / close * 100
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
