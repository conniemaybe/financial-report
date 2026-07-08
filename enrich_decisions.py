#!/usr/bin/env python3
"""
2026-07-08 新增：从 portfolio.trades 自动丰富决策节点的 d-body 文本

问题：原版决策实录 d-body 是 automation 手工写的简短摘要
     （例如："卖出优先窗口 — 🚡系统性风险，2笔成交：通富微电止损500股@64.45 + 新华保险止盈200股@63.34"）
     信息密度低，没展示触发条件、价位分析、对比板块等关键决策依据

修复：从 portfolio.trades 里提取当日同时段（10:00 / 10:30 / 13:30 / 14:45）的所有交易，
     用完整 reason 字段拼出详尽的决策说明。

用法：python enrich_decisions.py
"""
import json
import re
from pathlib import Path

PORTFOLIO = Path.home() / ".workbuddy" / "astock-simulator" / "portfolio.json"
FUND_PORTFOLIO = Path.home() / ".workbuddy" / "astock-simulator" / "fund_portfolio.json"
INDEX_HTML = Path(r"C:\temp\financial-report\index.html")

# 时段窗口：每笔 trade.time 归到对应的决策节点
TIME_WINDOWS = [
    ("10:00", ["09:50", "10:00", "10:10"]),  # 10:00 节点：09:50-10:10 成交
    ("10:30", ["10:20", "10:30", "10:40"]),
    ("13:30", ["13:20", "13:30", "13:40"]),
    ("14:45", ["14:35", "14:45", "14:55"]),
]

# 节点定位名（窗口定位描述）
NODE_LABELS = {
    "10:00": "卖出优先窗口",
    "10:30": "回调低吸窗口",
    "13:30": "午后纯买入窗口",
    "14:45": "尾盘最佳买入窗口",
}


def infer_trade_time(action: str, raw_time: str) -> str:
    """从原始 time 字段兜底推断决策节点 ID（参考 html_report._infer_trade_time）"""
    if raw_time:
        for window_id, allowed in TIME_WINDOWS:
            if raw_time in allowed:
                return window_id
    # 无 time 字段：SELL→10:00，BUY→10:30
    return "10:00" if action == "SELL" else "10:30"


def get_today_str(portfolio: dict) -> str:
    """今日 = trades 最新日期（盘中 automation 写入但日报可能还没生成）"""
    trades = portfolio.get("trades", [])
    dates = [t.get("date") for t in trades if t.get("date")]
    return max(dates) if dates else None


def collect_trades_by_window(portfolio: dict, today: str) -> dict:
    """按时间窗口聚合当日交易"""
    trades = portfolio.get("trades", [])
    by_window = {w[0]: [] for w in TIME_WINDOWS}
    for t in trades:
        if t.get("date") != today:
            continue
        action = t.get("action", "")
        raw_time = t.get("time", "")
        window_id = infer_trade_time(action, raw_time)
        # 把推断后的 time 写回 trade（用于后续格式化展示）
        if "time" not in t or t["time"] not in [w[0] for w in TIME_WINDOWS]:
            t = dict(t)  # 浅拷贝避免污染原数据
            t["time"] = window_id
        by_window[window_id].append(t)
    return by_window


def format_decision_body(window_id: str, trades: list, market_signal: str = "") -> str:
    """格式化决策节点 body 文本"""
    label = NODE_LABELS.get(window_id, window_id)

    if not trades:
        return f"{label} — 无成交"

    # 拆分 BUY / SELL
    buys = [t for t in trades if t.get("action") == "BUY"]
    sells = [t for t in trades if t.get("action") == "SELL"]

    parts = [label]
    if market_signal:
        parts.append(market_signal)

    # 卖出明细（含完整 reason）
    if sells:
        sell_parts = []
        for t in sells:
            name = t.get("name", "")
            code = t.get("code", "")
            price = t.get("price") or t.get("nav") or 0
            shares = t.get("shares", 0)
            reason = t.get("reason", "")
            amount = t.get("amount") or price * shares
            sell_parts.append(f"{name}({code}) {shares}股@{price} = ¥{amount:,.0f}｜{reason}")
        parts.append("卖出 " + "； ".join(sell_parts))

    # 买入明细
    if buys:
        buy_parts = []
        for t in buys:
            name = t.get("name", "")
            code = t.get("code", "")
            price = t.get("price") or t.get("nav") or 0
            shares = t.get("shares", 0)
            reason = t.get("reason", "")
            amount = t.get("amount") or price * shares
            buy_parts.append(f"{name}({code}) {shares}股@{price} = ¥{amount:,.0f}｜{reason}")
        parts.append("买入 " + "； ".join(buy_parts))

    return " — ".join(parts)


