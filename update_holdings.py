#!/usr/bin/env python3
"""
2026-07-08 v7 彻底重写：当日盈亏 = 持仓浮动 + 当日SELL实现损益

吃过 5 次坑的最终结论（v7）：
1. intraday_snapshots.daily_pnl 在 A股日内减仓后不会按新份额重算（14:45 bug）
2. intraday_snapshots.daily_pnl_pct 在 基金 实际是"总盈亏%"不是"当日%"（数据源 bug）
3. v6 用 "当前份额 × (今日价 - 昨日价)" 自算，**漏算已卖出部分的实现损益**
4. v7 正确口径：当日盈亏 = 持仓浮动 + Σ(当日SELL shares × (sell_price - 昨收)) - Σ(当日SELL fees)
   - 持仓浮动 = current_shares × (today_close - yesterday_close)
   - SELL 实现损益从 portfolio.trades 提取（含 commission + stamp_tax + transfer_fee）
5. **清仓标的也必须显示**：002156 通富、688131 皓元清仓，v6 完全遗漏了 -2431 元亏损

与账户卡片"今日 NAV - 昨日 NAV"天然对齐（NAV 法天然包含卖出+持仓两部分）。
"""
import json
import re
from pathlib import Path

ASTOCK_PORTFOLIO = Path(r"C:\Users\conniehe\.workbuddy\astock-simulator\portfolio.json")
FUND_PORTFOLIO = Path(r"C:\Users\conniehe\.workbuddy\astock-simulator\fund_portfolio.json")
INDEX_HTML = Path(r"E:\temp\financial-report\index.html")


def fmt_money(v: float) -> str:
    """v8 统一：所有金额保留 2 位小数（含 ¥、千分位、正负号）"""
    sign = "+" if v >= 0 else "-"
    return f"{sign}¥{abs(v):,.2f}"


def fmt_pct(v: float) -> str:
    """0.0112 → '+1.12%'（小数入参，渲染时 ×100，统一 2 位小数）"""
    sign = "+" if v >= 0 else ""
    return f"{sign}{v*100:.2f}%"


def css_cls(v: float) -> str:
    return "up" if v > 0 else ("down" if v < 0 else "")


def get_today(portfolio: dict) -> str | None:
    """今日 = 最新交易日（优先取 intraday_snapshots 最后一条，兜底 records[-1]）。

    v10 修复（2026-07-09）：原逻辑取 snapshots[0]，但 snapshots 跨日累积不清理，
    导致今日判定为昨天的日期，进而 get_prev_snapshot 取错 record。
    现在取 snapshots[-1]（最新一条），保证拿到的是真正的今日。
    """
    snaps = portfolio.get("intraday_snapshots", [])
    if snaps:
        d = snaps[-1].get("date")
        if d:
            return d
    records = portfolio.get("daily_records", [])
    if records:
        return records[-1].get("date")
    return None


def get_prev_snapshot(portfolio: dict, code: str) -> dict:
    """从昨日日报拿该标的的 snapshot。

    v10 修复（2026-07-09）：原逻辑用 today 与 records[-1].date 比对，但 today
    被错判（见 get_today）会导致 prev_rec 错乱。现在以 records 日期为准：
    - 找到今日 record 索引，prev_rec = 前一条
    - 若 records[-1].date != today（今日日报尚未生成），prev_rec = records[-1]
    """
    records = portfolio.get("daily_records", [])
    if not records:
        return {}
    today = get_today(portfolio)
    # 找今日 record 的索引
    today_idx = None
    for i in range(len(records) - 1, -1, -1):
        if records[i].get("date") == today:
            today_idx = i
            break
    if today_idx is not None and today_idx > 0:
        prev_rec = records[today_idx - 1]
    elif today_idx is None:
        # 今日日报还没生成 → records[-1] 就是昨日
        prev_rec = records[-1]
    else:
        # today_idx == 0，没有更早的 record
        return {}
    return prev_rec.get("positions_snapshot", {}).get(code, {})


