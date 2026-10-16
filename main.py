"""
Sequoia-X V2 主程序入口（GitHub Actions适配版）
两种运行模式：
  python main.py               # 日常模式：跑策略 + 飞书推送
  python main.py --backfill    # 预留回填参数（暂不使用，保持兼容）
新增功能：
1. 自动收集所有策略选股结果
2. 计算多策略重合股票
3. 按策略分组展示结果，区分【同组多策略选中】vs【跨不同组别同时选中（跨组共振高亮）】
4. 新增：【10个技术策略同时命中 终极共振】标的展示，排除事件定增策略
5. 统一推送汇总结果到飞书
6. 策略两两共现热力统计日志
7. 按分组拆分、列出组内重合标的，飞书消息内分组展示
"""
import argparse
import sys
import os
import json
import requests
from collections import Counter, defaultdict
from itertools import combinations
from dotenv import load_dotenv
load_dotenv()
from datetime import date
import socket
socket.setdefaulttimeout(10.0)
from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine
from sequoia_x.notify.feishu import FeishuNotifier
from sequoia_x.strategy.base import BaseStrategy
# ========== 原有11个策略导入 ==========
from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
from sequoia_x.strategy.private_placement import PrivatePlacementStrategy
from sequoia_x.strategy.RsiOversoldStrategy import RsiOversoldStrategy
from sequoia_x.strategy.bollinger_volatility import BollingerVolatilityStrategy
from sequoia_x.strategy.volume_price_divergence import VolumePriceDivergenceStrategy
from sequoia_x.strategy.box_breakout import BoxBreakoutStrategy
# ========== 新增2条高股息策略导入 ==========
from sequoia_x.strategy.high_dividend_stable import HighDividendStableStrategy
from sequoia_x.strategy.dividend_momentum import DividendMomentumStrategy


def send_summary_to_feishu(all_results: dict,
                            overlap: list,
                            cross_group_resonance: list,
                            same_group_overlap: list,
                            group_inner_overlap: dict,
                            all_tech_intersection: list,
                            strategy_group_map: dict,
                            stock_hit_strategies: dict,
                            webhook: str) -> bool:
    """推送汇总结果，新版渲染：删除原版全局多策略重合，保留终极共振+跨组共振+分组组内重合"""
    date_str = date.today().strftime("%Y-%m-%d")
    # 分组中文映射
    group_cn_name = {
        "bottom_reversal": "底部反转组",
        "volatility": "波动率蓄势组",
        "momentum_break": "动量突破组",
        "strong_shakeout": "强势股洗盘组",
        "event": "事件选股组",
        "value_defense": "价值防御组"
    }
    content = f"【选股汇总】 {date_str}\n\n"

    # ---------------------- 1. 终极共振（10个技术策略，剔除事件策略） ----------------------
    content += f"💎【🌟10个技术策略同时选中｜终极共振，极高优先级（排除事件定增策略）】共{len(all_tech_intersection)}只：\n"
    if all_tech_intersection:
        content += "标的清单：" + ",".join(sorted(all_tech_intersection)) + "\n标的明细：\n"
        for code in sorted(all_tech_intersection):
            hit_text = "、".join(stock_hit_strategies[code])
            content += f"{code}｜命中策略：{hit_text}\n"
    else:
        content += "今日无标的同时满足全部10个技术策略条件\n"
    content += "\n"

    # ---------------------- 2. 跨组共振 ----------------------
    content += f"💎【🌟跨组共振｜跨不同策略组别同时选出，高优先级】共{len(cross_group_resonance)}只\n"
    if cross_group_resonance:
        content += "标的清单：" + ",".join(sorted(cross_group_resonance)) + "\n标的明细：\n"
        for code in sorted(cross_group_resonance):
            hit_text = "、".join(stock_hit_strategies[code])
            content += f"{code}｜命中策略：{hit_text}\n"
    else:
        content += "标的清单：无\n标的明细：无\n"
    content += "\n---\n\n"

    # ---------------------- 3. 逐分组渲染 ----------------------
    group_order = ["bottom_reversal", "volatility", "momentum_break", "strong_shakeout", "event", "value_defense"]
    for group_key in group_order:
        group_name_cn = group_cn_name[group_key]
        content += f"📂【{group_name_cn}】\n"
        strat_list_in_group = [s for s, g in strategy_group_map.items() if g == group_key]
        for strat_name in strat_list_in_group:
            cnt = len(all_results.get(strat_name, []))
            content += f" 📌 {strat_name}：{cnt}只\n"
        inner_codes = group_inner_overlap.get(group_key, [])
        content += f" ✨【本组内多策略重合标的｜>=2策略同时选中】共{len(inner_codes)}只\n"
        if inner_codes:
            content += "标的清单：" + ",".join(sorted(inner_codes)) + "\n标的明细：\n"
            for code in sorted(inner_codes):
                hit_text = "、".join(stock_hit_strategies[code])
                content += f"{code}｜命中策略：{hit_text}\n"
        else:
            content += "标的清单：无\n标的明细：无\n"
        content += "\n"

    payload = {
        "msg_type": "text",
        "content": {
            "text": content
        }
    }
    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"飞书推送失败：{e}")
        return False


