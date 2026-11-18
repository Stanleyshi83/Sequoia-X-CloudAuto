# Sequoia-X V2 main
# 最小改动：新增5策略族跨族共振口径，旧逻辑完整保留
import sys
import os
import time
import requests
from collections import defaultdict
from itertools import combinations
from datetime import date
from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.base import BaseStrategy
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
from sequoia_x.strategy.high_dividend_stable import HighDividendStableStrategy
from sequoia_x.strategy.dividend_momentum import DividendMomentumStrategy

# ========== 防超时全局环境变量（无需改策略代码）==========
os.environ["TQDM_DISABLE"] = "1"          # 关闭进度条，减少日志IO
os.environ["AKSHARE_HTTP_TIMEOUT"] = "15" # akshare请求15秒超时，杜绝卡死
logger = get_logger(__name__)

# ---------------------- 参数配置区（新增，方便调参，不硬编码） ----------------------
# 跨族共振、乖离、涨幅过滤阈值
RESONANCE_MIN_FAMILY = 2
BIAS20_THRESHOLD = 5.0
RET20_THRESHOLD = 10.0
HOT_BIAS_THRESHOLD = 10.0
MIN_TURNOVER_AMT = 50000000  # 5000万最低成交额过滤
# 市值分界，单位亿
CAP_LARGE = 500
CAP_MID = 100

# ---------------------- 飞书发送工具 ----------------------
def _resolve_webhook_url(webhook_input: str) -> str:
    """自动识别：完整URL直接用，纯key自动拼前缀。"""
    if not webhook_input:
        return ""
    webhook_input = webhook_input.strip()
    if webhook_input.startswith(("http://", "https://")):
        return webhook_input
    return f"https://open.feishu.cn/open-apis/bot/v2/hook/{webhook_input}"

def _post_feishu(content: str, webhook_raw: str, tag: str = "") -> bool:
    """统一发送飞书text消息，校验业务返回码。"""
    url = _resolve_webhook_url(webhook_raw)
    if not url:
        logger.warning(f"[{tag}] webhook为空，跳过推送")
        return False
    payload = {"msg_type": "text", "content": {"text": content}}
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        res = resp.json()
        if res.get("code", 0) != 0:
            logger.error(f"[{tag}] 飞书业务错误: {res}")
            return False
        logger.info(f"[{tag}] 推送成功")
        return True
    except Exception as e:
        logger.error(f"[{tag}] 推送失败: {e}")
        return False