def get_today_sells(portfolio: dict, today: str | None) -> dict:
    """提取今日所有 SELL 交易，按 code 聚合。
    返回 {code: [{"shares": x, "price": y, "fees": z}, ...]}
    fees = commission + stamp_tax + transfer_fee
    """
    sells = {}
    if not today:
        return sells
    for t in portfolio.get("trades", []):
        if t.get("action") != "SELL":
            continue
        if t.get("date") != today:
            continue
        code = t.get("code")
        if not code:
            continue
        fees = (
            t.get("commission", 0)
            + t.get("stamp_tax", 0)
            + t.get("transfer_fee", 0)
            + t.get("fee", 0)  # 基金字段
        )
        sells.setdefault(code, []).append({
            "shares": t.get("shares", 0),
            "price": t.get("price") or t.get("nav", 0),
            "fees": fees,
        })
    return sells


def compute_total_pnl(portfolio: dict, code: str, pos: dict, current_price: float, is_fund: bool = False) -> tuple[float, float, float]:
    """计算持仓标的的总盈亏（v9 修正：绕开 amount 歧义，直接用 price/shares 重算）。
    返回 (total_pnl, total_pct, avg_cost)

    A股：基于完整交易历史
        BUY 成本  = Σ(price × shares + commission + transfer_fee)
        SELL 净额 = Σ(price × shares - commission - stamp_tax - transfer_fee)
        总盈亏 = SELL净额 + 持仓浮动市值 + Σ(分红) - BUY总成本

    基金：基于 pos.avg_nav（trades 历史可能有遗漏，fund_state.avg_nav 是权威值）
        总盈亏 = (current_nav - avg_nav) × shares
    """
    if is_fund:
        avg_cost = pos.get("avg_nav", 0)
        shares = pos.get("shares", 0)
        total_pnl = (current_price - avg_cost) * shares
        total_pct = (current_price - avg_cost) / avg_cost if avg_cost > 0 else 0
        return total_pnl, total_pct, avg_cost

    # A股：v9 基于 price/shares + 独立费用字段重算（绕开 amount 字段语义在不同时期不一致的问题）
    trades = [t for t in portfolio.get("trades", []) if t.get("code") == code]
    buy_total = sum(
        t.get("price", 0) * t.get("shares", 0) + t.get("commission", 0) + t.get("transfer_fee", 0)
        for t in trades if t.get("action") == "BUY"
    )
    sell_net = sum(
        t.get("price", 0) * t.get("shares", 0) - t.get("commission", 0) - t.get("stamp_tax", 0) - t.get("transfer_fee", 0)
        for t in trades if t.get("action") == "SELL"
    )
    div_total = sum(t.get("amount", 0) for t in trades if t.get("action") == "DIVIDEND")

    shares = pos.get("shares", 0)
    holding_mv = shares * current_price
    total_pnl = sell_net + holding_mv + div_total - buy_total
    buy_shares = sum(t.get("shares", 0) for t in trades if t.get("action") == "BUY")
    avg_cost = buy_total / buy_shares if buy_shares > 0 else 0
    total_pct = total_pnl / buy_total if buy_total > 0 else 0
    return total_pnl, total_pct, avg_cost