def main():
    logger = get_logger(__name__)
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--backfill", action="store_true", help="全量回填历史K线（预留，暂未实现）")
        args = parser.parse_args()

        # 初始化引擎，不调用额外update/backfill方法
        settings = get_settings()
        engine = DataEngine(settings=settings)

        # ========== 策略分组配置 ==========
        strategy_group_map = {
            "RsiOversoldStrategy": "bottom_reversal",
            "UptrendLimitDownStrategy": "bottom_reversal",
            "VolumePriceDivergenceStrategy": "bottom_reversal",
            "BollingerVolatilityStrategy": "volatility",
            "BoxBreakoutStrategy": "momentum_break",
            "HighTightFlagStrategy": "momentum_break",
            "MaVolumeStrategy": "momentum_break",
            "RpsBreakoutStrategy": "momentum_break",
            "TurtleTradeStrategy": "momentum_break",
            "LimitUpShakeoutStrategy": "strong_shakeout",
            "PrivatePlacementStrategy": "event",
            "HighDividendStableStrategy": "value_defense",
            "DividendMomentumStrategy": "value_defense"
        }

        strategy_cls_list = [
            RsiOversoldStrategy,
            UptrendLimitDownStrategy,
            VolumePriceDivergenceStrategy,
            BollingerVolatilityStrategy,
            BoxBreakoutStrategy,
            HighTightFlagStrategy,
            MaVolumeStrategy,
            RpsBreakoutStrategy,
            TurtleTradeStrategy,
            LimitUpShakeoutStrategy,
            PrivatePlacementStrategy,
            HighDividendStableStrategy,
            DividendMomentumStrategy
        ]

        # 批量运行全部策略
        all_strategy_results = {}
        for strat_cls in strategy_cls_list:
            strat_ins: BaseStrategy = strat_cls(engine)
            stock_codes = strat_ins.run()
            all_strategy_results[strat_cls.__name__] = stock_codes
            logger.info(f"{strat_cls.__name__} 选出 {len(stock_codes)} 只标的")

        # 统计每只股票命中策略、分组
        stock_hit_strategies = defaultdict(list)
        stock_hit_groups = defaultdict(set)
        for strat_name, code_list in all_strategy_results.items():
            group = strategy_group_map[strat_name]
            for code in code_list:
                stock_hit_strategies[code].append(strat_name)
                stock_hit_groups[code].add(group)

        overlap_stocks = [code for code, strat_list in stock_hit_strategies.items() if len(strat_list) >= 2]
        logger.info(f"多策略重合股票共 {len(overlap_stocks)} 只: {overlap_stocks}")

        cross_group_resonance = []
        same_group_overlap = []
        for stock_code, group_set in stock_hit_groups.items():
            hit_strats = stock_hit_strategies[stock_code]
            if len(hit_strats) >= 2:
                if len(group_set) >= 2:
                    cross_group_resonance.append(stock_code)
                else:
                    same_group_overlap.append(stock_code)
        logger.info(f"【跨组共振标的】共{len(cross_group_resonance)}只：{cross_group_resonance}")
        logger.info(f"【同组多策略重合标的】共{len(same_group_overlap)}只：{same_group_overlap}")

        # 策略两两共现热力统计日志
        stock_to_strats = defaultdict(set)
        for strat_name, codes in all_strategy_results.items():
            for code in set(codes):
                stock_to_strats[code].add(strat_name)
        pair_count = defaultdict(int)
        for _, strat_set in stock_to_strats.items():
            strat_list = sorted(strat_set)
            for s1, s2 in combinations(strat_list, 2):
                pair_count[(s1, s2)] += 1
        logger.info("==== 策略两两共同命中统计（热力原始数据）====")
        for (s1, s2), cnt in sorted(pair_count.items(), key=lambda x: x[1], reverse=True):
            logger.info(f"{s1} <--> {s2} 共同选中数量：{cnt}")
        logger.info("============================================")

        # 拆分各分组内部重合标的
        group_inner_overlap = defaultdict(list)
        for stock_code, group_set in stock_hit_groups.items():
            hit_strats = stock_hit_strategies[stock_code]
            if len(hit_strats) >= 2 and len(group_set) == 1:
                g = list(group_set)[0]
                group_inner_overlap[g].append(stock_code)
        logger.info("==== 各分组内部多策略重合标的 ====")
        for g_name, code_list in group_inner_overlap.items():
            logger.info(f"分组【{g_name}】组内重合标的({len(code_list)}只): {sorted(code_list)}")
        logger.info("============================================")

        # 终极共振：排除事件定增和高股息策略
        all_tech_strategy_sets = []
        for strat_name, codes in all_strategy_results.items():
            if strat_name in ("PrivatePlacementStrategy", "HighDividendStableStrategy", "DividendMomentumStrategy"):
                continue
            all_tech_strategy_sets.append(set(codes))
        if all_tech_strategy_sets:
            all_tech_intersection = list(set.intersection(*all_tech_strategy_sets))
        else:
            all_tech_intersection = []
        logger.info(f"【全部技术策略同时命中标的｜排除事件策略】共{len(all_tech_intersection)}只：{all_tech_intersection}")

        # 飞书推送汇总
        summary_webhook = os.environ.get("FEISHU_SUMMARY_WEBHOOK", "")
        if summary_webhook:
            push_ok = send_summary_to_feishu(
                all_strategy_results,
                overlap_stocks,
                cross_group_resonance,
                same_group_overlap,
                group_inner_overlap,
                all_tech_intersection,
                strategy_group_map,
                stock_hit_strategies,
                summary_webhook
            )
            logger.info(f"汇总消息推送结果: {push_ok}")
        logger.info("Sequoia-X V2 运行完成")
    except Exception:
        try:
            _logger = get_logger(__name__)
            _logger.exception("主流程发生未捕获异常，程序终止")
        except Exception:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
