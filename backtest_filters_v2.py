#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_filters_v2.py — 基于历史K线的过滤器假设验证

用 AkShare 拉历史 K 线，回测两个过滤器：
  A: 买入日所属板块5日累计涨幅 ≥ +2%
  B: 买入日 个股20日涨跌幅 > 0（近似 MA20 上方）

简化做法：用沪深300成分股所属的"行业板块"近似（AkShare 没有"概念板块历史涨幅"）。
更精确做法：直接用 个股 5日/20日 涨跌幅作为过滤条件——因为个股是其所属板块的代表样本。
"""
import sys
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

PORTFOLIO = Path.home() / ".workbuddy" / "astock-simulator" / "portfolio.json"

# 尝试加载 akshare（已在 backtest_engine 用过）
try:
    import akshare as ak
except ImportError:
    print("akshare 未安装，请先 pip install akshare")
    sys.exit(1)


def load_portfolio() -> dict:
    return json.loads(PORTFOLIO.read_text(encoding="utf-8"))


def fetch_kline(code: str, start: str, end: str) -> list[dict]:
    """拉取个股日 K 线（前复权）"""
    # A股个股日K
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                 start_date=start.replace("-", ""),
                                 end_date=end.replace("-", ""),
                                 adjust="qfq")
        if df is None or df.empty:
            return []
        # 标准化字段
        cols_map = {"日期": "date", "开盘": "open", "收盘": "close",
                    "最高": "high", "最低": "low", "成交量": "volume"}
        df = df.rename(columns=cols_map)
        return df.to_dict("records")
    except Exception as e:
        print(f"[WARN] 拉取 {code} K线失败: {e}")
        return []


def fetch_sector_kline(sector_name: str, start: str, end: str) -> list[dict]:
    """拉取行业板块历史涨跌幅"""
    # AkShare 行业板块日涨跌
    # 这里我们简化：直接用 个股 5日/20日涨幅 作为代理指标
    # 因为 NeoData 告诉我们该股"属于XX板块"，但板块历史涨跌幅 AkShare 接口不太稳定
    return []


def calc_filters_at_buy(code: str, buy_date: str) -> dict:
    """计算买入日的两个过滤器指标
    A: 过去5个交易日（不含买入日）累计涨幅
    B: 过去20个交易日累计涨幅（近似MA20位置）
    """
    buy_dt = datetime.strptime(buy_date, "%Y-%m-%d")
    start = (buy_dt - timedelta(days=40)).strftime("%Y-%m-%d")
    end = (buy_dt + timedelta(days=2)).strftime("%Y-%m-%d")  # 含买入日当天
    klines = fetch_kline(code, start, end)
    if len(klines) < 22:
        return {"k5_pct": None, "k20_pct": None, "samples": len(klines)}

    # 按日期排序
    klines.sort(key=lambda x: x["date"])
    # 找到买入日（或最接近的前一个交易日）的索引
    buy_idx = None
    for i, k in enumerate(klines):
        if str(k["date"])[:10] >= buy_date:
            buy_idx = i
            break
    if buy_idx is None or buy_idx < 20:
        return {"k5_pct": None, "k20_pct": None, "samples": len(klines)}

    buy_close = klines[buy_idx]["close"]
    # 过去5个交易日累计涨幅
    k5_base = klines[buy_idx - 5]["close"]
    k5_pct = (buy_close - k5_base) / k5_base * 100
    # 过去20个交易日累计涨幅
    k20_base = klines[buy_idx - 20]["close"]
    k20_pct = (buy_close - k20_base) / k20_base * 100

    return {"k5_pct": k5_pct, "k20_pct": k20_pct, "samples": len(klines),
            "buy_close": buy_close, "buy_idx": buy_idx}


def main():
    portfolio = load_portfolio()
    buys = [t for t in portfolio.get("trades", []) if t.get("action") == "BUY"]
    code_groups = {}
    for b in buys:
        c = b.get("code")
        if c not in code_groups:
            code_groups[c] = {"code": c, "name": b.get("name"), "buys": []}
        code_groups[c]["buys"].append(b)

    print(f"\n{'='*110}")
    print(f"📊 过滤器假设验证回测（基于历史K线，共 {len(code_groups)} 只标的）")
    print(f"{'='*110}")
    print(f"过滤器 A：买入前 5 个交易日累计涨幅 ≥ +2%（近似板块5日强度）")
    print(f"过滤器 B：买入前 20 个交易日累计涨幅 > 0（近似 MA20 上方）\n")

    rows = []
    for code, g in code_groups.items():
        name = g["name"]
        first_buy = g["buys"][0]
        buy_price = first_buy.get("price")
        buy_date = first_buy.get("date")
        # 卖出价
        sells = [t for t in portfolio.get("trades", []) if t.get("code") == code and t.get("action") == "SELL"]
        if sells:
            sell_price = sells[-1].get("price")
            status = "已清仓"
        else:
            pos = portfolio.get("positions", {}).get(code, {})
            sell_price = pos.get("current_price") or pos.get("avg_cost")
            status = "持仓中"
        pnl_pct = ((sell_price or 0) - buy_price) / buy_price * 100 if buy_price and sell_price else None

        print(f"  ⏳ 拉取 {name}({code}) {buy_date} 历史 K 线...", end="", flush=True)
        filters = calc_filters_at_buy(code, buy_date)
        print(f" 完成（{filters.get('samples', 0)} 条K线）")

        k5 = filters.get("k5_pct")
        k20 = filters.get("k20_pct")
        pass_a = (k5 is not None and k5 >= 2.0)
        pass_b = (k20 is not None and k20 > 0)

        rows.append({
            "code": code, "name": name, "buy_date": buy_date, "buy_price": buy_price,
            "sell_price": sell_price, "status": status, "pnl_pct": pnl_pct,
            "k5": k5, "k20": k20, "pass_a": pass_a, "pass_b": pass_b,
        })

    # 输出表格
    print(f"\n{'='*120}")
    print(f"{'标的':<10}{'买入日':<12}{'状态':<8}{'持有期':>10}  {'买入前5日':>12}  {'买入前20日':>12}  {'过滤器A':>10}  {'过滤器B':>10}  {'假设剔除':>10}")
    print("-" * 120)
    for r in rows:
        k5_str = f"{r['k5']:+.2f}%" if r['k5'] is not None else "?"
        k20_str = f"{r['k20']:+.2f}%" if r['k20'] is not None else "?"
        pa = "✅通过" if r['pass_a'] else "❌剔除"
        pb = "✅通过" if r['pass_b'] else "❌剔除"
        excluded = "❌剔除" if (not r['pass_a'] or not r['pass_b']) else "✅保留"
        pnl_str = f"{r['pnl_pct']:+.2f}%" if r['pnl_pct'] is not None else "?"
        print(f"{r['name']:<10}{r['buy_date']:<12}{r['status']:<8}{pnl_str:>10}  {k5_str:>12}  {k20_str:>12}  {pa:>10}  {pb:>10}  {excluded:>10}")

    # 结论
    print(f"\n{'='*120}")
    print(f"📋 回测结论")
    print(f"{'='*120}")
    excluded_rows = [r for r in rows if not r["pass_a"] or not r["pass_b"]]
    kept_rows = [r for r in rows if r["pass_a"] and r["pass_b"]]
    print(f"\n被剔除 {len(excluded_rows)} 只 / 通过 {len(kept_rows)} 只")

    if excluded_rows:
        print(f"\n❌ 被假设过滤器剔除的标的（如果过滤器生效，这些不会买）：")
        for r in excluded_rows:
            if r['pnl_pct'] is None: continue
            k5_str = f"{r['k5']:+.2f}%" if r['k5'] is not None else "?"
            k20_str = f"{r['k20']:+.2f}%" if r['k20'] is not None else "?"
            tag = "🎯救命（实际亏钱，过滤器救了）" if r['pnl_pct'] < 0 else "⚠️误杀（实际赚钱，过滤器错过了）"
            print(f"  • {r['name']:10} 持有期 {r['pnl_pct']:+.2f}%  | 5日={k5_str} 20日={k20_str}  → {tag}")

    if kept_rows:
        print(f"\n✅ 通过过滤器的标的：")
        for r in kept_rows:
            if r['pnl_pct'] is None: continue
            k5_str = f"{r['k5']:+.2f}%" if r['k5'] is not None else "?"
            k20_str = f"{r['k20']:+.2f}%" if r['k20'] is not None else "?"
            print(f"  • {r['name']:10} 持有期 {r['pnl_pct']:+.2f}%  | 5日={k5_str} 20日={k20_str}")

    e_pnl = [r["pnl_pct"] for r in excluded_rows if r["pnl_pct"] is not None]
    k_pnl = [r["pnl_pct"] for r in kept_rows if r["pnl_pct"] is not None]
    if e_pnl and k_pnl:
        avg_e = sum(e_pnl)/len(e_pnl)
        avg_k = sum(k_pnl)/len(k_pnl)
        print(f"\n  📊 被剔除组平均收益：{avg_e:+.2f}%")
        print(f"  📊 通过组平均收益：  {avg_k:+.2f}%")
        diff = avg_k - avg_e
        if diff > 5:
            verdict = "✅ 强烈建议加入——通过组明显跑赢"
        elif diff > 0:
            verdict = "🟡 弱有效——可作为加分项"
        else:
            verdict = "❌ 不建议——被剔除组反而跑赢"
        print(f"  📊 差值：{diff:+.2f}% → {verdict}")
    print()


if __name__ == "__main__":
    main()
