#!/usr/bin/env python3
"""
2026-07-08 v5 彻底重写：修正新建仓判定 + 修正当日盈亏计算

核心原则（吃过 3 次亏的教训）：
1. **"今日"= intraday_snapshots 最新一条的日期**（盘中 automation 写入的实时状态）
   不是 daily_records[-1].date（那是昨日日报存档）
2. **新建仓判定**：pos.buy_date == 今日（intraday_snapshots[0].date）
   只要 intraday_snapshots 已经有该仓位的数据，就不是新建仓
3. **当日盈亏**：直接用 intraday_snapshots[-1].positions[code].daily_pnl
   这是 portfolio_state.update_market_values() 算好的，无需 update_holdings 再算
4. **兜底**：intraday_snapshots 缺失时，用 daily_records 的 prev/current 价格反推
"""
import json
import re
from pathlib import Path
from datetime import date

ASTOCK_PORTFOLIO = Path(r"C:\Users\conniehe\.workbuddy\astock-simulator\portfolio.json")
FUND_PORTFOLIO = Path(r"C:\Users\conniehe\.workbuddy\astock-simulator\fund_portfolio.json")
INDEX_HTML = Path(r"C:\temp\financial-report\index.html")


def fmt_money(v: float) -> str:
    sign = "+" if v >= 0 else "-"
    return f"{sign}¥{abs(v):,.0f}"


def fmt_pct(v: float) -> str:
    """0.0112 → '+1.12%'（小数入参，渲染时 ×100）"""
    sign = "+" if v >= 0 else ""
    return f"{sign}{v*100:.2f}%"


def css_cls(v: float) -> str:
    return "up" if v > 0 else ("down" if v < 0 else "")


def get_today_from_intraday(portfolio: dict) -> str | None:
    """今日 = intraday_snapshots 第一条的日期（盘中 automation 写入的当天日期）"""
    snaps = portfolio.get("intraday_snapshots", [])
    for s in snaps:
        d = s.get("date")
        if d:
            return d
    return None


def build_astock_rows(portfolio: dict) -> str:
    positions = portfolio.get("positions", {})

    # 今日 = intraday_snapshots 的日期（不是 daily_records 最新日期！）
    intraday = portfolio.get("intraday_snapshots", [])
    today = get_today_from_intraday(portfolio)
    latest_intraday = intraday[-1] if intraday else None
    intraday_positions = (latest_intraday or {}).get("positions", {})

    # 兜底数据：daily_records 最新（昨日日报）
    records = portfolio.get("daily_records", [])
    prev_rec = records[-1] if records else None  # 7/7 日报
    prev_prev_rec = records[-2] if len(records) >= 2 else None  # 7/6 日报（用于新建仓判定 prev_price）

    rows = []
    for code, pos in positions.items():
        name = pos.get("name", "")
        shares = pos.get("shares", 0)
        cost = pos.get("avg_cost", 0)
        buy_date = pos.get("buy_date")

        # ===== 新建仓判定（v5 修正）=====
        # 今日 = intraday_snapshots 日期（如果有）
        # 新建仓 = pos.buy_date == 今日
        # 老仓位（buy_date 为 None 或 < 今日）→ 非新建仓
        is_new_position = bool(today) and buy_date == today

        # ===== 价格 & 市值（优先 intraday_snapshots）=====
        intraday_pos = intraday_positions.get(code, {})
        price = (
            intraday_pos.get("current_price")
            or pos.get("current_price")
            or cost
        )
        mv = intraday_pos.get("market_value") or price * shares
        pnl = intraday_pos.get("pnl") or pos.get("pnl", 0)
        pnl_pct = intraday_pos.get("pnl_pct") or pos.get("pnl_pct", 0)

        # ===== 当日盈亏（v5 修正）=====
        if is_new_position:
            # 真新建仓：当天买入，当日盈亏就是总盈亏（从成本到现价的浮动）
            # 显示"— 新建仓"更清晰（因为当日盈亏 = 总盈亏会让人误以为数据错了）
            daily_pnl = None
            daily_pct = None
        else:
            # 非新建仓：直接用 intraday_snapshots 算好的 daily_pnl
            daily_pnl = intraday_pos.get("daily_pnl")
            daily_pct = intraday_pos.get("daily_pnl_pct")
            # 兜底：仅当 intraday_snapshots 完全没算 daily_pnl 时才用 prev_rec 反推
            # 注意：不能用"daily_pnl == pnl"判定未更新，因为真新建仓两者会相等但 daily_pnl 是对的
            if daily_pnl is None:
                prev_snap = (prev_rec or {}).get("positions_snapshot", {}).get(code, {})
                prev_price = (
                    prev_snap.get("price")
                    or prev_snap.get("current_price")
                    or prev_snap.get("avg_cost", 0)
                )
                if prev_price > 0:
                    daily_pnl = (price - prev_price) * shares
                    daily_pct = (price - prev_price) / prev_price
                else:
                    daily_pnl, daily_pct = 0, 0

        # ===== 渲染 =====
        arrow = "↑" if price > cost else ("↓" if price < cost else "")
        cls_price = css_cls(price - cost)
        cls_pnl = css_cls(pnl)
        cls_dly = css_cls(daily_pnl or 0)

        if daily_pnl is None:
            daily_cell = "<td class=''><span style='color:#64748b'>— 新建仓</span></td>"
        elif daily_pnl == 0 and daily_pct == 0:
            daily_cell = "<td class=''><span style='color:#64748b'>— 无变化</span></td>"
        else:
            daily_cell = (
                f"<td class='{cls_dly}'>{fmt_money(daily_pnl)}<br>"
                f"<small>{fmt_pct(daily_pct)}</small></td>"
            )

        rows.append(
            f"<tr><td class='hide-mobile'><span class='stock-code'>{code}</span></td>"
            f"<td class='col-text'><span class='stock-name'>{name}</span></td>"
            f"<td>{shares:,} 股</td>"
            f"<td class='hide-mobile'>¥{cost:.2f}</td>"
            f"<td class='{cls_price}'>¥{price:.2f}{arrow}</td>"
            f"<td>¥{mv:,.0f}</td>"
            f"{daily_cell}"
            f"<td class='{cls_pnl}'>{fmt_money(pnl)}<br><small>{fmt_pct(pnl_pct)}</small></td></tr>"
        )
    return "\n        ".join(rows)


