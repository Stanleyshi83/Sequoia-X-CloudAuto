"""
Sequoia-X V2 main
基于原版可运行main.py，仅增加：共振统计、跨组/终极共振汇总、飞书汇总推送
策略实例化保持原生参数：(engine, settings)
优化项：
1. 全局一次性加载全市场股票列表，所有策略共用，减少重复网络请求
2. 全局requests连接池复用，减少连接开销，解决运行越跑越慢
3. 恢复单策略执行完成后独立飞书推送（每条策略单独发消息）
4. 保留原有全部共振汇总逻辑，最后再推送汇总消息
"""
import argparse
import sys
import os
import requests
from requests.adapters import HTTPAdapter
from collections import defaultdict
from itertools import combinations
from datetime import date
from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.base import BaseStrategy

# 策略导入（顺序保证：高股息动量在前，保证缓存导入正常）
from sequoia_x.strategy.dividend_momentum import DividendMomentumStrategy
from sequoia_x.strategy.high_dividend_stable import HighDividendStableStrategy
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

logger = get_logger(__name__)

# ---------------------- 全局连接池（实现请求复用，解决越跑越慢） ----------------------
# 全局唯一Session，所有飞书请求共用TCP连接
global_http_session = requests.Session()
# 扩大连接池上限
adapter = HTTPAdapter(
    pool_connections=20,
    pool_maxsize=20,
    pool_block=True
)
global_http_session.mount("https://", adapter)
global_http_session.mount("http://", adapter)

# ---------------------- 飞书消息通用发送函数（复用全局连接池） ----------------------
def send_feishu_text(content: str, webhook_key: str) -> bool:
    if not webhook_key or webhook_key.strip() == "":
        logger.warning(f"webhook_key为空，跳过本条推送")
        return False
    url = f"https://open.feishu.cn/open-apis/bot/v2/hook/{webhook_key.strip()}"
    payload = {
        "msg_type": "text",
        "content": {"text": content}
    }
    try:
        # 使用全局session，复用连接池
        resp = global_http_session.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info(f"单策略飞书推送成功 | key={webhook_key[:12]}...")
        return True
    except Exception as e:
        logger.error(f"单策略飞书推送失败: {str(e)}")
        return False

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
    content += f"💎【🌟技术策略终极共振】共{len(all_tech_intersection)}只：\n"
    if all_tech_intersection:
        content += "、".join(sorted(all_tech_intersection)) + "\n\n"
    else:
        content += "暂无共振标的\n\n"

    # 2.跨组共振
    content += f"🔥【跨分组共振标的】共{len(cross_group_resonance)}只：\n"
    if cross_group_resonance:
        content += "、".join(sorted(cross_group_resonance)) + "\n\n"
    else:
        content += "暂无跨组共振\n\n"

    # 3.分组内重合
    content += "📌【分组内部重合标的】\n"
    for g_en, g_cn in group_cn_name.items():
        codes = group_inner_overlap.get(g_en, [])
        if codes:
            content += f"{g_cn}：{len(codes)}只 → " + "、".join(sorted(codes)) + "\n"

    # 4.各策略选出数量统计
    content += "\n📊【各策略选股数量】\n"
    for strat_name, code_list in all_results.items():
        content += f"{strat_name}：{len(code_list)}只\n"

    try:
        resp = global_http_session.post(
            f"https://open.feishu.cn/open-apis/bot/v2/hook/{webhook.strip()}",
            json={"msg_type": "text", "content": {"text": content}},
            timeout=20
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"汇总消息推送失败：{e}")
        return False

def main():
    settings = get_settings()
    engine = DataEngine(settings)

    # ====================== 优化点1：全局只加载一次全市场股票列表 ======================
    global_stock_codes = engine.get_all_symbols()
    logger.info(f"全局加载全市场股票共 {len(global_stock_codes)} 只，所有策略共用此列表")

    # 策略实例列表（顺序：高股息动量在前，保证缓存先初始化）
    strategy_list = [
        DividendMomentumStrategy(engine, settings),
        HighDividendStableStrategy(engine, settings),
        HighTightFlagStrategy(engine, settings),
        LimitUpShakeoutStrategy(engine, settings),
        MaVolumeStrategy(engine, settings),
        TurtleTradeStrategy(engine, settings),
        UptrendLimitDownStrategy(engine, settings),
        RpsBreakoutStrategy(engine, settings),
        PrivatePlacementStrategy(engine, settings),
        RsiOversoldStrategy(engine, settings),
        BollingerVolatilityStrategy(engine, settings),
        VolumePriceDivergenceStrategy(engine, settings),
        BoxBreakoutStrategy(engine, settings),
    ]

    # 策略-分组映射
    strategy_group_map = {ins.__class__.__name__: ins.group for ins in strategy_list}
    all_strategy_results = dict()

    # 逐个执行策略 + 单策略独立推送
    today_str = date.today().strftime("%Y-%m-%d")
    for strat_ins in strategy_list:
        strat_name = strat_ins.__class__.__name__
        logger.info(f"===== 开始执行策略：{strat_name} =====")
        try:
            # ====================== 关键修改：传入全局股票列表 ======================
            selected_codes = strat_ins.run(global_stock_codes)
            all_strategy_results[strat_name] = selected_codes
            logger.info(f"{strat_name} 执行完成，选出 {len(selected_codes)} 只标的")

            # 组装单策略推送消息
            if selected_codes:
                code_lines = [f"{code}" for code in sorted(selected_codes)]
                code_text = "\n".join(code_lines)
            else:
                code_text = "无符合条件标的"

            single_msg = f"""【{strat_name}】
日期：{today_str}
策略分组：{strat_ins.group}
选股数量：{len(selected_codes)}
标的清单：
{code_text}
"""
            # 独立飞书推送
            send_feishu_text(single_msg, strat_ins.webhook_key)

        except Exception as exc:
            logger.exception(f"策略 {strat_name} 执行异常终止")
            all_strategy_results[strat_name] = []

    # ========== 下面所有共振统计逻辑完全保留原版，一行不动 ==========
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

    # 飞书汇总推送
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

    logger.info("Sequoia-X V2 全部策略运行完成")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        _logger = get_logger(__name__)
        _logger.exception("主流程发生未捕获异常，程序终止")
        sys.exit(1)
