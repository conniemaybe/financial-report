#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_filters_v4.py — 用腾讯接口（绕开eastmoney反爬）跑过滤器C+D回测

数据源：https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
"""
import os
import sys
import json
import urllib.request
from datetime import datetime
from pathlib import Path

os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:7892")
os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7892")

PORTFOLIO = Path.home() / ".workbuddy" / "astock-simulator" / "portfolio.json"


def load_portfolio() -> dict:
    return json.loads(PORTFOLIO.read_text(encoding="utf-8"))


def _code_with_market(code: str) -> str:
    """A股代码转腾讯格式：沪市sh 深市sz。指数特殊路由。"""
    # 上交所指数（沪深300=000300 / 上证综指=000001 / 上证50=000016 等）
    INDEX_SH = {"000300", "000001", "000016", "000905", "000688", "000852"}
    if code in INDEX_SH:
        return f"sh{code}"
    # 深交所指数（深证成指=399001 / 创业板指=399006 / 深证100=399330）
    if code.startswith("399"):
        return f"sz{code}"
    # 个股：688/60开头是科创板/沪市主板
    if code.startswith(("60", "68", "90", "11", "13")):
        return f"sh{code}"
    return f"sz{code}"


def fetch_kline_tencent(code: str, start: str, end: str) -> list[dict]:
    """腾讯K线接口，返回 [{date,open,close,high,low,volume}, ...]"""
    sym = _code_with_market(code)
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,{start},{end},250,qfq"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw).get("data", {}).get(sym, {})
        kline_list = data.get("qfqday") or data.get("day") or []
        rows = []
        for k in kline_list:
            if len(k) < 6: continue
            rows.append({
                "date": k[0], "open": float(k[1]), "close": float(k[2]),
                "high": float(k[3]), "low": float(k[4]), "volume": float(k[5]),
            })
        return rows
    except Exception as e:
        print(f"  [WARN] {code} 拉取失败: {e}")
        return []


def calc_filter_c(klines: list[dict], buy_idx: int, window: int = 5) -> dict:
    """过滤器C：量价配合度"""
    if buy_idx < window + 5:
        return {"pass": None, "vol_up": None, "vol_down": None, "diff": None, "detail": "数据不足"}

    # 前5日均量（用 buy_idx 前 window+5 到 buy_idx 前 5）
    vol_window = [klines[buy_idx - window - 5 + i]["volume"] for i in range(window)]
    avg_vol = sum(vol_window) / len(vol_window) if vol_window else 0

    vol_up = vol_down = 0
    details = []
    for i in range(window):
        idx = buy_idx - window + i
        k = klines[idx]
        is_vol_up = k["volume"] > avg_vol * 1.2  # 量比1.2
        is_price_up = k["close"] > k["open"]
        if is_vol_up and is_price_up:
            vol_up += 1; details.append(f"放量↑{str(k['date'])[5:10]}")
        elif is_vol_up and not is_price_up:
            vol_down += 1; details.append(f"放量↓{str(k['date'])[5:10]}")
    diff = vol_up - vol_down
    return {"pass": diff >= 1, "vol_up": vol_up, "vol_down": vol_down, "diff": diff,
            "detail": ", ".join(details) if details else "无明显放量"}


def calc_filter_d(klines: list[dict], hs300: list[dict], buy_idx: int, window: int = 5) -> dict:
    """过滤器D：相对强度"""
    if buy_idx < window + 1:
        return {"pass": None, "stock_pct": None, "hs300_pct": None, "diff": None, "detail": "个股数据不足"}

    stock_base = klines[buy_idx - window]["close"]
    stock_now = klines[buy_idx]["close"]
    stock_pct = (stock_now - stock_base) / stock_base * 100

    buy_date = klines[buy_idx]["date"]
    # 在沪深300里找买入日索引
    hs_idx = None
    for i, k in enumerate(hs300):
        if k["date"] >= buy_date:
            hs_idx = i; break
    if hs_idx is None or hs_idx < window:
        return {"pass": None, "stock_pct": stock_pct, "hs300_pct": None, "diff": None, "detail": "HS300对齐失败"}

    hs_base = hs300[hs_idx - window]["close"]
    hs_now = hs300[hs_idx]["close"]
    hs_pct = (hs_now - hs_base) / hs_base * 100

    diff = stock_pct - hs_pct
    return {"pass": diff >= 2.0, "stock_pct": stock_pct, "hs300_pct": hs_pct, "diff": diff,
            "detail": f"个股{stock_pct:+.2f}% - HS300{hs_pct:+.2f}% = {diff:+.2f}%"}


def main():
    portfolio = load_portfolio()
    buys = [t for t in portfolio.get("trades", []) if t.get("action") == "BUY"]
    code_groups = {}
    for b in buys:
        c = b.get("code")
        if c not in code_groups:
            code_groups[c] = {"code": c, "name": b.get("name"), "buys": []}
        code_groups[c]["buys"].append(b)

    # 拉沪深300一个长窗口（覆盖所有买入日）
    earliest = min(b.get("date") for b in buys)
    latest = max(b.get("date") for b in buys)
    print(f"\n  预拉沪深300 {earliest} ~ {latest}...")
    hs300_all = fetch_kline_tencent("000300", start="2026-05-01", end=latest)
    print(f"  沪深300共 {len(hs300_all)} 条")

    print(f"\n{'='*130}")
    print(f"📊 过滤器C+D 真实回测（腾讯接口，共 {len(code_groups)} 只标的）")
    print(f"{'='*130}")
    print(f"  过滤器C：量价配合度（买入前5日 放量上涨-放量下跌 ≥ 1）")
    print(f"  过滤器D：相对强度（买入前5日 个股涨幅 - HS300涨幅 ≥ +2%）\n")

    rows = []
    for code, g in code_groups.items():
        name = g["name"]
        b = g["buys"][0]
        buy_price = b.get("price")
        buy_date = b.get("date")
        sells = [t for t in portfolio.get("trades", []) if t.get("code") == code and t.get("action") == "SELL"]
        if sells:
            sell_price = sells[-1].get("price")
            status = "已清仓"
        else:
            pos = portfolio.get("positions", {}).get(code, {})
            sell_price = pos.get("current_price") or pos.get("avg_cost")
            status = "持仓中"
        pnl_pct = ((sell_price or 0) - buy_price) / buy_price * 100 if buy_price and sell_price else None

        # 拉K线（从买入前40日到买入后10日，确保有足够数据）
        start_d = (datetime.strptime(buy_date, "%Y-%m-%d")).replace(month=max(1, datetime.strptime(buy_date, "%Y-%m-%d").month - 2)).strftime("%Y-%m-%d")
        end_d = latest
        print(f"  ⏳ {name}({code}) {buy_date}...", end="", flush=True)
        klines = fetch_kline_tencent(code, start_d, end_d)
        print(f" {len(klines)}条")
        if len(klines) < 25:
            print(f"     ⚠️ 数据不足，跳过"); continue

        klines.sort(key=lambda x: x["date"])
        buy_idx = None
        for i, k in enumerate(klines):
            if k["date"] >= buy_date:
                buy_idx = i; break
        if buy_idx is None or buy_idx < 20:
            print(f"     ⚠️ 找不到买入日，跳过"); continue

        fc = calc_filter_c(klines, buy_idx)
        fd = calc_filter_d(klines, hs300_all, buy_idx)
        pass_c = fc.get("pass")
        pass_d = fd.get("pass")
        pass_cd = (pass_c is True and pass_d is True)

        rows.append({"code": code, "name": name, "buy_date": buy_date, "status": status,
                     "pnl_pct": pnl_pct, "fc": fc, "fd": fd,
                     "pass_c": pass_c, "pass_d": pass_d, "pass_cd": pass_cd})

    # 表格输出
    print(f"\n{'='*150}")
    print(f"{'标的':<10}{'买入日':<12}{'持有期':>10}  {'C':>8}  {'D':>8}  {'C+D':>8}  {'量价细节':<40}  {'强度细节':<30}")
    print("-" * 150)
    for r in rows:
        p_pnl = f"{r['pnl_pct']:+.2f}%" if r['pnl_pct'] is not None else "?"
        pc = "✅" if r['pass_c'] is True else ("❌" if r['pass_c'] is False else "?")
        pd = "✅" if r['pass_d'] is True else ("❌" if r['pass_d'] is False else "?")
        pcd = "✅" if r['pass_cd'] else ("❌" if (r['pass_c'] is False or r['pass_d'] is False) else "?")
        c_detail = r['fc'].get('detail', '')[:38]
        d_detail = r['fd'].get('detail', '')[:28]
        print(f"{r['name']:<10}{r['buy_date']:<12}{p_pnl:>10}  {pc:>8}  {pd:>8}  {pcd:>8}  {c_detail:<40}  {d_detail:<30}")

    # 分组对比
    print(f"\n{'='*150}")
    print(f"📋 分组对比")
    print(f"{'='*150}")
    for fname, key in [("过滤器C（量价配合）", "pass_c"),
                       ("过滤器D（相对强度）", "pass_d"),
                       ("C+D 联合", "pass_cd")]:
        kept = [r for r in rows if r[key] is True]
        excl = [r for r in rows if r[key] is False]
        k_pnl = [r["pnl_pct"] for r in kept if r["pnl_pct"] is not None]
        e_pnl = [r["pnl_pct"] for r in excl if r["pnl_pct"] is not None]
        print(f"\n【{fname}】 保留 {len(kept)} / 剔除 {len(excl)}")
        if k_pnl:
            k_avg = sum(k_pnl)/len(k_pnl)
            k_win = sum(1 for x in k_pnl if x > 0)
            print(f"  保留组平均：{k_avg:+.2f}%，胜率 {k_win}/{len(k_pnl)}")
        if e_pnl:
            e_avg = sum(e_pnl)/len(e_pnl)
            e_win = sum(1 for x in e_pnl if x > 0)
            print(f"  剔除组平均：{e_avg:+.2f}%，胜率 {e_win}/{len(e_pnl)}")
        if k_pnl and e_pnl:
            diff = k_avg - e_avg
            verdict = "✅ 强烈建议加入" if diff > 5 else ("🟡 弱有效" if diff > 0 else "❌ 无效或有害")
            print(f"  差值：{diff:+.2f}% → {verdict}")
        if excl:
            print(f"  被剔除标的：")
            for r in excl:
                if r['pnl_pct'] is None: continue
                tag = "🎯救命" if r['pnl_pct'] < 0 else "⚠️误杀"
                print(f"    • {r['name']:10} {r['pnl_pct']:+.2f}% → {tag}")
    print()


if __name__ == "__main__":
    main()