def build_fund_rows(portfolio: dict) -> str:
    positions = portfolio.get("positions", {})

    intraday = portfolio.get("intraday_snapshots", [])
    today = get_today_from_intraday(portfolio)
    latest_intraday = intraday[-1] if intraday else None
    intraday_positions = (latest_intraday or {}).get("positions", {})

    records = portfolio.get("daily_records", [])
    prev_rec = records[-1] if records else None

    rows = []
    for code, pos in positions.items():
        name = pos.get("name", "")
        shares = pos.get("shares", 0)
        avg_nav = pos.get("avg_nav", 0)
        buy_date = pos.get("buy_date")

        # 新建仓判定（同 A股逻辑）
        is_new_position = bool(today) and buy_date == today

        intraday_pos = intraday_positions.get(code, {})
        cur_nav = (
            intraday_pos.get("current_nav")
            or pos.get("current_nav")
            or avg_nav
        )
        mv = intraday_pos.get("market_value") or cur_nav * shares
        pnl = intraday_pos.get("pnl") or pos.get("pnl", 0)
        pnl_pct = intraday_pos.get("pnl_pct") or pos.get("pnl_pct", 0)

        # fund_type 显示
        ft_raw = pos.get("fund_type", "ETF")
        ft_display = ft_raw if "·" in ft_raw or len(ft_raw) > 6 else f"{ft_raw}·ETF"

        # 当日盈亏
        if is_new_position:
            daily_pnl = None
            daily_pct = None
        else:
            daily_pnl = intraday_pos.get("daily_pnl")
            daily_pct = intraday_pos.get("daily_pnl_pct")
            # 兜底：仅当 intraday 完全没算时才反推
            if daily_pnl is None:
                prev_snap = (prev_rec or {}).get("positions_snapshot", {}).get(code, {})
                prev_nav = (
                    prev_snap.get("current_nav")
                    or prev_snap.get("price")
                    or prev_snap.get("current_price")
                    or prev_snap.get("avg_nav", 0)
                )
                if prev_nav > 0:
                    daily_pnl = (cur_nav - prev_nav) * shares
                    daily_pct = (cur_nav - prev_nav) / prev_nav
                else:
                    daily_pnl, daily_pct = 0, 0

        arrow = "↑" if cur_nav > avg_nav else ("↓" if cur_nav < avg_nav else "")
        cls_price = css_cls(cur_nav - avg_nav)
        cls_pnl = css_cls(pnl)
        cls_dly = css_cls(daily_pnl or 0)

        if daily_pnl is None:
            daily_cell = "<td class=''><span style='color:#64748b'>— 新申购</span></td>"
        elif daily_pnl == 0 and daily_pct == 0:
            daily_cell = "<td class=''><span style='color:#64748b'>— 无变化</span></td>"
        else:
            daily_cell = (
                f"<td class='{cls_dly}'>{fmt_money(daily_pnl)}<br>"
                f"<small>{fmt_pct(daily_pct)}</small></td>"
            )

        rows.append(
            f"<tr><td class='hide-mobile'><span class='stock-code'>{code}</span></td>"
            f"<td class='col-text'><span class='stock-name'>{name}</span></td>"
            f"<td class='hide-mobile col-text'>{ft_display}</td>"
            f"<td>{shares:,.0f} 份</td>"
            f"<td class='hide-mobile'>¥{avg_nav:.4f}</td>"
            f"<td class='{cls_price}'>¥{cur_nav:.4f}{arrow}</td>"
            f"<td>¥{mv:,.0f}</td>"
            f"{daily_cell}"
            f"<td class='{cls_pnl}'>{fmt_money(pnl)}<br><small>{fmt_pct(pnl_pct)}</small></td></tr>"
        )
    return "\n        ".join(rows)


