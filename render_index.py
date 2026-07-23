#!/usr/bin/env python3
"""render_index.py — Jinja2 全量渲染 index.html（架构审查 Step 3，2026-07-23）

设计理念：
    **每次渲染都是从头重画**，彻底废弃 re.sub 字符串替换。
    4 张卡片 + 持仓表用 Jinja2 全量渲染，
    其他部分（chart_js / trades_js / decision / cleared / allReports）
    从现有 index.html 提取保留（由 sync_index / html_report 等脚本维护）。

为什么这样设计：
    1. 卡片+持仓表是"最频繁出错"的部分（re.sub 静默失败的重灾区）
    2. chart/trades 等数据由其他专门脚本维护，重写它们工作量太大且收益低
    3. Jinja2 全量渲染 = 这些核心字段"物理上不可能"出错

数据流：
    portfolio.json/fund_portfolio.json
        ↓ schemas.load_*_portfolio()  (pydantic 校验)
    Portfolio 对象
        ↓ compute_*()  (盈亏计算，复用 update_holdings.py)
    渲染变量
        ↓ Jinja2 模板 + 提取旧 index.html 的其他部分
    新 index.html
        ↓ self-check
    写入磁盘

用法：
    python render_index.py          # 渲染并写入
    python render_index.py --check  # 只校验不写入
"""
import sys
import re
import json
from pathlib import Path
from datetime import datetime

# 把 scripts 目录加入 path
SCRIPTS_DIR = Path(r"C:\Users\conniehe\.workbuddy\skills\astock-simulator\scripts")
sys.path.insert(0, str(SCRIPTS_DIR))
# update_holdings.py 在 financial-report 目录
FIN_REPORT_DIR = Path(r"E:\temp\financial-report")
sys.path.insert(0, str(FIN_REPORT_DIR))

from schemas import load_astock_portfolio, load_fund_portfolio, assert_self_consistent  # noqa: E402
from jinja2 import Environment, FileSystemLoader  # noqa: E402

INDEX_HTML = FIN_REPORT_DIR / "index.html"
TEMPLATE_DIR = FIN_REPORT_DIR / "templates"
INITIAL_CAPITAL = 500_000


def fmt_money(v: float) -> str:
    """金额格式化：含 ¥、千分位、正负号"""
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "-"
    return f"{sign}¥{abs(v):,.2f}"


def fmt_pct(v: float) -> str:
    """百分比格式化：小数 → 百分号"""
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "-"
    return f"{sign}{abs(v)*100:.2f}%"


def css_cls(v: float) -> str:
    """盈亏颜色：正红负绿零灰（A股惯例）"""
    if v is None or v == 0:
        return ""
    return "up" if v > 0 else "down"


