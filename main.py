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
global_http_session = requests.Session()
adapter = HTTPAdapter(
    pool_connections=20,
    pool_maxsize=20,
    pool_block=True
)
global_http_session.mount("https://", adapter)
global_http_session.mount("http://", adapter)

# ---------------------- 工具函数：自动处理webhook，避免URL重复拼接导致404 ----------------------
def _resolve_webhook_url(webhook_input: str) -> str:
    """自动识别：如果是完整URL直接返回；如果只是token，自动拼接飞书前缀"""
    if not webhook_input:
        return ""
    webhook_input = webhook_input.strip()
    if webhook_input.startswith(("http://", "https://")):
        return webhook_input
    return f"https://open.feishu.cn/open-apis/bot/v2/hook/{webhook_input}"

# ---------------------- 飞书单策略推送（增加业务码校验，杜绝假成功） ----------------------
def send_feishu_text(content: str, webhook_key: str) -> bool:
    if not webhook_key or webhook_key.strip() == "":
        logger.warning(f"webhook_key为空，跳过本条推送")
        return False

    # 优先读取策略专属环境变量，没有则降级到默认机器人
    env_key_map = {
        "dividend_momentum": "FEISHU_WEBHOOK_DIVIDEND_MOMENTUM",
        "high_dividend": "FEISHU_WEBHOOK_HIGH_DIVIDEND",
        "htf": "FEISHU_WEBHOOK_HTF",
        "shakeout": "FEISHU_WEBHOOK_SHAKEOUT",
        "ma_volume": "FEISHU_WEBHOOK_MA_VOLUME",
        "turtle": "FEISHU_WEBHOOK_TURTLE_TRADE",
        "limit_down": "FEISHU_WEBHOOK_LIMIT_DOWN",
        "rps": "FEISHU_WEBHOOK_RPS",
        "private_placement": "FEISHU_WEBHOOK_PRIVATE_PLACEMENT",
        "rsi_oversold": "FEISHU_WEBHOOK_RSI_OVERSOLD",
        "bollinger_vol": "FEISHU_WEBHOOK_BOLLINGER_VOL",
        "vol_price_div": "FEISHU_WEBHOOK_VOL_PRICE_DIV",
        "box_breakout": "FEISHU_WEBHOOK_BOX_BREAKOUT",
    }
    env_name = env_key_map.get(webhook_key, "FEISHU_WEBHOOK_URL")
    webhook_raw = os.environ.get(env_name, "")
    if not webhook_raw:
        webhook_raw = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if not webhook_raw:
        logger.warning(f"[{webhook_key}] 未配置飞书webhook，跳过推送")
        return False

    # 解析最终url，防止重复拼接
    url = _resolve_webhook_url(webhook_raw)
    payload = {
        "msg_type": "text",
        "content": {"text": content}
    }
    try:
        resp = global_http_session.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        # 校验飞书业务返回码，防止HTTP 200但消息发送失败
        res_json = resp.json()
        if res_json.get("code", 0) != 0:
            logger.error(f"[{webhook_key}] 飞书推送业务失败：code={res_json.get('code')}, msg={res_json.get('msg')}")
            return False
        logger.info(f"单策略飞书推送成功 | key={webhook_key[:12]}...")
        return True
    except Exception as e:
        logger.error(f"单策略飞书推送失败: {str(e)}")
        return False

# ---------------------- 汇总消息推送（修复404，不再硬拼URL前缀） ----------------------
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
    # 3. 各组 + 组内多策略重合
    group_order = ["bottom_reversal", "volatility", "momentum_break", "strong_shakeout", "event", "value_defense"]
    for group_key in group_order:
        group_name = group_cn_name[group_key]
        content += f"📂【{group_name}】\n"
        for strat_name, g in strategy_group_map.items():
            if g == group_key:
                cnt = len(all_results.get(strat_name, []))
                content += f" 📌 {strat_name}：{cnt}只\n"
        overlap_list = group_inner_overlap.get(group_key, [])
        content += f" ✨【本组内多策略重合标的｜>=2策略同时选中】共{len(overlap_list)}只\n"
        if overlap_list:
            content += "标的清单：" + ",".join(sorted(overlap_list)) + "\n标的明细：\n"
            for code in sorted(overlap_list):
                hit_text = "、".join(stock_hit_strategies[code])
                content += f"{code}｜命中策略：{hit_text}\n"
        else:
            content += "标的清单：无\n标的明细：无\n"
        content += "\n"

    # 核心修复：自动解析url，不会重复拼接前缀导致404
    summary_url = _resolve_webhook_url(webhook.strip())
    payload = {
        "msg_type": "text",
        "content": {"text": content}
    }
    try:
        resp = global_http_session.post(summary_url, json=payload, timeout=20)
        resp.raise_for_status()
        res_json = resp.json()
        if res_json.get("code", 0) != 0:
            logger.error(f"汇总消息飞书业务错误: {res_json}")
            return False
        logger.info("选股汇总消息推送成功")
        return True
    except Exception as e:
        logger.error(f"汇总消息推送失败: {str(e)}")
        return False

def main():
    settings = get_settings()
    engine = DataEngine(settings.db_path)
    engine.init_db()

    # 全局一次性加载全市场股票列表，所有策略共用
    logger.info("全局一次性加载全市场股票列表，所有策略复用，避免重复请求")
    all_stock_df = engine.get_stock_list()
    global_stock_codes = all_stock_df["code"].tolist()

    # 注入akshare全局session（保持原有逻辑不变）
    try:
        import akshare
        akshare.set_session(global_http_session)
    except Exception:
        logger.warning("当前akshare版本不支持set_session，跳过连接池注入")

    # 策略分组映射（原样保留）
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
        DividendMomentumStrategy,
        HighDividendStableStrategy,
        HighTightFlagStrategy,
        LimitUpShakeoutStrategy,
        MaVolumeStrategy,
        TurtleTradeStrategy,
        UptrendLimitDownStrategy,
        RsiBreakoutStrategy,
        PrivatePlacementStrategy,
        RsiOversoldStrategy,
        BollingerVolatilityStrategy,
        VolumePriceDivergenceStrategy,
        BoxBreakoutStrategy
    ]

    all_strategy_results = {}
    today_str = date.today().strftime("%Y-%m-%d")

    # 循环执行策略 + 逐条独立推送（原有逻辑不变）
    for strat_cls in strategy_cls_list:
        strat_ins: BaseStrategy = strat_cls(engine, settings)
        selected_codes = strat_ins.run(global_stock_codes)
        strat_name = strat_cls.__name__
        all_strategy_results[strat_name] = selected_codes
        logger.info(f"{strat_name} 选出 {len(selected_codes)} 只标的")

        # 拼装单策略消息
        if selected_codes:
            code_lines = []
            for code in selected_codes:
                if code.startswith("6"):
                    code_lines.append(f"SH{code}")
                else:
                    code_lines.append(f"SZ{code}")
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

    # 终极共振：自动排除事件定增 + 两个高股息策略
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
