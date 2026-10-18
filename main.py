def main():
    settings = get_settings()
    # =========修复点：恢复原始，传入settings对象，不要.settings.db_path，删掉多余init_db()=========
    engine = DataEngine(settings)

    # 全局一次性加载全市场股票列表，所有策略共用
    logger.info("全局一次性加载全市场股票列表，所有策略复用，避免重复请求")
    global_stock_codes = engine.get_all_symbols()

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
        RpsBreakoutStrategy,
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
