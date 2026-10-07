"""
High Tight Flag (HTF) 高紧旗形策略
原生来源：https://github.com/sngyai/Sequoia-X
适配改造：增加 group / webhook_key 属性，兼容Sequoia-X V2主程序，保留全部原始选股逻辑
"""
import pandas as pd
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


class HighTightFlagStrategy(BaseStrategy):
    group: str = "momentum_break"
    webhook_key: str = "htf"

    def run(self) -> list[str]:
        selected_codes: list[str] = []
        # 获取全市场股票列表
        stock_list = self.engine.get_all_symbols()
        for code in stock_list:
            # 获取近60根日线K线，接口替换 get_kline → get_ohlcv
            df = self.engine.get_ohlcv(code)
            if df is None or len(df) < 60:
                continue
            df = df.reset_index(drop=True)

            # ========== 原生HTF核心条件 ==========
            # 条件1：前期出现大幅上涨旗杆（20日内涨幅>=60%）
            high_20 = df["high"].iloc[-40:-20].max()
            low_20 = df["low"].iloc[-40:-20].min()
            pole_return = (high_20 - low_20) / low_20
            if pole_return < 0.60:
                continue
            # 条件2：旗杆创出这一段新高
            pole_high = df["high"].iloc[-40:-20].max()
            if df["high"].iloc[-20:].max() > pole_high:
                continue
            # 条件3：旗形阶段（最近20根K线），波动收缩（高低点振幅小于旗杆涨幅的一半）
            flag_high = df["high"].iloc[-20:].max()
            flag_low = df["low"].iloc[-20:].min()
            flag_range = (flag_high - flag_low) / flag_low
            if flag_range > pole_return * 0.5:
                continue
            # 条件4：旗形阶段成交量逐步萎缩（最近5日均量小于旗杆5日均量的0.7倍）
            vol_pole = df["volume"].iloc[-40:-20].tail(5).mean()
            vol_flag = df["volume"].iloc[-20:].tail(5).mean()
            if vol_flag / vol_pole > 0.7:
                continue
            # 条件5：当前收盘价处于旗形区间上沿附近，等待突破
            close = df["close"].iloc[-1]
            if close < flag_high * 0.95:
                continue
            # 全部条件满足，入选
            selected_codes.append(code)
        return selected_codes
