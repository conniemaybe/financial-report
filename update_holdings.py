#!/usr/bin/env python3
"""
2026-07-06 v3 同步 index.html 持仓表（A 股 + 基金）

数据前提（已由 normalize_pnl_pct.py 完成）：
  - positions[code].pnl_pct 已全部归一为小数形式（0.1595 表示 15.95%）
  - 不再有「百分号前数字」（15.95 表示 15.95%）混用

用法：
    python update_holdings.py
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


def build_astock_rows(portfolio: dict) -> str:
    records = portfolio.get("daily_records", [])
    today_rec = records[-1] if records else None
    prev_rec = records[-2] if len(records) >= 2 else None

    # 2026-07-08 修复：新建仓判定改用 pos.buy_date == records[-1].date
    # 不能再用 trades.action == BUY + trades.date == today_str（today_str 是当前真实日期，不是 daily_records 的日期）
    # daily_records 最新一条才是网站展示的"今日"，buy_date 和它对齐才是新建仓
    latest_record_date = records[-1].get("date") if records else None
    positions = portfolio.get("positions", {})

    # 2026-07-07 v4：优先用 intraday_snapshots[-1] 的实时价格
    # 盘中 10:00/10:30/13:30/14:45 同步后，网站立即显示最新价
    intraday = portfolio.get("intraday_snapshots", [])
    latest_intraday = intraday[-1] if intraday else None
    intraday_positions = (latest_intraday or {}).get("positions", {})

    rows = []
    for code, pos in positions.items():
        name = pos.get("name", "")
        shares = pos.get("shares", 0)
        cost = pos.get("avg_cost", 0)
        # 2026-07-08 修复：buy_date 对齐 daily_records 最新日期 = 新建仓
        is_new_position = (
            pos.get("buy_date") == latest_record_date
            or pos.get("buy_date") is None  # 老仓位无 buy_date 当作非新建仓
            and pos.get("buy_date") == latest_record_date
        )
        # 优先用 intraday_snapshots 实时价，回退到 positions 的 current_price
        intraday_pos = intraday_positions.get(code, {})
        price = (
            intraday_pos.get("current_price")
            or pos.get("current_price")
            or cost
        )
        mv = intraday_pos.get("market_value") or pos.get("market_value") or price * shares
        pnl = intraday_pos.get("pnl") or pos.get("pnl", 0)
        pnl_pct = intraday_pos.get("pnl_pct") or pos.get("pnl_pct", 0)
        daily_pnl = intraday_pos.get("daily_pnl")
        daily_pct = intraday_pos.get("daily_pnl_pct")

        # 2026-07-08 修复：新建仓直接显示"— 新建仓"，不算 daily_pnl
        # 之前的逻辑用 prev_rec.snapshot.price，但新建仓 prev_rec 里根本没有这条
        # 导致 prev_price=0 → 用 cost 兜底 → daily_pnl = 总盈亏（错误！）
        if is_new_position:
            daily_pnl = None  # 标记为新建仓
            daily_pct = None
        elif daily_pnl is None or (
            daily_pnl is not None and abs(daily_pnl - pnl) < 0.01
        ):
            # 当日盈亏兜底（intraday_snapshots 没算 或 与总盈亏相等时重算）
            prev_snap = (prev_rec or {}).get("positions_snapshot", {}).get(code, {})
            prev_price = prev_snap.get("price") or prev_snap.get("current_price") or prev_snap.get("avg_cost", 0)
            if prev_price > 0:
                daily_pnl = (price - prev_price) * shares
                daily_pct = (price - prev_price) / prev_price
            else:
                daily_pnl, daily_pct = 0, 0

        arrow = "↑" if price > cost else ("↓" if price < cost else "")
        cls_price = css_cls(price - cost)
        cls_pnl = css_cls(pnl)
        cls_dly = css_cls(daily_pnl or 0)
        if daily_pnl is None:
            daily_cell = "<td class=''><span style='color:#64748b'>— 新建仓</span></td>"
        else:
            daily_cell = (
                f"<td class='{cls_dly}'>{fmt_money(daily_pnl)}<br><small>{fmt_pct(daily_pct)}</small></td>"
                if daily_pnl != 0 or daily_pct != 0
                else "<td class=''><span style='color:#64748b'>— 无变化</span></td>"
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
    records = portfolio.get("daily_records", [])
    today_rec = records[-1] if records else None
    prev_rec = records[-2] if len(records) >= 2 else None

    # 2026-07-08 修复：新建仓判定改用 pos.buy_date == records[-1].date（同 A股）
    latest_record_date = records[-1].get("date") if records else None
    positions = portfolio.get("positions", {})

    # 2026-07-07 v4：优先用 intraday_snapshots[-1] 的实时净值
    intraday = portfolio.get("intraday_snapshots", [])
    latest_intraday = intraday[-1] if intraday else None
    intraday_positions = (latest_intraday or {}).get("positions", {})

    rows = []
    for code, pos in positions.items():
        name = pos.get("name", "")
        shares = pos.get("shares", 0)
        avg_nav = pos.get("avg_nav", 0)
        # 2026-07-08 修复：新建仓判定（buy_date 对齐 daily_records 最新日期）
        is_new_position = pos.get("buy_date") == latest_record_date
        # 优先用 intraday_snapshots 实时净值，回退到 positions
        intraday_pos = intraday_positions.get(code, {})
        cur_nav = (
            intraday_pos.get("current_nav")
            or pos.get("current_nav")
            or avg_nav
        )
        mv = intraday_pos.get("market_value") or pos.get("market_value") or cur_nav * shares
        pnl = intraday_pos.get("pnl") or pos.get("pnl", 0)
        pnl_pct = intraday_pos.get("pnl_pct") or pos.get("pnl_pct", 0)
        daily_pnl = intraday_pos.get("daily_pnl")
        daily_pct = intraday_pos.get("daily_pnl_pct")

        # fund_type 字段在 portfolio.json 里可能简写，扩展显示
        ft_raw = pos.get("fund_type", "ETF")
        ft_display = ft_raw if "·" in ft_raw or len(ft_raw) > 6 else f"{ft_raw}·ETF"

        # 2026-07-08 修复：新建仓直接显示"— 新申购"，不算 daily_pnl（同 A股逻辑）
        if is_new_position:
            daily_pnl = None
            daily_pct = None
        elif daily_pnl is None or (
            daily_pnl is not None and abs(daily_pnl - pnl) < 0.01
        ):
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
        else:
            daily_cell = (
                f"<td class='{cls_dly}'>{fmt_money(daily_pnl)}<br><small>{fmt_pct(daily_pct)}</small></td>"
                if daily_pnl != 0 or daily_pct != 0
                else "<td class=''><span style='color:#64748b'>— 无变化</span></td>"
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
