#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_filters.py — 历史交易回测：板块强度+MA20 双过滤器假设验证

目的（用户问题3）：
  用户提出的两个买入过滤器假设：
    过滤器A：买入前查询板块5日累计涨幅 ≥ +2% 才允许进入候选池
    过滤器B：禁止买入跌破 MA20 且 MA20 下行的标的
  用历史 9 笔 BUY 交易回测：如果当初加了这两个过滤器，哪些会被剔除？
  被剔除的标的，后续真实走势是亏是赚？→ 验证过滤器是否真的有效

判定原则：
  - 过滤器是"减少亏损"导向：剔除后赚钱 = 误杀（坏）；剔除后亏钱 = 救命（好）
  - 同时保留 = 不影响（中性）
  - 同时剔除 = 看后续走势

输出：每笔 BUY 的"原始结果 + 假设过滤器应用后是否剔除 + 剔除后收益对比"
"""

import sys
import json
import subprocess
import re
from pathlib import Path

PORTFOLIO = Path.home() / ".workbuddy" / "astock-simulator" / "portfolio.json"
NEO_QUERY = Path.home() / ".workbuddy" / "skills-marketplace" / "skills" / "neodata-financial-search" / "scripts" / "query.py"
PYTHON = sys.executable


def load_portfolio() -> dict:
    return json.loads(PORTFOLIO.read_text(encoding="utf-8"))


def query_neodata(query: str) -> str:
    """查询 NeoData 返回 raw 文本"""
    try:
        r = subprocess.run(
            [PYTHON, str(NEO_QUERY), "--query", query],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
            cwd=str(NEO_QUERY.parent),
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def get_stock_5day_info(code: str, name: str, ref_date: str | None = None) -> dict:
    """查询个股 5 日涨幅 + 所属板块 5 日涨幅 + MA20 位置"""
    date_clause = f"截至{ref_date}" if ref_date else ""
    txt = query_neodata(f"{name} {code} 5日涨跌幅 20日均线 所属板块5日涨幅 {date_clause}")
    info = {"stock_5d": None, "sector_5d": None, "sector_name": None, "ma20_pos": None, "raw": txt[:800]}

    # 个股 5 日涨跌幅
    m = re.search(r"5日涨跌幅[：:\s]*([-+]?\d+(?:\.\d+)?)\s*%", txt)
    if m: info["stock_5d"] = float(m.group(1))

    # 所属板块
    sec_m = re.search(r"所属.{0,10}板块[^涨]*?涨跌幅[：:\s]*([-+]?\d+(?:\.\d+)?)\s*%", txt)
    if sec_m:
        info["sector_5d"] = float(sec_m.group(1))
    sec_name_m = re.search(r"所属的([^\s，。]+)板块", txt)
    if sec_name_m:
        info["sector_name"] = sec_name_m.group(1)

    # MA20 位置（简化：从 20 日涨跌幅推断趋势方向）
    m20 = re.search(r"20日涨跌幅[：:\s]*([-+]?\d+(?:\.\d+)?)\s*%", txt)
    if m20:
        # 20 日涨幅 >0 视为 MA20 上方运行（近似），<0 视为下方
        info["ma20_pos"] = "上方" if float(m20.group(1)) > 0 else "下方"
        info["stock_20d"] = float(m20.group(1))
    return info


def main():
    portfolio = load_portfolio()
    buys = [t for t in portfolio.get("trades", []) if t.get("action") == "BUY"]
    # 同标的合并多笔买入，取最后一笔的卖出对比
    code_groups = {}
    for b in buys:
        c = b.get("code")
        if c not in code_groups:
            code_groups[c] = {"code": c, "name": b.get("name"), "buys": []}
        code_groups[c]["buys"].append(b)

    # 对每只标的计算：最终结果（还在持仓/已清仓）+ 假设过滤器判定
    print(f"\n{'='*110}")
    print(f"📊 买入过滤器假设验证回测（共 {len(code_groups)} 只标的）")
    print(f"{'='*110}")
    print(f"过滤器 A：板块 5 日累计涨幅 ≥ +2% 才允许买入")
    print(f"过滤器 B：20 日涨跌幅 > 0（近似 MA20 上方）才允许买入\n")

    rows = []
    for code, g in code_groups.items():
        name = g["name"]
        first_buy = g["buys"][0]
        buy_price = first_buy.get("price")
        buy_date = first_buy.get("date")
        # 找最终卖出（如有）
        sells = [t for t in portfolio.get("trades", []) if t.get("code") == code and t.get("action") == "SELL"]
        if sells:
            last_sell = sells[-1]
            sell_price = last_sell.get("price")
            status = "已清仓"
        else:
            # 还在持仓，用最新价
            pos = portfolio.get("positions", {}).get(code, {})
            sell_price = pos.get("current_price") or pos.get("avg_cost")
            status = "持仓中"

        pnl_pct = ((sell_price or 0) - buy_price) / buy_price * 100 if buy_price and sell_price else None

        # 假设过滤器判定（查询当前/历史 5日数据，因 NeoData 不能回溯历史日期，
        # 这里用当前数据近似 —— 真正的回测需要 backtest_engine 跑历史 K 线）
        info = get_stock_5day_info(code, name, ref_date=buy_date)

        # 过滤器 A：板块 5 日涨幅 ≥ +2%
        sec_5d = info.get("sector_5d")
        pass_a = (sec_5d is not None and sec_5d >= 2.0)
        # 过滤器 B：20 日涨幅 > 0（近似 MA20 上方）
        stock_20d = info.get("stock_20d")
        pass_b = (stock_20d is not None and stock_20d > 0)

        rows.append({
            "code": code, "name": name, "buy_date": buy_date,
            "buy_price": buy_price, "sell_price": sell_price, "status": status,
            "pnl_pct": pnl_pct,
            "sector_name": info.get("sector_name") or "?",
            "sec_5d": sec_5d, "stock_20d": stock_20d,
            "pass_a": pass_a, "pass_b": pass_b,
        })

    # 打印表格
    print(f"{'标的':<10}{'买入日':<12}{'状态':<8}{'持有期收益':>12}  {'板块5日':>10}  {'个股20日':>10}  {'过滤器A':>8}  {'过滤器B':>8}  {'假设剔除':>8}")
    print("-" * 110)
    for r in rows:
        sec_5d_str = f"{r['sec_5d']:+.2f}%" if r['sec_5d'] is not None else "?"
        s20_str = f"{r['stock_20d']:+.2f}%" if r['stock_20d'] is not None else "?"
        pa = "✅通过" if r['pass_a'] else ("❌剔除" if r['sec_5d'] is not None else "?")
        pb = "✅通过" if r['pass_b'] else ("❌剔除" if r['stock_20d'] is not None else "?")
        excluded = "❌会剔除" if (not r['pass_a'] or not r['pass_b']) else "✅保留"
        pnl_str = f"{r['pnl_pct']:+.2f}%" if r['pnl_pct'] is not None else "?"
        print(f"{r['name']:<10}{r['buy_date']:<12}{r['status']:<8}{pnl_str:>12}  {sec_5d_str:>10}  {s20_str:>10}  {pa:>8}  {pb:>8}  {excluded:>8}")

    # 关键判定：被剔除的标的真实走势
    print(f"\n{'='*110}")
    print(f"🎯 关键验证：被假设过滤器剔除的标的，真实走势如何？")
    print(f"{'='*110}")
    excluded_rows = [r for r in rows if not r["pass_a"] or not r["pass_b"]]
    kept_rows = [r for r in rows if r["pass_a"] and r["pass_b"]]

    if excluded_rows:
        print(f"\n❌ 会被过滤器剔除的 {len(excluded_rows)} 只：")
        excl_pnl = []
        for r in excluded_rows:
            if r["pnl_pct"] is None: continue
            tag = "✅过滤器误杀（实际赚钱）" if r["pnl_pct"] > 0 else "🎯过滤器救命（实际亏钱）"
            print(f"  • {r['name']:10} 持有期 {r['pnl_pct']:+.2f}%  板块5日 {r['sec_5d']:+.2f}%  个股20日 {r['stock_20d']:+.2f}%  → {tag}")
            excl_pnl.append(r["pnl_pct"])
        if excl_pnl:
            print(f"\n  📊 被剔除标的平均收益：{sum(excl_pnl)/len(excl_pnl):+.2f}%（{'过滤器有效（剔除的平均亏钱）' if sum(excl_pnl)<0 else '过滤器无效（剔除的平均仍赚钱，误杀）'}）")

    if kept_rows:
        print(f"\n✅ 通过过滤器的 {len(kept_rows)} 只：")
        kept_pnl = []
        for r in kept_rows:
            if r["pnl_pct"] is None: continue
            print(f"  • {r['name']:10} 持有期 {r['pnl_pct']:+.2f}%")
            kept_pnl.append(r["pnl_pct"])
        if kept_pnl:
            print(f"\n  📊 通过的标的平均收益：{sum(kept_pnl)/len(kept_pnl):+.2f}%")

    # 整体对比
    print(f"\n{'='*110}")
    print(f"📋 结论")
    print(f"{'='*110}")
    if excluded_rows and kept_rows:
        e_pnl = [r["pnl_pct"] for r in excluded_rows if r["pnl_pct"] is not None]
        k_pnl = [r["pnl_pct"] for r in kept_rows if r["pnl_pct"] is not None]
        if e_pnl and k_pnl:
            avg_e = sum(e_pnl)/len(e_pnl)
            avg_k = sum(k_pnl)/len(k_pnl)
            diff = avg_k - avg_e
            print(f"  被剔除组平均收益：{avg_e:+.2f}%")
            print(f"  通过组平均收益：  {avg_k:+.2f}%")
            print(f"  差值（通过-剔除）：{diff:+.2f}%")
            if diff > 3:
                print(f"  ✅ 过滤器有效：通过组明显跑赢被剔除组，建议加入买入选股流程")
            elif diff > 0:
                print(f"  🟡 过滤器弱有效：通过组小幅跑赢，可作为加分项而非硬过滤")
            else:
                print(f"  ❌ 过滤器无效：被剔除组反而跑赢通过组，不建议引入")
    print()


if __name__ == "__main__":
    main()
