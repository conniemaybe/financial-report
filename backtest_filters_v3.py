#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_filters_v3.py — 真正的过滤器回测（量价配合 + 相对强度）

过滤器C：量价配合度
  买入前5个交易日内：
    放量上涨天数 = (当日成交量 > 前5日均量) 且 (当日收盘 > 开盘)
    放量下跌天数 = (当日成交量 > 前5日均量) 且 (当日收盘 < 开盘)
    通过条件：放量上涨天数 - 放量下跌天数 ≥ 1
  含义：主力资金在买入前5日内净流入，不是出货

过滤器D：相对强度 RPS 简化版
  买入前5个交易日：
    个股5日累计涨幅 - 沪深300同窗口涨幅 ≥ +2%
  含义：跑赢大盘2个点，有真实阿尔法（不是跟着大盘混的）

过滤策略对比：
  - 原策略（v2）：个股5日涨幅≥2% + 个股20日涨幅>0（v2 已证明无效）
  - 过滤器C：量价配合度
  - 过滤器D：相对强度
  - 过滤器C+D：同时满足
"""
import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

# 强制代理（akshare_loader 风格）
_PROXY_HOST = "127.0.0.1"
_PROXY_PORT = "7892"
os.environ.setdefault("HTTP_PROXY", f"http://{_PROXY_HOST}:{_PROXY_PORT}")
os.environ.setdefault("HTTPS_PROXY", f"http://{_PROXY_HOST}:{_PROXY_PORT}")

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PORTFOLIO = Path.home() / ".workbuddy" / "astock-simulator" / "portfolio.json"


def load_portfolio() -> dict:
    return json.loads(PORTFOLIO.read_text(encoding="utf-8"))


def fetch_kline(code: str, start: str, end: str) -> list[dict]:
    """拉取个股日 K 线（前复权），返回开高低收量数据"""
    import akshare as ak
    try:
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust="qfq",
        )
        if df is None or df.empty:
            return []
        cols_map = {"日期": "date", "开盘": "open", "收盘": "close",
                    "最高": "high", "最低": "low", "成交量": "volume"}
        df = df.rename(columns=cols_map)
        return df.to_dict("records")
    except Exception as e:
        print(f"  [WARN] 拉取 {code} K线失败: {e}")
        return []


def fetch_hs300_kline(start: str, end: str) -> list[dict]:
    """拉取沪深300指数日 K 线"""
    import akshare as ak
    try:
        df = ak.stock_zh_index_daily(symbol="sh000300")
        if df is None or df.empty:
            return []
        # 过滤日期范围
        df["date"] = df["date"].astype(str)
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        df = df.sort_values("date")
        return df.to_dict("records")
    except Exception as e:
        print(f"  [WARN] 拉取沪深300失败: {e}")
        return []


def calc_filter_c(klines: list[dict], buy_idx: int, window: int = 5) -> dict:
    """过滤器C：量价配合度
    买入前 window 个交易日内：
      放量上涨天数 - 放量下跌天数 ≥ 1 → 通过
    """
    if buy_idx < window + 5:  # 需要额外5日算均量
        return {"pass": None, "vol_up": None, "vol_down": None, "detail": "数据不足"}

    # 前5日均量（用 buy_idx 前 window+5 到 buy_idx 前 5）
    vol_window = [klines[buy_idx - window - 5 + i]["volume"] for i in range(window)]
    avg_vol = sum(vol_window) / len(vol_window) if vol_window else 0

    vol_up = 0  # 放量上涨天数
    vol_down = 0  # 放量下跌天数
    details = []
    for i in range(window):
        idx = buy_idx - window + i
        k = klines[idx]
        is_vol_up = k["volume"] > avg_vol * 1.2  # 放量 = 量比1.2
        is_price_up = k["close"] > k["open"]
        if is_vol_up and is_price_up:
            vol_up += 1
            details.append(f"放量↑({k['date'][:10]})")
        elif is_vol_up and not is_price_up:
            vol_down += 1
            details.append(f"放量↓({k['date'][:10]})")

    diff = vol_up - vol_down
    return {
        "pass": diff >= 1,
        "vol_up": vol_up, "vol_down": vol_down,
        "diff": diff, "avg_vol": avg_vol,
        "detail": ", ".join(details) if details else "无明显放量",
    }


def calc_filter_d(klines: list[dict], hs300_klines: list[dict], buy_idx: int, window: int = 5) -> dict:
    """过滤器D：相对强度
    买入前 window 个交易日：
      个股累计涨幅 - 沪深300累计涨幅 ≥ +2% → 通过
    """
    if buy_idx < window + 1:
        return {"pass": None, "stock_pct": None, "hs300_pct": None, "diff": None, "detail": "数据不足"}

    # 个股5日累计涨幅
    stock_base = klines[buy_idx - window]["close"]
    stock_now = klines[buy_idx]["close"]
    stock_pct = (stock_now - stock_base) / stock_base * 100

    # 沪深300同窗口涨幅（按日期对齐）
    if not hs300_klines or len(hs300_klines) < window + 1:
        return {"pass": None, "stock_pct": stock_pct, "hs300_pct": None, "diff": None, "detail": "沪深300数据不足"}

    # 简化：取沪深300前window日和末日
    hs300_base = hs300_klines[-(window + 1)]["close"]
    hs300_now = hs300_klines[-1]["close"]
    hs300_pct = (hs300_now - hs300_base) / hs300_base * 100

    diff = stock_pct - hs300_pct
    return {
        "pass": diff >= 2.0,
        "stock_pct": stock_pct, "hs300_pct": hs300_pct,
        "diff": diff,
        "detail": f"个股{stock_pct:+.2f}% - HS300{hs300_pct:+.2f}% = {diff:+.2f}%",
    }


def main():
    portfolio = load_portfolio()
    buys = [t for t in portfolio.get("trades", []) if t.get("action") == "BUY"]
    code_groups = {}
    for b in buys:
        c = b.get("code")
        if c not in code_groups:
            code_groups[c] = {"code": c, "name": b.get("name"), "buys": []}
        code_groups[c]["buys"].append(b)

    # 预拉沪深300最近60日（用于所有标的对齐）
    latest_buy_date = max(b.get("date") for b in buys)
    earliest_buy_date = min(b.get("date") for b in buys)
    print(f"\n  预拉沪深300指数 {earliest_buy_date} ~ {latest_buy_date}...")
    # 沪深300拉一个足够长的窗口（最早买入前 40 日 ~ 最晚买入日）
    hs_start = (datetime.strptime(earliest_buy_date, "%Y-%m-%d") - timedelta(days=60)).strftime("%Y-%m-%d")
    hs_end = latest_buy_date
    hs300_all = fetch_hs300_kline(hs_start, hs_end)
    print(f"  沪深300共 {len(hs300_all)} 条记录")

    print(f"\n{'='*120}")
    print(f"📊 过滤器C+D 真实回测（共 {len(code_groups)} 只标的）")
    print(f"{'='*120}")
    print(f"  过滤器C：量价配合度（买入前5日 放量上涨-放量下跌 ≥ 1）")
    print(f"  过滤器D：相对强度（买入前5日 个股涨幅 - HS300涨幅 ≥ +2%）\n")

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

        print(f"  ⏳ 拉取 {name}({code}) {buy_date} K线...", end="", flush=True)
        buy_dt = datetime.strptime(buy_date, "%Y-%m-%d")
        kstart = (buy_dt - timedelta(days=40)).strftime("%Y-%m-%d")
        kend = (buy_dt + timedelta(days=2)).strftime("%Y-%m-%d")
        klines = fetch_kline(code, kstart, kend)
        print(f" {len(klines)}条")

        if len(klines) < 25:
            print(f"     ⚠️ K线不足，跳过")
            continue

        klines.sort(key=lambda x: str(x["date"]))
        buy_idx = None
        for i, k in enumerate(klines):
            if str(k["date"])[:10] >= buy_date:
                buy_idx = i
                break
        if buy_idx is None or buy_idx < 20:
            print(f"     ⚠️ 找不到买入日或数据不足，跳过")
            continue

        # 沪深300对应窗口（买入前5日到买入日）
        hs300_window = [h for h in hs300_all if str(h["date"])[:10] <= buy_date][-7:]
        fc = calc_filter_c(klines, buy_idx)
        fd = calc_filter_d(klines, hs300_window, buy_idx)

        pass_c = fc.get("pass")
        pass_d = fd.get("pass")
        pass_cd = (pass_c is True and pass_d is True)

        rows.append({
            "code": code, "name": name, "buy_date": buy_date, "status": status,
            "pnl_pct": pnl_pct,
            "fc": fc, "fd": fd,
            "pass_c": pass_c, "pass_d": pass_d, "pass_cd": pass_cd,
        })

    # 输出表格
    print(f"\n{'='*130}")
    print(f"{'标的':<10}{'买入日':<12}{'持有期':>10}  {'过滤器C':>10}  {'过滤器D':>10}  {'C+D联合':>10}  {'量价细节':<35}  {'强度细节':<30}")
    print("-" * 130)
    for r in rows:
        p_pnl = f"{r['pnl_pct']:+.2f}%" if r['pnl_pct'] is not None else "?"
        pc = "✅通过" if r['pass_c'] is True else ("❌剔除" if r['pass_c'] is False else "?")
        pd = "✅通过" if r['pass_d'] is True else ("❌剔除" if r['pass_d'] is False else "?")
        pcd = "✅保留" if r['pass_cd'] else ("❌剔除" if (r['pass_c'] is False or r['pass_d'] is False) else "?")
        c_detail = r['fc'].get('detail', '')[:33]
        d_detail = r['fd'].get('detail', '')[:28]
        print(f"{r['name']:<10}{r['buy_date']:<12}{p_pnl:>10}  {pc:>10}  {pd:>10}  {pcd:>10}  {c_detail:<35}  {d_detail:<30}")

    # 分组对比
    print(f"\n{'='*130}")
    print(f"📋 分组对比")
    print(f"{'='*130}")

    for filter_name, key in [("过滤器C（量价配合）", "pass_c"),
                              ("过滤器D（相对强度）", "pass_d"),
                              ("C+D联合", "pass_cd")]:
        kept = [r for r in rows if r[key] is True]
        excl = [r for r in rows if r[key] is False]
        k_pnl = [r["pnl_pct"] for r in kept if r["pnl_pct"] is not None]
        e_pnl = [r["pnl_pct"] for r in excl if r["pnl_pct"] is not None]
        print(f"\n【{filter_name}】")
        print(f"  保留 {len(kept)} 只 / 剔除 {len(excl)} 只")
        if k_pnl:
            k_avg = sum(k_pnl)/len(k_pnl)
            k_win = sum(1 for x in k_pnl if x > 0)
            print(f"  保留组平均收益：{k_avg:+.2f}%，胜率 {k_win}/{len(k_pnl)} = {k_win*100//len(k_pnl)}%")
        if e_pnl:
            e_avg = sum(e_pnl)/len(e_pnl)
            e_win = sum(1 for x in e_pnl if x > 0)
            print(f"  剔除组平均收益：{e_avg:+.2f}%，胜率 {e_win}/{len(e_pnl)} = {e_win*100//len(e_pnl)}%")

        if k_pnl and e_pnl:
            diff = k_avg - e_avg
            if diff > 5:
                verdict = "✅ 强烈建议加入"
            elif diff > 0:
                verdict = "🟡 弱有效（可作加分项）"
            else:
                verdict = "❌ 无效或有害"
            print(f"  差值（保留-剔除）：{diff:+.2f}% → {verdict}")

        # 列出每只被剔除的标的（关键验证）
        if excl:
            print(f"  被剔除的标的（验证是否救命）:")
            for r in excl:
                if r['pnl_pct'] is None: continue
                tag = "🎯救命" if r['pnl_pct'] < 0 else "⚠️误杀"
                print(f"    • {r['name']:10} {r['pnl_pct']:+.2f}% → {tag}")

    print()


if __name__ == "__main__":
    main()