def compute_card_vars(a_pf, f_pf):
    """计算 4 张卡片需要的所有变量。
    当日盈亏口径与 update_holdings.update_account_cards 完全一致：NAV - 昨日NAV。
    直接复用 update_holdings.get_prev_day_nav，避免口径分裂。
    """
    import update_holdings as uh

    a_pf_dict = a_pf.model_dump()
    f_pf_dict = f_pf.model_dump()

    # === A股 ===
    a_nav = a_pf.nav
    a_cash = a_pf.cash
    a_total_pnl = a_nav - INITIAL_CAPITAL
    a_total_pct = a_total_pnl / INITIAL_CAPITAL
    a_prev = uh.get_prev_day_nav(a_pf_dict)
    if a_prev is None:
        a_prev = a_nav  # 无历史记录时今日盈亏=0
    a_today_pnl = (a_nav - a_prev) if a_prev else 0
    a_today_pct = (a_today_pnl / a_prev) if a_prev else 0

    # === 基金 ===
    f_nav = f_pf.nav
    f_cash = f_pf.cash
    f_total_pnl = f_nav - INITIAL_CAPITAL
    f_total_pct = f_total_pnl / INITIAL_CAPITAL
    f_prev = uh.get_prev_day_nav(f_pf_dict)
    if f_prev is None:
        f_prev = f_nav
    f_today_pnl = (f_nav - f_prev) if f_prev else 0
    f_today_pct = (f_today_pnl / f_prev) if f_prev else 0

    # === 合并 ===
    combined_nav = a_nav + f_nav
    combined_total_pnl = combined_nav - 2 * INITIAL_CAPITAL
    combined_total_pct = combined_total_pnl / (2 * INITIAL_CAPITAL)

    return {
        # A股
        'a_nav': a_nav, 'a_cash': a_cash,
        'a_total_pnl_str': fmt_money(a_total_pnl), 'a_total_pct_str': fmt_pct(a_total_pct),
        'a_total_cls': css_cls(a_total_pnl),
        'a_today_pnl_str': fmt_money(a_today_pnl), 'a_today_pct_str': fmt_pct(a_today_pct),
        'a_today_cls': css_cls(a_today_pnl),
        # 基金
        'f_nav': f_nav, 'f_cash': f_cash,
        'f_total_pnl_str': fmt_money(f_total_pnl), 'f_total_pct_str': fmt_pct(f_total_pct),
        'f_total_cls': css_cls(f_total_pnl),
        'f_today_pnl_str': fmt_money(f_today_pnl), 'f_today_pct_str': fmt_pct(f_today_pct),
        'f_today_cls': css_cls(f_today_pnl),
        # 合并
        'combined_nav': combined_nav,
        'combined_total_pnl_str': fmt_money(combined_total_pnl),
        'combined_total_pct_str': fmt_pct(combined_total_pct),
        'combined_total_cls': css_cls(combined_total_pnl),
    }


def build_astock_rows(a_pf) -> str:
    """构建 A股持仓表行（简化版，从 update_holdings.build_astock_rows 复用核心逻辑）"""
    # 直接调用 update_holdings 的函数（它已经处理了复杂盈亏计算）
    import update_holdings as uh
    # uh.build_astock_rows 需要 dict，把 pydantic 对象转回去
    # model_dump() 已包含 daily_records（schemas.py 显式声明）
    # update_holdings.get_prev_snapshot 依赖 daily_records[].positions_snapshot[code].price
    pf_dict = a_pf.model_dump()
    return uh.build_astock_rows(pf_dict)


def build_fund_rows(f_pf) -> str:
    """构建基金持仓表行"""
    import update_holdings as uh
    pf_dict = f_pf.model_dump()
    return uh.build_fund_rows(pf_dict)


def extract_preserved_sections(old_html: str) -> dict:
    """从旧 index.html 提取需要保留的部分（非 update_holdings 维护的）

    提取：
    - trades_js: allTrades 数组
    - chart_js: chart 数据和渲染代码
    - decision_section: 今日盘中决策实录
    - cleared_section: 已清仓标的
    """
    preserved = {
        'trades_js': '',
        'chart_js': '',
        'decision_section': '',
        'cleared_section': '',
    }

    # trades_js: 提取 allTrades = [...] 整段
    m = re.search(r'(// ========== REAL TRADE DATA.*?)(?=// ========== CHART)', old_html, re.DOTALL)
    if m:
        preserved['trades_js'] = m.group(1).strip()
    else:
        raise AssertionError("[extract] 找不到 allTrades 段")

    # chart_js: 提取 chart 配置段（从 CHART 注释到 TRADE PAGINATION）
    m = re.search(r'(// ========== CHART:.*?)(?=// ========== TRADE PAGINATION)', old_html, re.DOTALL)
    if m:
        preserved['chart_js'] = m.group(1).strip()
    else:
        raise AssertionError("[extract] 找不到 chart 段")

    # decision_section: 提取今日盘中决策实录整个 div
    m = re.search(r'(\s*<!-- 今日盘中决策实录.*?</div>\s*</div>\s*</div>)', old_html, re.DOTALL)
    if m:
        preserved['decision_section'] = m.group(1).strip()

    # cleared_section: 提取已清仓标的整个 div（含 switchClearedTab script）
    m = re.search(r'(<!-- 已清仓标的.*?</script>)', old_html, re.DOTALL)
    if m:
        preserved['cleared_section'] = m.group(1).strip()

    return preserved


