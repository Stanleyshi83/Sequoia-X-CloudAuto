"""
Sequoia-X V2 main
基于原版可运行main.py，仅增加：共振统计、跨组/终极共振汇总、飞书汇总推送
策略实例化保持原生参数：(engine, settings)
"""
import argparse
import sys
import os
import requests
from collections import defaultdict
from itertools import combinations
from datetime import date

from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.base import BaseStrategy

# 策略导入（和你原本正常运行版本保持一致，新增两条高股息策略）
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
# 新增两个高股息策略
from sequoia_x.strategy.high_dividend_stable import HighDividendStableStrategy
from sequoia_x.strategy.dividend_momentum import DividendMomentumStrategy


def send_summary_to_feishu(all_results: dict,
                            cross_group_resonance: list,
                            group_inner_overlap: dict,
                            all_tech_intersection: list,
                            strategy_group_map: dict,
                            stock_hit_strategies: dict,
                            webhook: str) -> bool:
    date_str = date.today().strftime("%Y-%m-%d")
    group_cn_name = {
        "bottom_reversal": "底部反转组",
        "volatility": "波动率蓄势组",
        "momentum_break": "动量突破组",
        "strong_shakeout": "强势股洗盘组",
        "event": "事件选股组",
        "value_defense": "价值防御组"
    }
    content = f"【选股汇总】 {date_str}\n\n"

    # 1. 终极共振（排除事件、高股息策略）
    content += f"💎【🌟10个技术策略同时选中｜终极共振，极高优先级（排除事件定增策略）】共{len(all_tech_intersection)}只：\n"
    if all_tech_intersection:
        content += "标的清单：" + ",".join(sorted(all_tech_intersection)) + "\n标的明细：\n"
        for code in sorted(all_tech_intersection):
            hit_text = "、".join(stock_hit_strategies[code])
            content += f"{code}｜命中策略：{hit_text}\n"
    else:
        content += "今日无标的同时满足全部10个技术策略条件\n"
    content += "\n"

    # 2. 跨组共振
    content += f"💎【🌟跨组共振｜跨不同策略组别同时选出，高优先级】共{len(cross_group_resonance)}只\n"
    if cross_group_resonance:
        content += "标的清单：" + ",".join(sorted(cross_group_resonance)) + "\n标的明细：\n"
        for code in sorted(cross_group_resonance):
            hit_text = "、".join(stock_hit_strategies[code])
            content += f"{code}｜命中策略：{hit_text}\n"
    else:
        content += "标的清单：无\n标的明细：无\n"
    content += "\n---\n\n"

    # 3. 各组 + 组内多策略重合（补齐了你之前截断的代码）
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
        logger.error(f"飞书汇总推送失败：{e}")
        return False


def main():
    logger = get_logger(__name__)
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--backfill", action="store_true")
        args = parser.parse_args()

        # 原版初始化，无改动
        settings = get_settings()
        engine = DataEngine(settings)

        # 策略分组映射：新增2个高股息策略划入value_defense
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
            # 新增两行
            "HighDividendStableStrategy": "value_defense",
            "DividendMomentumStrategy": "value_defense"
        }

        # 策略列表：追加两个高股息策略
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

        all_strategy_results = {}
        # ✅ 严格保持原生实例化：strat_cls(engine, settings)，不会再报BaseStrategy参数缺失
        for strat_cls in strategy_cls_list:
            strat_ins: BaseStrategy = strat_cls(engine, settings)
            stock_codes = strat_ins.run()
            all_strategy_results[strat_cls.__name__] = stock_codes
            logger.info(f"{strat_cls.__name__} 选出 {len(stock_codes)} 只标的")

        # ========== 共振统计逻辑（完全沿用，无修改） ==========
        stock_hit_strategies = defaultdict(list)
        stock_hit_groups = defaultdict(set)
        for strat_name, code_list in all_strategy_results.items():
            group = strategy_group_map[strat_name]
            for code in code_list:
                stock_hit_strategies[code].append(strat_name)
                stock_hit_groups[code].add(group)

        cross_group_resonance = []
        group_inner_overlap = defaultdict(list)
        for stock_code, group_set in stock_hit_groups.items():
            hit_strats = stock_hit_strategies[stock_code]
            if len(hit_strats) >= 2:
                if len(group_set) >= 2:
                    cross_group_resonance.append(stock_code)
                else:
                    g = list(group_set)[0]
                    group_inner_overlap[g].append(stock_code)

        logger.info(f"【跨组共振标的】共{len(cross_group_resonance)}只：{cross_group_resonance}")
        for g_name, code_list in group_inner_overlap.items():
            logger.info(f"分组【{g_name}】组内重合标的({len(code_list)}只): {sorted(code_list)}")

        # 策略两两共现热力日志
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

        # 终极共振：自动排除事件定增 + 两个高股息策略，只计算技术策略交集
        skip_strats = {"PrivatePlacementStrategy", "HighDividendStableStrategy", "DividendMomentumStrategy"}
        all_tech_strategy_sets = []
        for strat_name, codes in all_strategy_results.items():
            if strat_name not in skip_strats:
                all_tech_strategy_sets.append(set(codes))
        all_tech_intersection = list(set.intersection(*all_tech_strategy_sets)) if all_tech_strategy_sets else []
        logger.info(f"【全部技术策略同时命中标的｜排除事件策略】共{len(all_tech_intersection)}只：{all_tech_intersection}")

        # 飞书汇总推送（不影响每个策略自带的独立飞书推送）
        summary_webhook = os.environ.get("FEISHU_SUMMARY_WEBHOOK", "")
        if summary_webhook:
            push_ok = send_summary_to_feishu(
                all_strategy_results,
                cross_group_resonance,
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