def update_index(astock_rows: str, fund_rows: str):
    html = INDEX_HTML.read_text(encoding="utf-8")

    html = re.sub(
        r"<thead><tr><th class=\"hide-mobile\">代码</th><th>名称</th><th>持仓</th>"
        r"<th class=\"hide-mobile\">成本</th><th>现价</th><th>市值</th>.*?</tr></thead>\s*<tbody>.*?</tbody>",
        f'<thead><tr><th class="hide-mobile">代码</th><th>名称</th><th>持仓</th>'
        f'<th class="hide-mobile">成本</th><th>现价</th><th>市值</th>'
        f'<th>当日盈亏</th><th>总盈亏</th></tr></thead>\n      <tbody>\n        {astock_rows}\n      </tbody>',
        html, count=1, flags=re.DOTALL,
    )

    html = re.sub(
        r"<thead><tr><th class=\"hide-mobile\">代码</th><th>名称</th><th class=\"hide-mobile\">类型</th>"
        r"<th>持仓</th><th class=\"hide-mobile\">成本净值</th><th>当前净值</th><th>市值</th>.*?</tr></thead>\s*<tbody>.*?</tbody>",
        f'<thead><tr><th class="hide-mobile">代码</th><th>名称</th><th class="hide-mobile">类型</th>'
        f'<th>持仓</th><th class="hide-mobile">成本净值</th><th>当前净值</th><th>市值</th>'
        f'<th>当日盈亏</th><th>总盈亏</th></tr></thead>\n      <tbody>\n        {fund_rows}\n      </tbody>',
        html, count=1, flags=re.DOTALL,
    )

    INDEX_HTML.write_text(html, encoding="utf-8")
    print(f"✅ index.html 持仓表已更新（A 股 + 基金，分两列）")


if __name__ == "__main__":
    with open(ASTOCK_PORTFOLIO, encoding="utf-8") as f:
        astock_pf = json.load(f)
    with open(FUND_PORTFOLIO, encoding="utf-8") as f:
        fund_pf = json.load(f)

    a_rows = build_astock_rows(astock_pf)
    f_rows = build_fund_rows(fund_pf)
    update_index(a_rows, f_rows)

    # 打印当日盈亏自检（让用户能直接核对）
    print("\n📊 当日盈亏自检：")
    today = get_today_from_intraday(astock_pf)
    print(f"  今日（intraday_snapshots 日期）: {today}")
    intraday = astock_pf.get("intraday_snapshots", [])
    if intraday:
        latest = intraday[-1]
        print(f"  最新 intraday 时间: {latest.get('time')}")
        total_daily = 0
        for code, p in latest.get("positions", {}).items():
            dp = p.get("daily_pnl", 0) or 0
            total_daily += dp
            print(f"    {p.get('name')}({code}): daily_pnl={dp:+.2f} ({(p.get('daily_pnl_pct') or 0)*100:+.2f}%)")
        print(f"  持仓浮盈合计: {total_daily:+.2f}")
