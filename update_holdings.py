#!/usr/bin/env python3
"""
2026-07-08 v6 彻底重写：完全弃用 intraday_snapshots.daily_pnl，自己算

吃过 4 次坑的最终结论：
1. intraday_snapshots.daily_pnl 在 A股日内减仓后不会按新份额重算（14:45 bug）
2. intraday_snapshots.daily_pnl_pct 在 基金 实际是"总盈亏%"不是"当日%"（数据源 bug）
3. 唯一可靠的当日盈亏计算方法：用 prev_rec（昨日日报 snapshot）的 price 自己算
   daily_pnl = (今日价 - 昨日价) × 当前持仓份额

边界处理：
- 新建仓（buy_date == 今日）：显示"— 新建仓"
- 日内减仓（14:45 卖出部分）：用当前剩余份额 × (今日价 - 昨日价)，这是简化但合理的口径
  （理论上应该分前段/后段算，但网站展示用近似值即可）
- 无 prev_rec 数据（历史首日）：显示"— 无数据"
"""
import json
import re
from pathlib import Path

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


def get_today(portfolio: dict) -> str | None:
    """今日 = intraday_snapshots 第一条的日期"""
    for s in portfolio.get("intraday_snapshots", []):
        d = s.get("date")
        if d:
            return d
    return None


def get_prev_snapshot(portfolio: dict, code: str) -> dict:
    """从 daily_records[-1]（昨日日报）拿该标的的 snapshot"""
    records = portfolio.get("daily_records", [])
    if not records:
        return {}
    return records[-1].get("positions_snapshot", {}).get(code, {})


def compute_daily_pnl_astock(portfolio: dict, code: str, pos: dict, today_price: float, today: str | None) -> tuple[float | None, float | None, bool]:
    """计算 A股当日盈亏。返回 (daily_pnl, daily_pct, is_new_position)"""
    shares = pos.get("shares", 0)
    buy_date = pos.get("buy_date")
    is_new_position = bool(today) and buy_date == today

    if is_new_position:
        return None, None, True

    # 用昨日 snapshot 的 price 反推
    prev_snap = get_prev_snapshot(portfolio, code)
    prev_price = (
        prev_snap.get("price")
        or prev_snap.get("current_price")
        or prev_snap.get("avg_cost", 0)
    )
    if prev_price > 0:
        daily_pnl = (today_price - prev_price) * shares
        daily_pct = (today_price - prev_price) / prev_price
        return daily_pnl, daily_pct, False
    return None, None, False  # 无 prev 数据


def compute_daily_pnl_fund(portfolio: dict, code: str, pos: dict, today_nav: float, today: str | None) -> tuple[float | None, float | None, bool]:
    """计算基金当日盈亏"""
    shares = pos.get("shares", 0)
    buy_date = pos.get("buy_date")
    is_new_position = bool(today) and buy_date == today

    if is_new_position:
        return None, None, True

    prev_snap = get_prev_snapshot(portfolio, code)
    prev_nav = (
        prev_snap.get("current_nav")
        or prev_snap.get("price")
        or prev_snap.get("current_price")
        or prev_snap.get("avg_nav", 0)
    )
    if prev_nav > 0:
        daily_pnl = (today_nav - prev_nav) * shares
        daily_pct = (today_nav - prev_nav) / prev_nav
        return daily_pnl, daily_pct, False
    return None, None, False


