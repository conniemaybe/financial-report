#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
t_plus_3_review.py — 清仓后 T+3 复盘机制

功能：
  扫描所有"清仓日距今 ≤ 3 个交易日"的标的，查询现价，
  计算清仓后涨跌幅，按三档判定（卖对了/中性/卖早了），
  生成反思报告输出到控制台 + 写入 portfolio.json 的 t3_reviews 字段。

触发时机：
  每日日报流程末尾（safe-push + cross_validate 之后）

用法：
  python t_plus_3_review.py                # 复盘所有 T+3 窗口内的清仓标的
  python t_plus_3_review.py --days 5       # 自定义窗口（默认 3）
  python t_plus_3_review.py --today 2026-07-09   # 指定基准日

判定档位：
  清仓后跌 ≤ -3%  →  ✅ 卖对了（避开继续跌）
  清仓后波动 ±3%  →  🟡 卖得中性
  清仓后涨 ≥ +3%  →  ❌ 卖早了（触发反思）
"""

import sys
import json
import subprocess
import logging
from datetime import datetime, timedelta
from pathlib import Path

# ===== 路径 =====
PORTFOLIO = Path.home() / ".workbuddy" / "astock-simulator" / "portfolio.json"
FUND_PORTFOLIO = Path.home() / ".workbuddy" / "astock-simulator" / "fund_portfolio.json"
NEO_QUERY = Path.home() / ".workbuddy" / "skills-marketplace" / "skills" / "neodata-financial-search" / "scripts" / "query.py"
PYTHON = sys.executable

# ===== 日志 =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("t3_review")

# ===== 判定阈值 =====
SELL_RIGHT_THRESHOLD = -3.0   # 清仓后跌 ≤ -3% → 卖对了
SELL_WRONG_THRESHOLD = 3.0    # 清仓后涨 ≥ +3% → 卖早了


def load_portfolio() -> dict:
    if not PORTFOLIO.exists():
        return {}
    return json.loads(PORTFOLIO.read_text(encoding="utf-8"))


def save_portfolio(p: dict):
    PORTFOLIO.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")


def is_cleared(p: dict, code: str) -> tuple[bool, str | None, float | None, int | None]:
    """判断某标的是否已清仓。返回 (是否清仓, 清仓日, 清仓价, 清仓股数)"""
    if code in p.get("positions", {}):
        return (False, None, None, None)
    sells = [t for t in p.get("trades", []) if t.get("code") == code and t.get("action") == "SELL"]
    buys = [t for t in p.get("trades", []) if t.get("code") == code and t.get("action") == "BUY"]
    if not sells or not buys:
        return (False, None, None, None)
    total_buy = sum(b.get("shares", 0) for b in buys)
    total_sell = sum(s.get("shares", 0) for s in sells)
    if total_sell < total_buy or total_buy == 0:
        return (False, None, None, None)
    last_sell = sells[-1]
    return (True, last_sell.get("date"), last_sell.get("price"), total_sell)


def query_current_price(code: str, name: str) -> float | None:
    """通过 NeoData 查询现价"""
    try:
        result = subprocess.run(
            [PYTHON, str(NEO_QUERY), "--query", f"{name} {code} 最新股价"],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
            cwd=str(NEO_QUERY.parent),  # 关键：在脚本目录下运行，避免 ~ 展开问题
        )
        if result.returncode != 0:
            return None
        # 优先匹配 JSON 输出里的 "最新价格:XX.XX元"
        import re
        m = re.search(r"最新价格[：:]\s*([0-9]+\.?[0-9]*)\s*元", result.stdout)
        if m:
            return float(m.group(1))
        # 兜底：匹配其他价格关键字
        for pattern in [r"收盘价[：:]\s*([0-9]+\.?[0-9]*)\s*元", r"现价[：:]\s*([0-9]+\.?[0-9]*)\s*元"]:
            m = re.search(pattern, result.stdout)
            if m:
                return float(m.group(1))
        log.warning(f"  {name}({code}) 价格解析失败，NeoData 输出未匹配到价格关键字")
        return None
    except Exception as e:
        log.warning(f"查询 {name}({code}) 现价失败: {e}")
        return None


def trading_days_between(date1: str, date2: str) -> int:
    """粗略计算两个日期间的交易日数（跳过周末）"""
    d1 = datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.strptime(date2, "%Y-%m-%d")
    days = 0
    cur = d1
    while cur < d2:
        cur += timedelta(days=1)
        if cur.weekday() < 5:  # 0-4 是周一到周五
            days += 1
    return days


def run(today: str | None = None, window_days: int = 3) -> int:
    """主流程：返回生成的复盘记录条数"""
    today_dt = datetime.strptime(today, "%Y-%m-%d") if today else datetime.now()
    today_str = today_dt.strftime("%Y-%m-%d")
    log.info(f"========== T+3 清仓复盘 {today_str} (窗口={window_days}交易日) ==========")

    portfolio = load_portfolio()
    if not portfolio:
        log.warning("portfolio.json 不存在或为空")
        return 0

    # 找所有曾经交易过的 code
    all_codes = set()
    for t in portfolio.get("trades", []):
        if t.get("code"):
            all_codes.add((t["code"], t.get("name", "")))

    # 筛选：已清仓 + 清仓日距今 ≤ window_days 交易日
    targets = []
    for code, name in all_codes:
        is_clr, clr_date, clr_price, clr_shares = is_cleared(portfolio, code)
        if not is_clr or not clr_date or not clr_price:
            continue
        days = trading_days_between(clr_date, today_str)
        if 0 <= days <= window_days:
            targets.append({
                "code": code, "name": name,
                "clear_date": clr_date, "clear_price": clr_price,
                "clear_shares": clr_shares,
                "trading_days_since": days,
            })

    if not targets:
        log.info(f"✅ 窗口内（T+{window_days}）无清仓标的需复盘")
        return 0

    log.info(f"待复盘 {len(targets)} 只：{[t['name'] for t in targets]}")

    # 查现价 + 判定
    reviews = []
    for t in targets:
        now_price = query_current_price(t["code"], t["name"])
        if now_price is None:
            log.warning(f"  {t['name']}({t['code']}) 现价查询失败，跳过")
            continue
        post_pct = (now_price - t["clear_price"]) / t["clear_price"] * 100
        if post_pct <= SELL_RIGHT_THRESHOLD:
            verdict = "✅ 卖对了"
            reflection = f"清仓后 T+{t['trading_days_since']} 跌 {abs(post_pct):.2f}%，止损/止盈决策正确，避开了继续下跌"
        elif post_pct >= SELL_WRONG_THRESHOLD:
            verdict = "❌ 卖早了"
            reflection = f"清仓后 T+{t['trading_days_since']} 涨 {post_pct:.2f}%，触发反思：清仓当天的决策依据是什么？哪个信号误导？下次同类情况该如何处理？"
        else:
            verdict = "🟡 卖得中性"
            reflection = f"清仓后 T+{t['trading_days_since']} 波动 {post_pct:+.2f}%，无明显方向，决策可接受"

        review = {
            "code": t["code"], "name": t["name"],
            "clear_date": t["clear_date"], "clear_price": t["clear_price"],
            "review_date": today_str,
            "current_price": now_price,
            "post_clear_pct": round(post_pct, 2),
            "trading_days_since": t["trading_days_since"],
            "verdict": verdict,
            "reflection": reflection,
        }
        reviews.append(review)
        log.info(f"  {t['name']:8} 清仓价={t['clear_price']:.2f} 现价={now_price:.2f} 清仓后={post_pct:+.2f}% → {verdict}")

    # 写入 portfolio.json 的 t3_reviews
    if reviews:
        existing = portfolio.get("t3_reviews", [])
        # 去重：同 code + 同 review_date 只保留最新
        dedup_keys = {(r["code"], r["review_date"]) for r in reviews}
        existing = [r for r in existing if (r.get("code"), r.get("review_date")) not in dedup_keys]
        existing.extend(reviews)
        portfolio["t3_reviews"] = existing
        save_portfolio(portfolio)
        log.info(f"已写入 portfolio.json t3_reviews（共 {len(existing)} 条历史记录）")

    # 控制台报告
    if reviews:
        print(f"\n{'='*90}\n📋 T+{window_days} 清仓复盘报告（{today_str}）\n{'='*90}")
        print(f"{'标的':<10}{'清仓日':<12}{'清仓价':>8}{'现价':>8}{'清仓后':>10}{'持有天数':>10}  {'判定':<12}")
        print("-" * 90)
        for r in reviews:
            print(f"{r['name']:<10}{r['clear_date']:<12}{r['clear_price']:>8.2f}{r['current_price']:>8.2f}"
                  f"{r['post_clear_pct']:>+9.2f}%{r['trading_days_since']:>10}  {r['verdict']}")
        print(f"\n💡 反思要点（卖早了的标的）：")
        wrong_ones = [r for r in reviews if "卖早了" in r["verdict"]]
        if wrong_ones:
            for r in wrong_ones:
                print(f"  • {r['name']}：{r['reflection']}")
        else:
            print(f"  无卖早了的标的，本次复盘无反思触发。")
        print(f"{'='*90}\n")

    return len(reviews)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", help="基准日 YYYY-MM-DD（默认今天）")
    parser.add_argument("--days", type=int, default=3, help="复盘窗口（交易日，默认3）")
    args = parser.parse_args()
    run(today=args.today, window_days=args.days)
