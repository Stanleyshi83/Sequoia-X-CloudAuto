"""
Sequoia-X V2 主程序入口（GitHub Actions适配版）
两种运行模式：
  python main.py               # 日常模式：8进程增量补数据 + 跑策略 + 飞书推送（2~3分钟）
  python main.py --backfill    # 回填模式：baostock 拉全市场历史K线（首次/补数据用，约12分钟）
新增功能：
1. 自动收集所有策略选股结果
2. 计算多策略重合股票
3. 统一推送汇总结果到飞书
4. 自动生成东方财富自选股导入TXT文件
"""
import argparse
import sys
import os
import json
import requests
from collections import Counter
from dotenv import load_dotenv
load_dotenv()  # 本地运行加载.env，GitHub环境自动跳过不影响
from datetime import date
import socket
socket.setdefaulttimeout(10.0)
from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine
from sequoia_x.notify.feishu import FeishuNotifier
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
from sequoia_x.strategy.private_placement import PrivatePlacementStrategy
# 改动：导入RsiOversoldStrategy，文件名为 RsiOversoldStrategy.py
from sequoia_x.strategy.RsiOversoldStrategy import RsiOversoldStrategy

def send_summary_to_feishu(all_results: dict, overlap: list, webhook: str) -> bool:
    """推送汇总结果+多策略重合到飞书（新增功能）"""
    date_str = date.today().strftime("%Y-%m-%d")
    content = f"【选股汇总】 {date_str}\n\n"
    # 置顶多策略重合
    content += f"🔥 多策略重合股票（共{len(overlap)}只）：\n"
    if overlap:
        content += "、".join(overlap)
    else:
        content += "今日无重合股票"
    content += "\n\n" + "---" + "\n\n"
    # 各策略结果概览
    for strategy_name, codes in all_results.items():
        content += f"📌 {strategy_name}：{len(codes)}只\n"
    content += "\n💡 附件可下载东方财富导入文件"
    payload = {
        "msg_type": "text",
        "content": {"text": content}
    }
    try:
        resp = requests.post(webhook, data=json.dumps(payload), timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"汇总推送失败: {e}")
        return False

def generate_eastmoney_file(codes: list) -> str:
    """生成东方财富可导入的TXT文件（新增功能）"""
    file_path = "eastmoney_import.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        for code in codes:
            f.write(code + "\n")
    return file_path

def main() -> None:
    parser = argparse.ArgumentParser(description="Sequoia-X V2 选股系统")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="回填模式：通过 baostock 拉取全市场历史 K 线（约12分钟）",
    )
    args = parser.parse_args()
    try:
        # 1. 初始化配置
        settings = get_settings()
        # 2. 初始化日志
        logger = get_logger(__name__)
        logger.info("Sequoia-X V2 启动")
        # 3. 初始化数据引擎
        engine = DataEngine(settings)
        if args.backfill:
            # ── 回填模式：单线程保守拉历史 K 线，自动多轮重跑 ──
            logger.info("进入回填模式...")
            all_symbols = engine.get_all_symbols()
            engine.backfill(all_symbols)
            logger.info("Sequoia-X V2 回填模式运行完成")
            return
        # ── 日常模式：单次 API 补今天 + 策略 + 推送 ──
        logger.info("开始拉取最新快照...")
        count = engine.sync_today_bulk()
        logger.info(f"快照同步完成，写入 {count} 只股票")
        # 4. 策略列表（新增策略在此追加即可）
        strategies: list[BaseStrategy] = [
            MaVolumeStrategy(engine=engine, settings=settings),
            TurtleTradeStrategy(engine=engine, settings=settings),
            HighTightFlagStrategy(engine=engine, settings=settings),
            LimitUpShakeoutStrategy(engine=engine, settings=settings),
            UptrendLimitDownStrategy(engine=engine, settings=settings),
            RpsBreakoutStrategy(engine=engine, settings=settings),
            PrivatePlacementStrategy(engine=engine, settings=settings),
            # 新增 RSI超跌反转策略实例
            RsiOversoldStrategy(engine=engine, settings=settings),
        ]
        notifier = FeishuNotifier(settings)
        # ========== 新增：收集所有策略结果 ==========
        all_strategy_results = {}
        # 5. 遍历策略，有结果则推送至对应机器人，增加异常捕获保护
        for strategy in strategies:
            strategy_name = type(strategy).__name__
            logger.info(f"执行策略：{strategy_name}")
            try:
                selected: list[str] = strategy.run()
                logger.info(f"{strategy_name} 选出 {len(selected)} 只股票")
                # 保存结果用于后续重合计算
                all_strategy_results[strategy_name] = selected
                if selected:
                    notifier.send(
                        symbols=selected,
                        strategy_name=strategy_name,
                        webhook_key=strategy.webhook_key,
                    )
                else:
                    logger.info(f"{strategy_name} 无选股结果，跳过推送")
            except Exception as e:
                logger.exception(f"策略 {strategy_name} 执行异常，跳过该策略")
                all_strategy_results[strategy_name] = []
        # ========== 修复：计算多策略重合（出现在 >=2 个策略即算重合） ==========
        # 原代码 set.intersection(*stock_sets) 求的是"同时被所有策略选中"，
        # 对 5 个集合求交集几乎恒为空集，导致汇总永远报 0。
        # 正确口径：一只股票只要被 2 个及以上策略选中，就算重合。
        stock_counter = Counter()
        for codes in all_strategy_results.values():
            for code in set(codes):          # 同一策略内先去重，避免重复计数
                stock_counter[code] += 1
        overlap_stocks = sorted(
            code for code, cnt in stock_counter.items() if cnt >= 2
        )
        logger.info(f"多策略重合股票共 {len(overlap_stocks)} 只: {overlap_stocks}")
        # ========== 新增：生成东方财富导入文件 ==========
        if overlap_stocks:
            generate_eastmoney_file(overlap_stocks)
            logger.info("东方财富导入文件已生成: eastmoney_import.txt")
        # ========== 新增：统一推送汇总结果 ==========
        # 从环境变量读取汇总用的飞书webhook（GitHub Secrets注入）
        summary_webhook = os.environ.get("FEISHU_SUMMARY_WEBHOOK", "")
        if summary_webhook:
            push_ok = send_summary_to_feishu(all_strategy_results, overlap_stocks, summary_webhook)
            logger.info(f"汇总消息推送结果: {push_ok}")
    except Exception:
        try:
            _logger = get_logger(__name__)
            _logger.exception("主流程发生未捕获异常，程序终止")
        except Exception:
            import traceback
            traceback.print_exc()
        sys.exit(1)
    logger.info("Sequoia-X V2 运行完成")

if __name__ == "__main__":
    main()
