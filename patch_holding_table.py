"""
In-place 历史日报持仓表修复脚本（patch_holding_table.py）

⚠️ 重要：此脚本只做 HTML 文本层面的 in-place 替换，绝不重跑 generate_evening_report。
否则会冲掉 AI 生成的市场小结、晨报策略执行追踪、策略反思等核心内容。

修复内容：
1. 表头「浮盈亏」单列 → 「当日盈亏 + 总盈亏」两列
2. 重算每只标的的当日盈亏（用前一交易日 snapshot price + 当日 BUY 价兜底新仓）
3. 重算总盈亏（小数形式避免 1595% bug）

适用范围：只修持仓表那一段（<table>...</table>），保留报告其他所有 HTML。
"""
import json
import re
import sys
from pathlib import Path
from datetime import date

PORTFOLIO_PATH = Path("C:/Users/conniehe/.workbuddy/astock-simulator/portfolio.json")
FUND_PORTFOLIO_PATH = Path("C:/Users/conniehe/.workbuddy/astock-simulator/fund_portfolio.json")
REPORTS_DIR = Path("C:/temp/financial-report/reports")


def fmt_money(v: float) -> str:
    """金额格式化：+1,234.56 / -789.00 / 0.00"""
    sign = "+" if v >= 0 else "-"
    return f"{sign}{abs(v):,.2f}"


def fmt_pct(v: float) -> str:
    """百分比格式化（输入为小数如 0.0596 表示 5.96%）：+5.96%"""
    sign = "+" if v >= 0 else "-"
    return f"{sign}{abs(v) * 100:.2f}%"


def cls(v: float) -> str:
    """正负 CSS class（A 股红涨绿跌）：pos=红涨, neg=绿跌, flat=平"""
    if v > 0:
        return "pos"
    elif v < 0:
        return "neg"
    return "neu"


def compute_stock_holding_rows(target_date: str) -> str:
    """根据 portfolio.json 的 daily_records 生成 A 股持仓表的 <tbody> HTML"""
    with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
        p = json.load(f)

    records = p.get("daily_records", [])
    today_idx = None
    for i, r in enumerate(records):
        if r.get("date") == target_date:
            today_idx = i
            break
    if today_idx is None:
        return ""

    today = records[today_idx]
    prev = records[today_idx - 1] if today_idx > 0 else {}
    today_snap = today.get("positions_snapshot", {})
    prev_snap = prev.get("positions_snapshot", {})

    # 占比分母：用当日账户净值（持仓市值 + 现金）
    total_nav = today.get("nav", 0) or sum(s.get("market_value", 0) for s in today_snap.values())

    # 当日 BUY 价兜底新建仓
    today_buy_prices = {}
    for t in p.get("trades", []):
        if t.get("date") == target_date and t.get("action") == "BUY":
            today_buy_prices[t["code"]] = t.get("price", 0)

    rows = []
    for code, snap in today_snap.items():
        name = snap.get("name", code)
        shares = snap.get("shares", 0)
        avg_cost = snap.get("avg_cost", 0)
        price = snap.get("price", 0)
        market_value = snap.get("market_value", 0)
        pct_of_port = market_value / total_nav * 100 if total_nav > 0 else 0

        pnl = snap.get("pnl", 0)
        pnl_pct = (price - avg_cost) / avg_cost if avg_cost > 0 else 0  # 小数

        # 当日盈亏
        prev_price = prev_snap.get(code, {}).get("price")
        if not prev_price and code in today_buy_prices:
            prev_price = today_buy_prices[code]
        if prev_price and prev_price > 0:
            daily_pnl = (price - prev_price) * shares
            daily_pnl_pct = (price - prev_price) / prev_price
        else:
            daily_pnl = 0
            daily_pnl_pct = 0

        daily_cls = cls(daily_pnl)
        total_cls = cls(pnl)

        rows.append(
            f'<tr>\n            <td class="col-text">{name}<br><small style="color:#607a99">{code}</small></td>\n'
            f'            <td>{shares:,}</td><td>{avg_cost:.2f}</td>\n'
            f'            <td>{price:.2f}</td><td>{int(market_value):,}</td>\n'
            f'            <td>{pct_of_port:.1f}%</td>'
            f'<td><span class="{daily_cls}">{fmt_money(daily_pnl)}</span> <span class="{daily_cls}">{fmt_pct(daily_pnl_pct)}</span></td>'
            f'<td><span class="{total_cls}">{fmt_money(pnl)}</span> <span class="{total_cls}">{fmt_pct(pnl_pct)}</span></td></tr>'
        )

    return "".join(rows)