def compute_daily_pnl_astock(
    portfolio: dict, code: str, pos: dict, today_price: float, today: str | None,
    today_sells: dict,
) -> tuple[float | None, float | None, bool]:
    """计算 A股当日盈亏（v7：持仓浮动 + SELL 实现损益）。
    返回 (daily_pnl, daily_pct, is_new_position)

    正确口径：
      当日盈亏 = 持仓浮动 + SELL 实现损益
      持仓浮动 = current_shares × (today_close - yesterday_close)
      SELL 实现损益 = Σ(sell_shares × (sell_price - yesterday_close)) - Σ(sell_fees)

    daily_pct 含义：相对"昨日收盘 × 昨日份额"的涨跌幅（用于展示）
    """
    shares = pos.get("shares", 0)
    buy_date = pos.get("buy_date")
    is_new_position = bool(today) and buy_date == today

    prev_snap = get_prev_snapshot(portfolio, code)
    prev_price = (
        prev_snap.get("price")
        or prev_snap.get("current_price")
        or prev_snap.get("avg_cost", 0)
    )

    if is_new_position:
        # 新建仓：v11.2 修复 — 新建仓也有当日盈亏，基准用 avg_cost（含手续费摊薄）
        # 当日盈亏 = 持仓浮动（today_price - avg_cost）× shares + SELL 实现损益
        avg_cost = pos.get("avg_cost", 0)
        sells_today = today_sells.get(code, [])
        if avg_cost > 0 and today_price > 0:
            holding_pnl = (today_price - avg_cost) * shares
            realized = sum(s["shares"] * (s["price"] - avg_cost) - s["fees"] for s in sells_today)
            daily_pnl = holding_pnl + realized
            daily_pct = daily_pnl / (avg_cost * shares) if shares > 0 else 0
            return daily_pnl, daily_pct, True
        return None, None, True

    if prev_price <= 0:
        return None, None, False  # 无 prev 数据

    # 持仓浮动
    holding_pnl = (today_price - prev_price) * shares

    # SELL 实现损益
    sells_today = today_sells.get(code, [])
    realized = sum(s["shares"] * (s["price"] - prev_price) - s["fees"] for s in sells_today)

    daily_pnl = holding_pnl + realized

    # daily_pct：相对昨日市值的涨跌幅（昨日市值 = 昨日份额 × 昨日价）
    # 昨日份额需要从昨日 snapshot 拿，否则用当前份额+SELL 份额近似
    prev_shares = prev_snap.get("shares", shares + sum(s["shares"] for s in sells_today))
    prev_value = prev_shares * prev_price
    daily_pct = daily_pnl / prev_value if prev_value > 0 else 0

    return daily_pnl, daily_pct, False


def compute_daily_pnl_cleared(
    portfolio: dict, code: str, today: str | None, today_sells: dict,
    is_fund: bool = False,
) -> float | None:
    """计算今日清仓标的的当日实现损益。
    清仓标的不在 positions 里，但当日有 SELL 交易全部清仓。
    实现损益 = Σ(sell_shares × (sell_price - yesterday_close)) - Σ(sell_fees)
    """
    sells_today = today_sells.get(code, [])
    if not sells_today:
        return None

    prev_snap = get_prev_snapshot(portfolio, code)
    if is_fund:
        prev_price = (
            prev_snap.get("current_nav")
            or prev_snap.get("price")
            or prev_snap.get("current_price")
            or prev_snap.get("avg_nav", 0)
        )
    else:
        prev_price = (
            prev_snap.get("price")
            or prev_snap.get("current_price")
            or prev_snap.get("avg_cost", 0)
        )

    if prev_price <= 0:
        return None

    realized = sum(s["shares"] * (s["price"] - prev_price) - s["fees"] for s in sells_today)
    return realized


def compute_daily_pnl_fund(
    portfolio: dict, code: str, pos: dict, today_nav: float, today: str | None,
    today_sells: dict,
) -> tuple[float | None, float | None, bool]:
    """计算基金当日盈亏（v7）"""
    shares = pos.get("shares", 0)
    buy_date = pos.get("buy_date") or pos.get("purchase_date")
    is_new_position = bool(today) and buy_date == today

    prev_snap = get_prev_snapshot(portfolio, code)
    prev_nav = (
        prev_snap.get("current_nav")
        or prev_snap.get("price")
        or prev_snap.get("current_price")
        or prev_snap.get("avg_nav", 0)
    )

    if is_new_position:
        # 新建仓：v11.2 修复 — 新建仓也有当日盈亏，基准用 avg_nav（含手续费摊薄）
        avg_nav = pos.get("avg_nav") or pos.get("avg_cost", 0)
        sells_today = today_sells.get(code, [])
        if avg_nav > 0 and today_nav > 0:
            holding_pnl = (today_nav - avg_nav) * shares
            realized = sum(s["shares"] * (s["price"] - avg_nav) - s["fees"] for s in sells_today)
            daily_pnl = holding_pnl + realized
            daily_pct = daily_pnl / (avg_nav * shares) if shares > 0 else 0
            return daily_pnl, daily_pct, True
        return None, None, True

    if prev_nav <= 0:
        return None, None, False

    # 持仓浮动
    holding_pnl = (today_nav - prev_nav) * shares

    # SELL 实现损益
    sells_today = today_sells.get(code, [])
    realized = sum(s["shares"] * (s["price"] - prev_nav) - s["fees"] for s in sells_today)

    daily_pnl = holding_pnl + realized

    prev_shares = prev_snap.get("shares", shares + sum(s["shares"] for s in sells_today))
    prev_value = prev_shares * prev_nav
    daily_pct = daily_pnl / prev_value if prev_value > 0 else 0

    return daily_pnl, daily_pct, False


