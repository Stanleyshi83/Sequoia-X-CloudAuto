"""
generate_resonance_md.py
读取action日志，自动生成共振标的Markdown台账模板
使用方式：
方案A：把github action日志全部复制保存为 log.txt
> python generate_resonance_md.py log.txt

方案B：直接粘贴日志字符串到代码内log_text变量，本地测试
输出：resonance_today.md
新增限制：无共振标的时，不生成空md文件
"""
import re
import sys
from dataclasses import dataclass
from typing import List, Dict

@dataclass
class ResonanceItem:
    stock_code: str
    resonance_type: str
    hit_strategies: List[str]
    date_str: str

def parse_log(log_content:str) -> List[ResonanceItem]:
    # 提取日期
    date_match = re.search(r"【选股汇总】 (\d{4}-\d{2}-\d{2})", log_content)
    date_str = date_match.group(1) if date_match else ""

    # 提取 终极共振标的
    ultimate_match = re.search(r"【10个技术策略同时命中标的｜排除事件策略】共\d+只：\[([^\]]*)\]", log_content)
    cross_match = re.search(r"【跨组共振标的】共\d+只：\[([^\]]*)\]", log_content)

    # 股票 -> 命中策略
    stock_hit_strategy:Dict[str,List[str]] = {}
    pattern_hit = re.findall(r"(\w+Strategy) 选出 \d+ 只股票.*?\[([^\]]*)\]", log_content, re.S)
    for strat_name, code_str in pattern_hit:
        codes = [c.strip().strip("'") for c in code_str.split(",") if c.strip().strip("'")]
        for code in codes:
            if code not in stock_hit_strategy:
                stock_hit_strategy[code] = []
            stock_hit_strategy[code].append(strat_name)

    result_list = []
    # 终极共振
    if ultimate_match:
        code_raw = ultimate_match.group(1)
        codes = [c.strip().strip("'") for c in code_raw.split(",") if c.strip().strip("'")]
        for code in codes:
            result_list.append(ResonanceItem(
                stock_code=code,
                resonance_type="终极共振",
                hit_strategies=stock_hit_strategy.get(code, []),
                date_str=date_str
            ))
    # 跨组共振
    if cross_match:
        code_raw = cross_match.group(1)
        codes = [c.strip().strip("'") for c in code_raw.split(",") if c.strip().strip("'")]
        for code in codes:
            result_list.append(ResonanceItem(
                stock_code=code,
                resonance_type="跨组共振",
                hit_strategies=stock_hit_strategy.get(code, []),
                date_str=date_str
            ))
    return result_list

def render_md(items:List[ResonanceItem]) -> str:
    md_lines = []
    for item in items:
        md = f"""# 共振标的跟踪台账
> 共振类型：□终极共振 □跨组共振 □组内同策略重合
股票代码：{item.stock_code}
股票名称：
行业：
触发日期：{item.date_str}
触发当日收盘价：
命中策略清单：{', '.join(item.hit_strategies)}

## 阶段1 快速排雷
ST/退市风险：
近20日日均成交额：
重大利空/解禁减持：
排雷结论：□通过 □剔除，原因：

## 阶段2 基本面分析
行业周期：
竞争地位：
财务简述（营收/扣非/毛利/现金流）：
潜在催化：
基本面评级：□A有催化 □B平稳 □C基本面恶化

## 阶段3 技术复盘
逐个策略核验：
支撑位：
压力位：
筹码峰：
共振真假：□真共振 □假共振，原因：

## 阶段4 资金&板块
股东人数变化：
北向/机构持仓：
板块趋势：
大盘环境：

## 阶段5 标的分级与计划
标的分级：□A重点观察 □B普通观察 □C剔除
计划：
止损参考价：

## 阶段6 后续跟踪记录
T+3涨跌幅：
T+5涨跌幅：
T+20涨跌幅：
T+60涨跌幅：
最大回撤：
最终复盘结论：

---
"""
        md_lines.append(md)
    return "\n".join(md_lines)

def main():
    if len(sys.argv) >=2:
        log_file_path = sys.argv[1]
        with open(log_file_path,"r",encoding="utf-8") as f:
            log_content = f.read()
    else:
        print("请传入日志文件：python generate_resonance_md.py log.txt")
        return
    items = parse_log(log_content)
    if not items:
        print("ℹ️ 今日无共振标的，跳过生成 resonance_today.md")
        return
    md_text = render_md(items)
    with open("resonance_today.md","w",encoding="utf-8") as f:
        f.write(md_text)
    print(f"✅已生成 resonance_today.md，共 {len(items)} 条共振标的台账")

if __name__ == "__main__":
    main()