# 策略webhook_key → 环境变量名映射；没配的自动fallback到 FEISHU_WEBHOOK_URL
WEBHOOK_ENV_MAP = {
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

def send_single_strategy_msg(content: str, webhook_key: str) -> bool:
    env_name = WEBHOOK_ENV_MAP.get(webhook_key, "FEISHU_WEBHOOK_URL")
    webhook_raw = os.environ.get(env_name, "") or os.environ.get("FEISHU_WEBHOOK_URL", "")
    return _post_feishu(content, webhook_raw, tag=webhook_key)

# ========== 【改动点1】增加新入参 new_cross_family_resonance ==========
def send_summary_to_feishu(all_results: dict,
                            cross_group_resonance: list,
                            group_inner_overlap: dict,
                            all_tech_intersection: list,
                            strategy_group_map: dict,
                            stock_hit_strategies: dict,
                            stock_meta: dict,
                            new_cross_family_resonance: list,
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

    # ==========【改动点2】顶部精选池切换为新族共振数据源 new_cross_family_resonance ==========
    qualified_pool = []
    hot_watch_pool = []
    # 新口径跨族共振池替换原有cross_group_resonance
    for code in new_cross_family_resonance:
        meta = stock_meta.get(code, {})
        qualified_pool.append((code, meta))
    for g_code_list in group_inner_overlap.values():
        for code in g_code_list:
            meta = stock_meta.get(code, {})
            hot_watch_pool.append((code, meta))

    content += "==== ✅【跨族共振精选候选池｜满足低乖离过滤，等待回踩MA10/MA20，T+10观察窗口】====\n"
    qualified_codes = [c for c,m in qualified_pool if m.get("qualified", False)]
    if qualified_codes:
        for code in sorted(qualified_codes):
            m = stock_meta.get(code, {})
            bias20 = m.get("bias20", "N/A")
            ret20 = m.get("ret20", "N/A")
            cap_group = m.get("cap_group", "未知市值")
            strat_list = stock_hit_strategies[code]
            content += f"{code}｜乖离{bias20:.2f}%｜20日涨幅{ret20:.2f}%｜{cap_group}｜命中：{'、'.join(strat_list)}\n"
    else:
        content += "今日无符合低乖离跨族共振标的\n"

    content += "\n==== ⚠️【过热观察池｜同族扎堆 / 高乖离，仅跟踪，不直接买入】====\n"
    hot_codes = [c for c,m in hot_watch_pool if m.get("hot", True)]
    if hot_codes:
        for code in sorted(set(hot_codes)):
            m = stock_meta.get(code, {})
            bias20 = m.get("bias20", "N/A")
            ret20 = m.get("ret20", "N/A")
            cap_group = m.get("cap_group", "未知市值")
            strat_list = stock_hit_strategies[code]
            content += f"{code}｜乖离{bias20:.2f}%｜20日涨幅{ret20:.2f}%｜{cap_group}｜命中：{'、'.join(strat_list)}\n"
    else:
        content += "今日无过热标的\n"
    content += "\n--- 原有汇总信息（旧分组口径） ---\n\n"

    # ========== 下方全部旧汇总内容完全原样保留，无任何修改 ==========
    content += f"💎【🌟10个技术策略同时选中｜终极共振，极高优先级（排除事件定增策略）】共{len(all_tech_intersection)}只：\n"
    if all_tech_intersection:
        content += "标的清单：" + ",".join(sorted(all_tech_intersection)) + "\n标的明细：\n"
        for code in sorted(all_tech_intersection):
            content += f"{code}｜命中策略：{'、'.join(stock_hit_strategies[code])}\n"
    else:
        content += "今日无标的同时满足全部10个技术策略条件\n"
    content += "\n"
    content += f"💎【🌟跨组共振｜旧业务分组口径】共{len(cross_group_resonance)}只\n"
    if cross_group_resonance:
        content += "标的清单：" + ",".join(sorted(cross_group_resonance)) + "\n标的明细：\n"
        for code in sorted(cross_group_resonance):
            content += f"{code}｜命中策略：{'、'.join(stock_hit_strategies[code])}\n"
    else:
        content += "标的清单：无\n标的明细：无\n"
    content += "\n---\n\n"
    group_order = ["bottom_reversal", "volatility", "momentum_break", "strong_shakeout", "event", "value_defense"]
    for group_key in group_order:
        content += f"📂【{group_cn_name[group_key]}】\n"
        for strat_name, g in strategy_group_map.items():
            if g == group_key:
                cnt = len(all_results.get(strat_name, []))
                content += f"  📌 {strat_name}：{cnt}只\n"
        overlap_list = group_inner_overlap.get(group_key, [])
        content += f"  ✨【本组内多策略重合标的｜>=2策略同时选中】共{len(overlap_list)}只\n"
        if overlap_list:
            content += "标的清单：" + ",".join(sorted(overlap_list)) + "\n标的明细：\n"
            for code in sorted(overlap_list):
                content += f"{code}｜命中策略：{'、'.join(stock_hit_strategies[code])}\n"
        else:
            content += "标的清单：无\n标的明细：无\n"
        content += "\n"
    return _post_feishu(content, webhook, tag="汇总")

def main():
    # ========== 策略白名单（调试用，空=全量执行）==========
    run_filter = os.environ.get("RUN_STRATEGY_FILTER", "").strip()
    filter_set = set(run_filter.split(",")) if run_filter else set()

    # ====== 严格沿用你原版初始化方式，不做改动 ======
    settings = get_settings()
    engine = DataEngine(settings)

    # =====================【关键修复：增量同步今日日线】=====================
    engine.sync_today_bulk()
    # ======================================================================

    # ====== 全局只加载一次股票列表，所有策略共用 ======
    global_stock_codes = engine.get_all_symbols()
    logger.info(f"全局加载全市场股票共 {len(global_stock_codes)} 只，所有策略共用")

    # 旧业务分组映射（完全保留，不作修改）
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

    # ==========【改动点3】新增独立5大策略族映射（和旧分组完全隔离） ==========
    strategy_family_map = {
        # 1.趋势突破族
        "BoxBreakoutStrategy": "trend_break",
        "HighTightFlagStrategy": "trend_break",
        "MaVolumeStrategy": "trend_break",
        "RpsBreakoutStrategy": "trend_break",
        "TurtleTradeStrategy": "trend_break",
        # 2.均值回归族
        "RsiOversoldStrategy": "mean_revert",
        "BollingerVolatilityStrategy": "mean_revert",
        "VolumePriceDivergenceStrategy": "mean_revert",
        # 3.量价异动族
        "LimitUpShakeoutStrategy": "price_volume_shock",
        "UptrendLimitDownStrategy": "price_volume_shock",
        # 4.红利基本面族
        "HighDividendStableStrategy": "dividend_fundamental",
        "DividendMomentumStrategy": "dividend_fundamental",
        # 5.事件族
        "PrivatePlacementStrategy": "event"
    }

    strategy_cls_list = [
        DividendMomentumStrategy,
        HighDividendStableStrategy,
        HighTightFlagStrategy,
        LimitUpShakeoutStrategy,
        MaVolumeStrategy,
        TurtleTradeStrategy,
        UptrendLimitDownStrategy,
        RpsBreakoutStrategy,
        PrivatePlacementStrategy,
        RsiOversoldStrategy,
        BollingerVolatilityStrategy,
        VolumePriceDivergenceStrategy,
        BoxBreakoutStrategy,
    ]

    all_strategy_results = {}
    today_str = date.today().strftime("%Y-%m-%d")

    # ====== 逐个执行策略 + 计时 + 单策略独立推送 + 异常隔离 ======
    for strat_cls in strategy_cls_list:
        strat_ins: BaseStrategy = strat_cls(engine, settings)
        strat_name = strat_cls.__name__

        # 白名单过滤，跳过不在列表内的策略
        if filter_set and strat_ins.webhook_key not in filter_set:
            logger.info(f"跳过策略 {strat_name}（不在RUN_STRATEGY_FILTER白名单内）")
            continue

        logger.info(f"===== 开始执行策略：{strat_name} =====")
        strat_start = time.time()  # 记录策略开始时间

        try:
            # 把全局股票列表传入run()（策略已改造成接收一个参数）
            selected_codes = strat_ins.run(global_stock_codes)
            all_strategy_results[strat_name] = selected_codes
            logger.info(f"{strat_name} 选出 {len(selected_codes)} 只标的")

            # 组装单策略消息（SH/SZ前缀，和你截图格式一致）
            if selected_codes:
                code_lines = []
                for c in sorted(selected_codes):
                    code_lines.append(f"SH{c}" if c.startswith("6") else f"SZ{c}")
                code_text = " ".join(code_lines)
            else:
                code_text = "无标的"

            single_msg = f"""【{strat_name}】
日期：{today_str}
策略：{strat_name}
选股数量：{len(selected_codes)}
选股列表：
{code_text}
"""
            send_single_strategy_msg(single_msg, strat_ins.webhook_key)

        except Exception as exc:
            logger.exception(f"策略 {strat_name} 执行异常，继续执行后续策略")
            all_strategy_results[strat_name] = []
        finally:
            # 打印单策略耗时，定位慢策略
            cost_sec = round(time.time() - strat_start, 2)
            logger.info(f"策略 {strat_name} 执行总耗时：{cost_sec} 秒")

    # ==============================================
    # 【原有旧共振统计逻辑：完全原样保留，不修改一行】
    # ==============================================
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

    logger.info(f"【旧口径-跨组共振】共{len(cross_group_resonance)}只：{cross_group_resonance}")
    for g_name, code_list in group_inner_overlap.items():
        logger.info(f"分组【{g_name}】组内重合({len(code_list)}只): {sorted(code_list)}")

    stock_to_strats = defaultdict(set)
    for strat_name, codes in all_strategy_results.items():
        for code in set(codes):
            stock_to_strats[code].add(strat_name)

    pair_count = defaultdict(int)
    for _, strat_set in stock_to_strats.items():
        sl = sorted(strat_set)
        for s1, s2 in combinations(sl, 2):
            pair_count[(s1, s2)] += 1

    logger.info("==== 策略两两共同命中统计 ====")
    for (s1, s2), cnt in sorted(pair_count.items(), key=lambda x: x[1], reverse=True):
        logger.info(f"{s1} <--> {s2}: {cnt}")
    logger.info("==============================")

    skip_strats = {"PrivatePlacementStrategy", "HighDividendStableStrategy", "DividendMomentumStrategy"}
    tech_sets = [set(codes) for s, codes in all_strategy_results.items() if s not in skip_strats]
    all_tech_intersection = list(set.intersection(*tech_sets)) if tech_sets else []
    logger.info(f"【技术策略终极共振】共{len(all_tech_intersection)}只：{all_tech_intersection}")

    # ==============================================
    # 【改动点4：全新新增：5策略族共振统计（独立链路，不污染旧变量）】
    # 规则：同族最多算1票，共振分=跨策略族数量
    # ==============================================
    stock_hit_families = defaultdict(set)
    for strat_name, code_list in all_strategy_results.items():
        family = strategy_family_map[strat_name]
        for code in code_list:
            stock_hit_families[code].add(family)  # 同一个族只保留一次

    # 新口径跨族共振：命中>=2个不同策略族
    new_cross_family_resonance = [
        code for code, family_set in stock_hit_families.items()
        if len(family_set) >= RESONANCE_MIN_FAMILY
    ]
    logger.info(f"【新口径-跨策略族共振】共{len(new_cross_family_resonance)}只：{new_cross_family_resonance}")

    # ==========【本次新增：批量读取K线，计算乖离、20日涨幅、市值分层、过滤标记】 ==========
    all_candidate_codes = set()
    all_candidate_codes.update(new_cross_family_resonance)  # 候选池改用新共振标的
    for clist in group_inner_overlap.values():
        all_candidate_codes.update(clist)

    stock_meta = dict()
    for code in all_candidate_codes:
        try:
            # 调用engine新增接口获取指标
            bias20, ret20, cap, turnover = engine.get_stock_bias_cap_turnover(code)
            # 市值分组
            if cap >= CAP_LARGE:
                cap_group = "大盘"
            elif cap >= CAP_MID:
                cap_group = "中盘"
            else:
                cap_group = "小盘"
            # 过滤判定
            qualified = False
            hot = False
            if bias20 < BIAS20_THRESHOLD and ret20 < RET20_THRESHOLD and turnover >= MIN_TURNOVER_AMT:
                qualified = True
            if bias20 > HOT_BIAS_THRESHOLD:
                hot = True
            stock_meta[code] = {
                "bias20": bias20,
                "ret20": ret20,
                "cap": cap,
                "cap_group": cap_group,
                "turnover": turnover,
                "qualified": qualified,
                "hot": hot
            }
        except Exception as e:
            logger.warning(f"标的 {code} 指标计算失败: {e}")
            stock_meta[code] = {
                "bias20": None,
                "ret20": None,
                "cap": None,
                "cap_group": "未知",
                "turnover": 0,
                "qualified": False,
                "hot": False
            }

    # ======【改动点5：调用汇总函数，把新共振变量传入】 ======
    summary_webhook = os.environ.get("FEISHU_SUMMARY_WEBHOOK", "")
    if summary_webhook:
        ok = send_summary_to_feishu(
            all_strategy_results,
            cross_group_resonance,
            group_inner_overlap,
            all_tech_intersection,
            strategy_group_map,
            stock_hit_strategies,
            stock_meta,
            new_cross_family_resonance,  # 新增入参
            summary_webhook
        )
        logger.info(f"汇总推送结果: {ok}")

    logger.info("Sequoia-X V2 运行完成")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        _logger = get_logger(__name__)
        _logger.exception("主流程异常终止")
        sys.exit(1)