def build_astock_rows(portfolio: dict) -> str:
    positions = portfolio.get("positions", {})
    today = get_today(portfolio)
    today_sells = get_today_sells(portfolio, today)

    rows = []
    debug_lines = []
    for code, pos in positions.items():
        name = pos.get("name", "")
        shares = pos.get("shares", 0)

        price = (
            pos.get("current_price")
            or pos.get("avg_cost", 0)
        )
        mv = price * shares

        # 总盈亏：基于完整交易历史（v8，与已清仓标的口径一致）
        pnl, pnl_pct, avg_cost = compute_total_pnl(portfolio, code, pos, price)

        # 当日盈亏：持仓浮动 + SELL 实现损益（v7）
        daily_pnl, daily_pct, is_new = compute_daily_pnl_astock(
            portfolio, code, pos, price, today, today_sells
        )

        # 渲染
        arrow = "↑" if price > avg_cost else ("↓" if price < avg_cost else "")
        cls_price = css_cls(price - avg_cost)
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
                f"<small>{fmt_pct(daily_pct or 0)}</small></td>"
            )

        rows.append(
            f"<tr><td class='hide-mobile'><span class='stock-code'>{code}</span></td>"
            f"<td class='col-text'><span class='stock-name'>{name}</span></td>"
            f"<td>{shares:,} 股</td>"
            f"<td class='hide-mobile'>¥{avg_cost:.2f}</td>"
            f"<td class='{cls_price}'>¥{price:.2f}{arrow}</td>"
            f"<td>¥{mv:,.2f}</td>"
            f"{daily_cell}"
            f"<td class='{cls_pnl}'>{fmt_money(pnl)}<br><small>{fmt_pct(pnl_pct)}</small></td></tr>"
        )

        # debug：拆解持仓浮动 vs SELL 实现损益
        holding_part = 0
        realized_part = 0
        prev_snap = get_prev_snapshot(portfolio, code)
        prev_price = prev_snap.get("price") or prev_snap.get("current_price") or prev_snap.get("avg_cost", 0)
        if prev_price > 0 and not is_new:
            holding_part = (price - prev_price) * shares
            realized_part = sum(s["shares"] * (s["price"] - prev_price) - s["fees"] for s in today_sells.get(code, []))
        debug_lines.append(
            f"  {name}({code}) shares={shares} price={price} prev_close={prev_price:.2f} | "
            f"当日[持仓浮动={holding_part:+.2f} SELL实现={realized_part:+.2f}]={daily_pnl} | "
            f"总盈亏={pnl:+.2f}({pnl_pct*100:+.2f}%)"
        )

    # 注：历史清仓标的已移至独立"已清仓标的"模块（cleared_positions.py 维护）

    print("📊 A股 当日盈亏+总盈亏自检（v8 持仓浮动+SELL实现+总盈亏交易历史口径）：")
    for line in debug_lines:
        print(line)
    return "\n        ".join(rows)