def compute_fund_holding_rows(target_date: str) -> str:
    """根据 fund_portfolio.json 的 daily_records 生成基金持仓表的 <tbody> HTML"""
    with open(FUND_PORTFOLIO_PATH, "r", encoding="utf-8") as f:
        fp = json.load(f)

    records = fp.get("daily_records", [])
    today_idx = None
    for i, r in enumerate(records):
        if r.get("date") == target_date:
            today_idx = i
            break
    if today_idx is None:
        return ""

    today = records[today_idx]
    prev = records[today_idx - 1] if today_idx > 0 else {}
    today_snap = today.get("positions_snapshot", {})
    prev_snap = prev.get("positions_snapshot", {})

    total_mv = sum(s.get("market_value", 0) for s in today_snap.values())
    total_nav = today.get("nav", 0) or total_mv

    today_buy_navs = {}
    for t in fp.get("trades", []):
        if t.get("date") == target_date and t.get("action") == "BUY":
            today_buy_navs[t["code"]] = t.get("nav") or t.get("price", 0)

    rows = []
    for code, snap in today_snap.items():
        name = snap.get("name", code)
        shares = snap.get("shares", 0)
        avg_nav = snap.get("avg_nav", 0)
        nav = snap.get("current_nav", 0)
        market_value = snap.get("market_value", 0)
        fund_type = snap.get("fund_type", "")
        pct_of_port = market_value / total_nav * 100 if total_nav > 0 else 0

        pnl = snap.get("pnl", 0)
        pnl_pct = (nav - avg_nav) / avg_nav if avg_nav > 0 else 0

        prev_nav = prev_snap.get(code, {}).get("current_nav")
        if not prev_nav and code in today_buy_navs:
            prev_nav = today_buy_navs[code]
        if prev_nav and prev_nav > 0:
            daily_pnl = (nav - prev_nav) * shares
            daily_pnl_pct = (nav - prev_nav) / prev_nav
        else:
            daily_pnl = 0
            daily_pnl_pct = 0

        daily_cls = cls(daily_pnl)
        total_cls = cls(pnl)
        ft_tag = fund_type_tag(fund_type)

        rows.append(
            f'<tr>\n                <td class="col-text">{name}<small style="color:#607a99"><br>{code}</small> <span class="fund-type-tag {ft_tag[1]}">{ft_tag[0]}</span></td>\n'
            f'                <td>{shares:,.2f}</td><td>{avg_nav:.4f}</td>\n'
            f'                <td>{nav:.4f}</td><td>{int(market_value):,}</td>\n'
            f'                <td>{pct_of_port:.1f}%</td>'
            f'<td><span class="{daily_cls}">{fmt_money(daily_pnl)}</span> <span class="{daily_cls}">{fmt_pct(daily_pnl_pct)}</span></td>'
            f'<td><span class="{total_cls}">{fmt_money(pnl)}</span> <span class="{total_cls}">{fmt_pct(pnl_pct)}</span></td></tr>'
        )

    return "".join(rows)


def fund_type_tag(fund_type: str):
    """根据 fund_type 返回 (display_name, css_class)"""
    mapping = {
        "A股宽基": ("A股宽基", "ft-a-share"),
        "A股行业": ("A股行业·ETF", "ft-a-share"),
        "黄金": ("黄金", "ft-gold"),
        "QDII": ("QDII", "ft-qdii"),
        "美股": ("美股·QDII", "ft-qdii"),
        "主动": ("主动管理", "ft-active"),
    }
    return mapping.get(fund_type, (fund_type, "ft-a-share"))


# A 股持仓表头（旧单列 + 错误的双列都要识别）
OLD_STOCK_HEAD = '<tr><th class="col-text">股票</th><th>持股数</th><th>成本价</th><th>收盘价</th><th>市值</th><th>占比</th><th>浮盈亏</th></tr>'
NEW_STOCK_HEAD = '<tr><th class="col-text">股票</th><th>持股数</th><th>成本价</th><th>收盘价</th><th>市值</th><th>占比</th><th>当日盈亏</th><th>总盈亏</th></tr>'
# 错误状态：双列 head 但行是单列 → 需要回退到单列 head
BAD_STOCK_HEAD = NEW_STOCK_HEAD  # 双列 head 但 snapshot 缺失时要回退

OLD_FUND_HEAD = '<tr><th class="col-text">基金</th><th>份额</th><th>成本净值</th><th>最新净值</th><th>市值</th><th>占比</th><th>浮盈亏</th></tr>'
NEW_FUND_HEAD = '<tr><th class="col-text">基金</th><th>份额</th><th>成本净值</th><th>最新净值</th><th>市值</th><th>占比</th><th>当日盈亏</th><th>总盈亏</th></tr>'