def build_astock_rows(portfolio: dict) -> str:
    positions = portfolio.get("positions", {})
    today = get_today(portfolio)

    # intraday_snapshots 只用来取"今日现价"（这个数据可靠）
    intraday = portfolio.get("intraday_snapshots", [])
    latest_intraday = intraday[-1] if intraday else None
    intraday_positions = (latest_intraday or {}).get("positions", {})

    rows = []
    debug_lines = []
    for code, pos in positions.items():
        name = pos.get("name", "")
        shares = pos.get("shares", 0)
        cost = pos.get("avg_cost", 0)

        # 今日现价（仅用这个字段，不用 daily_pnl）
        intraday_pos = intraday_positions.get(code, {})
        price = (
            intraday_pos.get("current_price")
            or pos.get("current_price")
            or cost
        )
        mv = price * shares
        pnl = (price - cost) * shares
        pnl_pct = (price - cost) / cost if cost > 0 else 0

        # 当日盈亏：完全自算
        daily_pnl, daily_pct, is_new = compute_daily_pnl_astock(
            portfolio, code, pos, price, today
        )

        # 渲染
        arrow = "↑" if price > cost else ("↓" if price < cost else "")
        cls_price = css_cls(price - cost)
        cls_pnl = css_cls(pnl)
        cls_dly = css_cls(daily_pnl or 0)

        if daily_pnl is None:
            if is_new:
                daily_cell = "<td class=''><span style='color:#64748b'>— 新建仓</span></td>"
            else:
                daily_cell = "<td class=''><span style='color:#64748b'>— 无数据</span></td>"
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
        debug_lines.append(f"  {name}({code}) shares={shares} price={price} daily_pnl={daily_pnl} daily_pct={(daily_pct or 0)*100:.2f}%")

    # 自检输出
    print("📊 A股 当日盈亏自检（v6 全自算）：")
    for line in debug_lines:
        print(line)
    return "\n        ".join(rows)


def build_fund_rows(portfolio: dict) -> str:
    positions = portfolio.get("positions", {})
    today = get_today(portfolio)

    intraday = portfolio.get("intraday_snapshots", [])
    latest_intraday = intraday[-1] if intraday else None
    intraday_positions = (latest_intraday or {}).get("positions", {})

    rows = []
    debug_lines = []
    for code, pos in positions.items():
        name = pos.get("name", "")
        shares = pos.get("shares", 0)
        avg_nav = pos.get("avg_nav", 0)

        # 今日净值（仅用这个字段）
        intraday_pos = intraday_positions.get(code, {})
        cur_nav = (
            intraday_pos.get("current_nav")
            or pos.get("current_nav")
            or avg_nav
        )
        mv = cur_nav * shares
        pnl = (cur_nav - avg_nav) * shares
        pnl_pct = (cur_nav - avg_nav) / avg_nav if avg_nav > 0 else 0

        # 当日盈亏：完全自算
        daily_pnl, daily_pct, is_new = compute_daily_pnl_fund(
            portfolio, code, pos, cur_nav, today
        )

        ft_raw = pos.get("fund_type", "ETF")
        ft_display = ft_raw if "·" in ft_raw or len(ft_raw) > 6 else f"{ft_raw}·ETF"

        arrow = "↑" if cur_nav > avg_nav else ("↓" if cur_nav < avg_nav else "")
        cls_price = css_cls(cur_nav - avg_nav)
        cls_pnl = css_cls(pnl)
        cls_dly = css_cls(daily_pnl or 0)

        if daily_pnl is None:
            daily_cell = f"<td class=''><span style='color:#64748b'>— {'新申购' if is_new else '无数据'}</span></td>"
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
        debug_lines.append(f"  {name}({code}) shares={shares} nav={cur_nav} daily_pnl={daily_pnl} daily_pct={(daily_pct or 0)*100:.2f}%")

    print("\n📊 基金 当日盈亏自检（v6 全自算）：")
    for line in debug_lines:
        print(line)
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
    print(f"\n✅ index.html 持仓表已更新（A 股 + 基金，分两列，v6 全自算）")


if __name__ == "__main__":
    with open(ASTOCK_PORTFOLIO, encoding="utf-8") as f:
        astock_pf = json.load(f)
    with open(FUND_PORTFOLIO, encoding="utf-8") as f:
        fund_pf = json.load(f)

    a_rows = build_astock_rows(astock_pf)
    f_rows = build_fund_rows(fund_pf)
    update_index(a_rows, f_rows)