def build_fund_rows(portfolio: dict) -> str:
    positions = portfolio.get("positions", {})
    today = get_today(portfolio)
    today_sells = get_today_sells(portfolio, today)

    rows = []
    debug_lines = []
    for code, pos in positions.items():
        name = pos.get("name", "")
        shares = pos.get("shares", 0)

        cur_nav = (
            pos.get("current_nav")
            or pos.get("avg_nav", 0)
        )
        mv = cur_nav * shares

        # 总盈亏：基金用 avg_nav（trades 历史不完整，详见 compute_total_pnl 注释）
        pnl, pnl_pct, avg_nav = compute_total_pnl(portfolio, code, pos, cur_nav, is_fund=True)

        # 当日盈亏：持仓浮动 + SELL 实现损益（v7）
        daily_pnl, daily_pct, is_new = compute_daily_pnl_fund(
            portfolio, code, pos, cur_nav, today, today_sells
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
                f"<small>{fmt_pct(daily_pct or 0)}</small></td>"
            )

        rows.append(
            f"<tr><td class='hide-mobile'><span class='stock-code'>{code}</span></td>"
            f"<td class='col-text'><span class='stock-name'>{name}</span></td>"
            f"<td class='hide-mobile col-text'>{ft_display}</td>"
            f"<td>{shares:,.0f} 份</td>"
            f"<td class='hide-mobile'>¥{avg_nav:.4f}</td>"
            f"<td class='{cls_price}'>¥{cur_nav:.4f}{arrow}</td>"
            f"<td>¥{mv:,.2f}</td>"
            f"{daily_cell}"
            f"<td class='{cls_pnl}'>{fmt_money(pnl)}<br><small>{fmt_pct(pnl_pct)}</small></td></tr>"
        )

        # debug：拆解
        holding_part = 0
        realized_part = 0
        prev_snap = get_prev_snapshot(portfolio, code)
        prev_nav_val = prev_snap.get("current_nav") or prev_snap.get("price") or prev_snap.get("current_price") or prev_snap.get("avg_nav", 0)
        if prev_nav_val > 0 and not is_new:
            holding_part = (cur_nav - prev_nav_val) * shares
            realized_part = sum(s["shares"] * (s["price"] - prev_nav_val) - s["fees"] for s in today_sells.get(code, []))
        debug_lines.append(
            f"  {name}({code}) shares={shares} nav={cur_nav} prev_nav={prev_nav_val:.4f} | "
            f"当日[持仓浮动={holding_part:+.2f} SELL实现={realized_part:+.2f}]={daily_pnl} | "
            f"总盈亏={pnl:+.2f}({pnl_pct*100:+.2f}%)"
        )

    # 注：历史清仓标的已移至独立"已清仓标的"模块（cleared_positions.py 维护）

    # ⏳ 待确认订单（场外基金 T+N，2026-07-17 新增）
    # pending_orders 里的订单：资金已扣但份额未确认，不能算入 NAV（避免与 cash 重复）
    # 但必须在持仓表里可见，让用户看到"钱去了哪"
    pending_orders = portfolio.get("pending_orders", [])
    pending_rows = []
    for o in pending_orders:
        if o.get("status") != "pending_confirm":
            continue
        pcode = o.get("code", "")
        pname = o.get("name", "")
        pamount = o.get("amount", 0)
        pfee = o.get("purchase_fee", 0)
        pinvest = o.get("invest_amount", pamount - pfee)
        pnav = o.get("order_nav", 0)
        pft = o.get("fund_type", "")
        pft_display = pft if "·" in pft or len(pft) > 6 else f"{pft}·场外"
        pconfirm = o.get("confirm_date", "—")
        pending_rows.append(
            f"<tr style='background:rgba(245,158,11,0.08)'>"
            f"<td class='hide-mobile'><span class='stock-code'>{pcode}</span></td>"
            f"<td class='col-text'><span class='stock-name'>{pname}</span> "
            f"<span style='color:#f59e0b;font-size:0.78rem'>⏳ 待确认</span></td>"
            f"<td class='hide-mobile col-text'>{pft_display}</td>"
            f"<td><span style='color:#f59e0b'>⏳ 待确认</span></td>"
            f"<td class='hide-mobile'>¥{pnav:.4f}<br><small style='color:#94a3b8'>下单参考</small></td>"
            f"<td><span style='color:#94a3b8'>—</span></td>"
            f"<td>¥{pinvest:,.2f}<br><small style='color:#94a3b8'>申购金额</small></td>"
            f"<td colspan='2'><span style='color:#f59e0b'>⏳ T+N 待确认<br>"
            f"<small>预计 {pconfirm} 入持仓</small></span></td></tr>"
        )

    print("\n📊 基金 当日盈亏+总盈亏自检（v8 持仓浮动+SELL实现+总盈亏交易历史口径）：")
    for line in debug_lines:
        print(line)
    if pending_rows:
        print(f"⏳ 待确认订单 {len(pending_rows)} 笔已追加到持仓表")
    return "\n        ".join(rows + pending_rows)


