import pandas as pd
import sqlite3
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

class RpsBreakoutStrategy(BaseStrategy):
    """RPS 极强动量突破策略【完整优化版】
    优化点：
    1. 20日涨幅上限：剔除短期连续暴涨的高位加速票，规避强势股补跌
    2. 最低成交额过滤：剔除流动性极差的冷门小票
    3. 极端爆量过滤：剔除单日天量换手、筹码松动标的
    """
    webhook_key: str = "rps"
    rps_period: int = 120
    rps_threshold: int = 90

    # ===== 优化参数（可自行微调） =====
    short_window = 20                # 短期涨幅观察周期
    max_short_gain = 0.40            # 20日累计涨幅上限，超过40%剔除
    min_turnover = 5000000           # 最低单日成交额（元），默认500万
    vol_spike_multiplier = 3         # 单日成交量不超过10日均量的倍数
    vol_avg_window = 10              # 均量计算周期

    def run(self) -> list[str]:
        try:
            with sqlite3.connect(self.engine.db_path) as conn:
                # 读取字段已按你实际表结构修正：volume + turnover
                df = pd.read_sql(
                    "SELECT symbol, date, close, high, volume, turnover FROM stock_daily",
                    conn
                )
        except Exception as exc:
            logger.error(f"读取数据库失败: {exc}")
            return []
        if df.empty:
            return []

        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values(['symbol', 'date'])

        # 原版：120日涨跌幅，用于RPS横向排位
        df['close_shift'] = df.groupby('symbol')['close'].shift(self.rps_period)
        df['pct_change'] = (df['close'] - df['close_shift']) / df['close_shift']

        # 新增1：20日涨跌幅，过滤短期暴涨高位票
        df['close_20_shift'] = df.groupby('symbol')['close'].shift(self.short_window)
        df['pct_20d'] = (df['close'] - df['close_20_shift']) / df['close_20_shift']

        # 新增2：10日均量，过滤极端爆量
        df['avg_vol_10'] = df.groupby('symbol')['volume'].transform(
            lambda x: x.rolling(window=self.vol_avg_window, min_periods=5).mean()
        )

        latest_date = df['date'].max()
        latest_df = df[df['date'] == latest_date].copy()
        latest_df = latest_df.dropna(subset=['pct_change'])

        # RPS横向百分位排名
        latest_df['rps'] = latest_df['pct_change'].rank(pct=True) * 100
        strong_stocks = latest_df[latest_df['rps'] >= self.rps_threshold].copy()

        # 原版：120日滚动最高价，突破判定
        roll_high = df.groupby('symbol')['high'].rolling(
            window=self.rps_period, min_periods=self.rps_period // 2
        ).max().reset_index(level=0, drop=True)
        df['roll_high'] = roll_high
        latest_roll_high = df[df['date'] == latest_date][['symbol', 'roll_high']]
        strong_stocks = strong_stocks.merge(latest_roll_high, on='symbol')

        # ========== 全部筛选条件 ==========
        # 原有核心条件：收盘价接近/突破120日高点
        cond_breakout = strong_stocks['close'] >= strong_stocks['roll_high'] * 0.90
        # 过滤1：20日涨幅不超过上限
        cond_gain_limit = strong_stocks['pct_20d'] <= self.max_short_gain
        # 过滤2：单日成交额达标，剔除小票
        cond_liquidity = strong_stocks['turnover'] >= self.min_turnover
        # 过滤3：无极端爆量，避免筹码松动
        cond_no_spike = strong_stocks['volume'] <= strong_stocks['avg_vol_10'] * self.vol_spike_multiplier

        selected = strong_stocks[
            cond_breakout & cond_gain_limit & cond_liquidity & cond_no_spike
        ]

        logger.info(f"RpsBreakoutStrategy 优化版选出 {len(selected)} 只股票")
        return selected['symbol'].tolist()