def patch_report(report_path: Path, target_date: str, dry_run: bool = False) -> dict:
    """修复单个日报 HTML"""
    content = report_path.read_text(encoding="utf-8")
    original = content
    changes = {"stock_head": False, "stock_rows": False, "fund_head": False, "fund_rows": False}

    # 先在原 content 上定位 A 股/基金 section 的边界（用 panel-stock / panel-fund 分隔）
    fund_panel_pos = content.find('id="panel-fund"')
    if fund_panel_pos < 0:
        fund_panel_pos = len(content)

    # A 股收盘持仓 section
    stock_section_start = content.find('<div class="section-title">收盘持仓</div>', 0, fund_panel_pos)

    # 基金收盘持仓 section
    fund_section_title_pos = content.find('<div class="section-title">收盘持仓</div>', fund_panel_pos)

    # === A 股部分 ===
    # 先尝试生成行数据；如果 snapshot 缺失则 A 股表回退到单列 head（避免 8 列 head 配 7 列 row 的错位 bug）
    stock_rows_html = compute_stock_holding_rows(target_date)
    if stock_rows_html and stock_section_start > 0:
        # 1. A 股表头（兼容 BAD/OLD 两种状态）
        if OLD_STOCK_HEAD in content:
            content = content.replace(OLD_STOCK_HEAD, NEW_STOCK_HEAD, 1)
            changes["stock_head"] = True
        elif BAD_STOCK_HEAD in content:
            # 已是双列 head，无需替换
            changes["stock_head"] = True
        # 2. A 股持仓行
        new_stock_section_start = content.find('<div class="section-title">收盘持仓</div>', 0, content.find('id="panel-fund"') if 'id="panel-fund"' in content else len(content))
        tbody_start = content.find("<tbody><tr>", new_stock_section_start)
        tbody_end = content.find("</tbody></table>", tbody_start)
        if tbody_start > 0 and tbody_end > 0:
            content = content[:tbody_start] + "<tbody>" + stock_rows_html + content[tbody_end:]
            changes["stock_rows"] = True
    else:
        # snapshot 缺失：A 股表回退到单列 head（如果当前是错误的双列状态）
        if BAD_STOCK_HEAD in content:
            # 找到 A 股表头位置（panel-fund 之前）
            fund_pos_check = content.find('id="panel-fund"')
            if fund_pos_check < 0:
                fund_pos_check = len(content)
            bad_head_pos = content.find(BAD_STOCK_HEAD, 0, fund_pos_check)
            if bad_head_pos > 0:
                content = content[:bad_head_pos] + OLD_STOCK_HEAD + content[bad_head_pos + len(BAD_STOCK_HEAD):]
                changes["stock_head"] = "rollback_to_single"

    # === 基金部分 ===
    fund_rows_html = compute_fund_holding_rows(target_date)
    if fund_rows_html and fund_section_title_pos > 0:
        # 3. 基金表头
        if OLD_FUND_HEAD in content:
            content = content.replace(OLD_FUND_HEAD, NEW_FUND_HEAD, 1)
            changes["fund_head"] = True
        # 4. 基金持仓行
        fund_panel_start = content.find('id="panel-fund"')
        if fund_panel_start > 0:
            fund_section_pos = content.find('<div class="section-title">收盘持仓</div>', fund_panel_start)
            if fund_section_pos > 0:
                tbody_start = content.find("<tbody><tr>", fund_section_pos)
                tbody_end = content.find("</tbody></table>", tbody_start)
                if tbody_start > 0 and tbody_end > 0:
                    content = content[:tbody_start] + "<tbody>" + fund_rows_html + content[tbody_end:]
                    changes["fund_rows"] = True

    if content == original:
        return {"path": str(report_path), "changed": False, "changes": changes}

    if not dry_run:
        report_path.write_text(content, encoding="utf-8")

    return {"path": str(report_path), "changed": True, "changes": changes, "dry_run": dry_run}


def main():
    # 默认只修 7/6 日报（其他日报的百分比已确认正常）
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    targets = args if args else ["20260706"]
    dry_run = "--dry-run" in sys.argv

    print(f"{'[DRY-RUN] ' if dry_run else ''}开始修复 {len(targets)} 个日报")
    print("=" * 70)

    for date_str in targets:
        # 转换 20260706 → 2026-07-06
        iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        report_path = REPORTS_DIR / f"{date_str}_日报.html"
        if not report_path.exists():
            print(f"❌ 文件不存在: {report_path}")
            continue

        print(f"\n📝 修复 {date_str}_日报.html (target_date={iso_date})")
        result = patch_report(report_path, iso_date, dry_run=dry_run)
        print(f"   结果: changed={result['changed']}")
        print(f"   细节: {result['changes']}")

    print("\n" + "=" * 70)
    print("✅ 修复完成")


if __name__ == "__main__":
    main()