def calc_nav(portfolio: dict, price_field_a: str = "current_price", price_field_f: str = "current_nav") -> tuple[float, float]:
    """计算账户 NAV 和现金。返回 (nav, cash)

    v9 (2026-07-17)：加入 pending_orders 待确认订单价值。
    场外基金 T+N 申购后资金已从 cash 扣除，但份额未入持仓。
    若 NAV 不算 pending_orders，会造成 NAV 虚低（差值 = 待确认金额）。
    pending_orders 按 invest_amount 计入（份额未确认，按已投入金额算）。
    """
    cash = portfolio.get("cash", 0)
    total_mv = 0
    for code, p in portfolio.get("positions", {}).items():
        shares = p.get("shares", 0)
        # 兼容 A股/基金
        price = p.get(price_field_a) or p.get(price_field_f) or p.get("avg_cost") or p.get("avg_nav") or 0
        total_mv += shares * price

    # 待确认订单按 invest_amount 计入 NAV（资金已扣但份额未确认的中间态）
    pending_value = sum(
        o.get("invest_amount", 0) for o in portfolio.get("pending_orders", [])
        if o.get("status") == "pending_confirm"
    )
    if pending_value > 0:
        total_mv += pending_value
    return cash + total_mv, cash


def get_prev_day_nav(portfolio: dict) -> float | None:
    """取昨日 NAV：与 get_prev_snapshot 同步逻辑（v10 修复）。

    判定：找到今日 record 索引，prev = 前一条；若今日 record 未生成，prev = records[-1]。
    """
    records = portfolio.get("daily_records", [])
    if not records:
        return None
    today = get_today(portfolio)
    today_idx = None
    for i in range(len(records) - 1, -1, -1):
        if records[i].get("date") == today:
            today_idx = i
            break
    if today_idx is not None and today_idx > 0:
        return records[today_idx - 1].get("nav")
    elif today_idx is None:
        return records[-1].get("nav")
    else:
        return None