def enrich_decision_nodes(html: str, today: str, astock_trades: dict, fund_trades: dict) -> str:
    """在 index.html 里找到决策节点并替换 d-body"""
    # 找到"今日盘中决策实录"section
    section_start = html.find("今日盘中决策")
    if section_start < 0:
        print("⚠️ 没找到'今日盘中决策实录'section")
        return html

    # 定位 section 结束（market-banner 之后）
    section_end_marker = '<div class="market-banner'
    section_end = html.find(section_end_marker, section_start)
    if section_end < 0:
        section_end = len(html)
    else:
        # 包含 banner 整行
        banner_end = html.find("</div>", section_end)
        section_end = banner_end + 6 if banner_end > 0 else section_end + 500

    section = html[section_start:section_end]

    # 提取 market-banner 的风险信号
    banner_match = re.search(r'<div class="market-banner[^"]*">(.*?)</div>', section, re.DOTALL)
    market_signal = ""
    if banner_match:
        banner_text = re.sub(r'<[^>]+>', '', banner_match.group(1)).strip()
        # 提取核心风险信号（截取第一句）
        # 例如："📊 7/8 盘中 · 🚡系统性风险锁定（10:00北向净流出22.3亿升级，保守原则当日不降级）"
        market_signal = banner_text.split("·")[1].strip() if "·" in banner_text else banner_text[:50]

    # 逐个替换 decision-node
    new_section = section
    for window_id, _ in TIME_WINDOWS:
        # 找该节点的 decision-node
        # 结构：<div class="decision-node ..."><div>...<div class="d-time">{time}</div>...</div><div class="d-body">{旧文本}</div></div>
        pattern = re.compile(
            r'(<div class="decision-node[^"]*"[^>]*>\s*<div>\s*<div class="d-time">'
            + re.escape(window_id)
            + r'</div>.*?<div class="d-body">)(.*?)(</div>\s*</div>)',
            re.DOTALL,
        )

        # 合并 A股 + 基金该时段的交易
        all_trades = astock_trades.get(window_id, []) + fund_trades.get(window_id, [])
        if not all_trades:
            # 没成交，保留 "⏳ 待执行" 状态
            continue

        new_body = format_decision_body(window_id, all_trades, market_signal)
        # HTML 转义（保留表情符号）
        new_body_html = new_body.replace("|", "｜")

        # 替换
        def replacer(m):
            return f'{m.group(1)}{new_body_html}{m.group(3)}'

        new_section, count = pattern.subn(replacer, new_section)
        if count > 0:
            print(f"  ✅ {window_id} 节点已更新：{new_body[:80]}...")
        else:
            print(f"  ⚠️ {window_id} 节点未匹配到 pattern")

    return html[:section_start] + new_section + html[section_end:]


def main():
    print(f"📖 读取 portfolio.json...")
    portfolio = json.loads(PORTFOLIO.read_text(encoding="utf-8"))
    today = get_today_str(portfolio)
    print(f"  今日（daily_records 最新）: {today}")

    astock_trades = collect_trades_by_window(portfolio, today)
    print(f"\nA股 当日交易窗口分布:")
    for w, trades in astock_trades.items():
        if trades:
            print(f"  {w}: {len(trades)} 笔 — {[t['name'] for t in trades]}")

    # 基金
    fund_trades = {}
    if FUND_PORTFOLIO.exists():
        fund_pf = json.loads(FUND_PORTFOLIO.read_text(encoding="utf-8"))
        fund_today = get_today_str(fund_pf) or today
        fund_trades = collect_trades_by_window(fund_pf, fund_today)
        if any(fund_trades.values()):
            print(f"\n基金 当日交易窗口分布:")
            for w, trades in fund_trades.items():
                if trades:
                    print(f"  {w}: {len(trades)} 笔 — {[t['name'] for t in trades]}")

    print(f"\n🔧 丰富决策节点...")
    html = INDEX_HTML.read_text(encoding="utf-8")
    new_html = enrich_decision_nodes(html, today, astock_trades, fund_trades)

    if new_html != html:
        INDEX_HTML.write_text(new_html, encoding="utf-8")
        print(f"\n✅ index.html 决策实录已丰富化")
    else:
        print(f"\n⚠️ 没有修改（可能当日无交易 或 pattern 没匹配）")


if __name__ == "__main__":
    main()