def render_index(check_only: bool = False) -> str:
    """主入口：加载数据 → 渲染 → self-check → 写入"""
    print("=" * 60)
    print("render_index.py 开始（Jinja2 全量渲染模式）")

    # === Step 1: 加载数据（pydantic 校验）===
    print("📥 Step 1: 加载数据...")
    a_pf = load_astock_portfolio()
    f_pf = load_fund_portfolio()
    print(f"  A股: cash=¥{a_pf.cash:,.2f} positions={len(a_pf.positions)} NAV=¥{a_pf.nav:,.2f}")
    print(f"  基金: cash=¥{f_pf.cash:,.2f} positions={len(f_pf.positions)} NAV=¥{f_pf.nav:,.2f}")

    # === Step 2: self-check ===
    print("🔍 Step 2: self-check...")
    assert_self_consistent(a_pf, f_pf)
    print("  ✅ 通过")

    # === Step 3: 计算渲染变量 ===
    print("🧮 Step 3: 计算渲染变量...")
    card_vars = compute_card_vars(a_pf, f_pf)
    print(f"  A股 NAV=¥{card_vars['a_nav']:,.2f} | 基金 NAV=¥{card_vars['f_nav']:,.2f} | 合并=¥{card_vars['combined_nav']:,.2f}")

    # === Step 4: 构建持仓表行 ===
    print("📋 Step 4: 构建持仓表行...")
    astock_rows = build_astock_rows(a_pf)
    fund_rows = build_fund_rows(f_pf)

    # === Step 5: 从旧 index.html 提取保留段 ===
    print("📦 Step 5: 提取保留段（chart/trades/decision/cleared）...")
    old_html = INDEX_HTML.read_text(encoding="utf-8")
    preserved = extract_preserved_sections(old_html)
    print(f"  trades={len(preserved['trades_js'])} chars, chart={len(preserved['chart_js'])} chars")

    # === Step 6: Jinja2 渲染 ===
    print("🎨 Step 6: Jinja2 渲染...")
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=False)
    template = env.get_template("index.html.j2")
    update_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_html = template.render(
        update_time=update_time,
        astock_rows=astock_rows,
        fund_rows=fund_rows,
        trades_js=preserved['trades_js'],
        chart_js=preserved['chart_js'],
        decision_section=preserved['decision_section'],
        cleared_section=preserved['cleared_section'],
        **card_vars,
    )

    # === Step 7: 渲染后 self-check ===
    print("✅ Step 7: 渲染后 self-check...")
    checks = [
        (f"¥{card_vars['a_nav']:,.2f}", "A股 NAV 卡片"),
        (f"¥{card_vars['f_nav']:,.2f}", "基金 NAV 卡片"),
        (f"¥{card_vars['combined_nav']:,.2f}", "合并 NAV 卡片"),
        (f"¥{card_vars['a_cash']:,.2f} / ¥{card_vars['f_cash']:,.2f}", "可用资金卡片"),
        ("const allTrades = [", "allTrades 数组"),
        ("new Chart(ctx", "Chart 渲染"),
    ]
    for expected, label in checks:
        if expected not in new_html:
            raise AssertionError(f"[self-check] {label} 渲染失败：找不到 '{expected}'")
    print("  ✅ 全部通过")

    # === Step 8: 写入 ===
    if check_only:
        print("\n🟡 --check 模式：不写入文件")
    else:
        INDEX_HTML.write_text(new_html, encoding="utf-8")
        print(f"💾 已写入 {INDEX_HTML}")

    return new_html


if __name__ == "__main__":
    check_only = "--check" in sys.argv
    try:
        render_index(check_only=check_only)
        print("\n✅ 全部完成")
    except Exception as e:
        print(f"\n❌ 失败：{e}")
        sys.exit(1)