def update_account_cards(astock_pf: dict, fund_pf: dict, html: str) -> str:
    """自动重算 4 个账户卡片：A股、基金、合并、可用资金。
    v8 原则：固定格式，禁止自发挥。每个卡片只输出"标签 + 净值 + 累计/今日盈亏"三行。
    """
    initial = 500000  # 初始资金

    # === A股 ===
    a_nav, a_cash = calc_nav(astock_pf, "current_price")
    a_prev = get_prev_day_nav(astock_pf)
    a_total_pnl = a_nav - initial
    a_total_pct = a_total_pnl / initial
    a_today_pnl = (a_nav - a_prev) if a_prev else 0
    a_today_pct = (a_today_pnl / a_prev) if a_prev else 0

    # === 基金 ===
    f_nav, f_cash = calc_nav(fund_pf, "current_nav")
    f_prev = get_prev_day_nav(fund_pf)
    f_total_pnl = f_nav - initial
    f_total_pct = f_total_pnl / initial
    f_today_pnl = (f_nav - f_prev) if f_prev else 0
    f_today_pct = (f_today_pnl / f_prev) if f_prev else 0

    # === 合并 ===
    combined_nav = a_nav + f_nav
    combined_total_pnl = combined_nav - initial * 2
    combined_total_pct = combined_total_pnl / (initial * 2)

    # 渲染辅助：所有金额统一 2 位小数
    def fmt_card_money(v: float) -> str:
        sign = "+" if v >= 0 else "-"
        return f"{sign}¥{abs(v):,.2f}"

    def fmt_card_pct(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v*100:.2f}%"

    def cls(v: float) -> str:
        return "up" if v > 0 else ("down" if v < 0 else "")

    # === 替换 A股卡片（固定三行：标签 + 净值 + 累计/今日盈亏，无脚注）===
    a_value = f'<div class="value">¥{a_nav:,.2f}</div>'
    a_change = (
        f'<div class="change">'
        f'<span class="{cls(a_total_pnl)}">累计 {fmt_card_money(a_total_pnl)} ({fmt_card_pct(a_total_pct)})</span> · '
        f'<span class="{cls(a_today_pnl)}">今日 {fmt_card_money(a_today_pnl)} ({fmt_card_pct(a_today_pct)})</span>'
        f'</div>'
    )
    # 移除已存在的脚注（幂等）
    html = re.sub(
        r'(A股账户净值</div>\s*<div class="value">[^<]+</div>\s*<div class="change[^"]*">.*?</div>)\s*<div class="change[^"]*" style="font-size:11px[^"]*">[^<]*</div>',
        r'\1',
        html, count=1, flags=re.DOTALL,
    )
    html = re.sub(
        r'(A股账户净值</div>)\s*<div class="value">[^<]+</div>\s*<div class="change[^"]*">.*?</div>',
        rf'\1\n      {a_value}\n      {a_change}',
        html, count=1, flags=re.DOTALL,
    )

    # === 替换 基金卡片（固定三行）===
    f_value = f'<div class="value">¥{f_nav:,.2f}</div>'
    f_change = (
        f'<div class="change">'
        f'<span class="{cls(f_total_pnl)}">累计 {fmt_card_money(f_total_pnl)} ({fmt_card_pct(f_total_pct)})</span> · '
        f'<span class="{cls(f_today_pnl)}">今日 {fmt_card_money(f_today_pnl)} ({fmt_card_pct(f_today_pct)})</span>'
        f'</div>'
    )
    html = re.sub(
        r'(基金账户净值</div>)\s*<div class="value">[^<]+</div>\s*<div class="change[^"]*">.*?</div>',
        rf'\1\n      {f_value}\n      {f_change}',
        html, count=1, flags=re.DOTALL,
    )

    # === 替换 合并卡片（固定三行）===
    c_value = f'<div class="value">¥{combined_nav:,.2f}</div>'
    c_change = (
        f'<div class="change">'
        f'<span class="{cls(combined_total_pnl)}">累计 {fmt_card_money(combined_total_pnl)} ({fmt_card_pct(combined_total_pct)})</span>'
        f'</div>'
    )
    html = re.sub(
        r'(合并总净值</div>)\s*<div class="value">[^<]+</div>\s*<div class="change[^"]*">.*?</div>',
        rf'\1\n      {c_value}\n      {c_change}',
        html, count=1, flags=re.DOTALL,
    )

    # === 可用资金卡片（固定两行：标签 + 金额，无任何额外文案）===
    cash_value = f'<div class="value">¥{a_cash:,.2f} / ¥{f_cash:,.2f}</div>'
    # 移除可能存在的"日报归档"等自发挥 change 行
    html = re.sub(
        r'(A股可用 / 基金可用</div>)\s*<div class="value">[^<]+</div>(\s*<div class="change[^"]*">.*?</div>)?',
        rf'\1\n      {cash_value}',
        html, count=1, flags=re.DOTALL,
    )

    # 自检输出
    print(f"\n📊 账户卡片自算（v8 固定格式）：")
    print(f"  A股: NAV ¥{a_nav:,.2f} | 昨日 ¥{a_prev:,.2f} | 今日 {fmt_card_money(a_today_pnl)} ({fmt_card_pct(a_today_pct)}) | 累计 {fmt_card_money(a_total_pnl)} ({fmt_card_pct(a_total_pct)})")
    print(f"  基金: NAV ¥{f_nav:,.2f} | 昨日 ¥{f_prev:,.2f} | 今日 {fmt_card_money(f_today_pnl)} ({fmt_card_pct(f_today_pct)}) | 累计 {fmt_card_money(f_total_pnl)} ({fmt_card_pct(f_total_pct)})")
    print(f"  合并: NAV ¥{combined_nav:,.2f} | 累计 {fmt_card_money(combined_total_pnl)} ({fmt_card_pct(combined_total_pct)})")

    return html


def update_index(astock_rows: str, fund_rows: str, astock_pf: dict, fund_pf: dict):
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

    # 自动重算账户卡片
    html = update_account_cards(astock_pf, fund_pf, html)

    INDEX_HTML.write_text(html, encoding="utf-8")
    print(f"\n✅ index.html 已更新（持仓表 + 账户卡片，v8 固定格式）")


if __name__ == "__main__":
    with open(ASTOCK_PORTFOLIO, encoding="utf-8") as f:
        astock_pf = json.load(f)
    with open(FUND_PORTFOLIO, encoding="utf-8") as f:
        fund_pf = json.load(f)

    a_rows = build_astock_rows(astock_pf)
    f_rows = build_fund_rows(fund_pf)
    update_index(a_rows, f_rows, astock_pf, fund_pf)
