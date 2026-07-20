#!/usr/bin/env python3
"""
2026-07-08 v1：已清仓标的板块生成器

从 portfolio.trades 全量遍历，识别所有清仓标的（SELL 总份额 ≥ BUY 总份额
且已不在 positions 里），计算实现盈亏、盈亏%、清仓均价、清仓后距今收益率等指标。

输出：在 index.html 的"近期交易记录"和"历史报告档案"之间插入独立模块，
      支持A股/基金切换。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ASTOCK_PORTFOLIO = Path(r"C:\Users\conniehe\.workbuddy\astock-simulator\portfolio.json")
FUND_PORTFOLIO = Path(r"C:\Users\conniehe\.workbuddy\astock-simulator\fund_portfolio.json")
INDEX_HTML = Path(r"E:\temp\financial-report\index.html")

# NeoData query.py 路径（v11.16 修复 #018：旧路径 skills-marketplace 已废弃，
# 统一用 plugins 路径；如未来再迁移，请同步更新所有引用该路径的脚本）
NEODATA_QUERY_CANDIDATES = [
    Path(r"C:\Users\conniehe\.workbuddy\plugins\marketplaces\experts\plugins\a-share-analysis\skills\neodata-financial-search\scripts\query.py"),
    Path(r"C:\Users\conniehe\.workbuddy\skills-marketplace\skills\neodata-financial-search\scripts\query.py"),
]
NEODATA_QUERY = next((p for p in NEODATA_QUERY_CANDIDATES if p.exists()), NEODATA_QUERY_CANDIDATES[0])
PYTHON = r"C:\Users\conniehe\.workbuddy\binaries\python\versions\3.13.12\python.exe"

# Token 缓存文件（v11.16 修复 #018：query.py 失效时主动用缓存 token 重试）
NEODATA_TOKEN_CACHE = Path(r"C:\Users\conniehe\.workbuddy\plugins\marketplaces\experts\plugins\a-share-analysis\skills\.neodata_token")


def _read_cached_token() -> str:
    """读取 NeoData 缓存 token（12 小时有效期）。
    失败返回空串。"""
    try:
        if NEODATA_TOKEN_CACHE.exists():
            return NEODATA_TOKEN_CACHE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _query_neodata(query: str) -> str:
    """查询 NeoData，自动带 --token（v11.16 修复 #018）。

    流程：
      1. 先无 token 查（让 query.py 自己读缓存）
      2. 若返回 TOKEN_EXPIRED / TOKEN_MISSING → 读本地缓存 token 显式带 --token 重试
      3. 仍失败 → 返回空串，调用方按"未查到"处理
    """
    try:
        r = subprocess.run(
            [PYTHON, str(NEODATA_QUERY), "--query", query],
            capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        out = r.stdout or ""
        # 若脚本提示需要 token → 用缓存 token 重试
        if "TOKEN_EXPIRED" in out or "TOKEN_MISSING" in out:
            tok = _read_cached_token()
            if tok:
                r2 = subprocess.run(
                    [PYTHON, str(NEODATA_QUERY), "--query", query, "--token", tok],
                    capture_output=True, text=True, encoding="utf-8", timeout=60
                )
                return r2.stdout or ""
        return out
    except Exception as e:
        print(f"  ⚠️ NeoData 查询异常: {e}")
        return ""


# ============== 数据层 ==============

def identify_cleared_positions(portfolio: dict, is_fund: bool = False) -> list[dict]:
    """识别已清仓标的。
    清仓判定：① 已不在 positions；② SELL 总份额 ≥ BUY 总份额；③ BUY 总份额 > 0
    """
    current_positions = set(portfolio.get("positions", {}).keys())
    trades_by_code = {}
    for t in portfolio.get("trades", []):
        c = t.get("code")
        if not c:
            continue
        trades_by_code.setdefault(c, []).append(t)

    cleared = []
    for code, trades in trades_by_code.items():
        if code in current_positions:
            continue
        buys = [t for t in trades if t["action"] == "BUY"]
        sells = [t for t in trades if t["action"] == "SELL"]
        divs = [t for t in trades if t["action"] == "DIVIDEND"]
        buy_shares = sum(t["shares"] for t in buys)
        sell_shares = sum(t["shares"] for t in sells)
        if buy_shares == 0:
            continue
        if sell_shares < buy_shares:
            # 部分清仓但 positions 里没有 → 数据残留，跳过避免误导
            continue

        name = trades[0].get("name", code)
        # ⚠️ v9 正确口径：绕开 amount 字段歧义（早期 amount=price×shares，后期 amount=total_cost/net_proceeds）
        # 直接基于 price/shares + 独立费用字段重算
        # BUY 成本 = Σ(price × shares + commission + transfer_fee)
        buy_total = sum(
            t["price"] * t["shares"] + t.get("commission", 0) + t.get("transfer_fee", 0)
            for t in buys
        )
        # SELL 净额 = Σ(price × shares - commission - stamp_tax - transfer_fee)
        sell_total = sum(
            t["price"] * t["shares"] - t.get("commission", 0) - t.get("stamp_tax", 0) - t.get("transfer_fee", 0)
            for t in sells
        )
        div_total = sum(t.get("amount", 0) for t in divs)
        realized = sell_total - buy_total + div_total
        realized_pct = realized / buy_total if buy_total > 0 else 0
        avg_buy = buy_total / buy_shares
        avg_sell = sell_total / sell_shares if sell_shares > 0 else 0
        last_sell_date = max(t["date"] for t in sells)

        cleared.append({
            "code": code,
            "name": name,
            "is_fund": is_fund,
            "shares": buy_shares,
            "avg_buy": avg_buy,
            "avg_sell": avg_sell,
            "buy_total": buy_total,
            "sell_total": sell_total,
            "div_total": div_total,
            "realized": realized,
            "realized_pct": realized_pct,
            "clear_date": last_sell_date,
            "current_price": None,  # 后续 NeoData 填充
            "post_clear_pct": None,  # 清仓后距今收益率
        })

    # 按清仓日期降序
    cleared.sort(key=lambda x: x["clear_date"], reverse=True)
    return cleared


def fetch_current_prices(cleared_astock: list, cleared_fund: list) -> None:
    """用 NeoData 查询清仓标的的现价，填充 current_price 和 post_clear_pct。

    v11.16 修复 #018：原版不传 --token，NeoData 本地凭证失效后全部返回空 →
    所有清仓标的"清仓后距今"显示"—"。现在统一走 _query_neodata helper，
    先无 token 查 → TOKEN_MISSING 时用缓存 token 重试。
    """
    # 股票
    for item in cleared_astock:
        code = item["code"]
        query = f"{item['name']} {code} 最新价格"
        out = _query_neodata(query)
        m = re.search(r"最新价格[:：]\s*([\d.]+)元", out)
        if m:
            price = float(m.group(1))
            item["current_price"] = price
            if item["avg_sell"] > 0:
                item["post_clear_pct"] = (price - item["avg_sell"]) / item["avg_sell"]
            print(f"  ✅ {item['name']}({code}) 现价 {price}")
        else:
            print(f"  ⚠️ {item['name']}({code}) 未解析到价格（NeoData 返回为空或鉴权失败）")

    # 基金（ETF 走股票接口）
    for item in cleared_fund:
        code = item["code"]
        query = f"{item['name']} {code} 最新净值"
        out = _query_neodata(query)
        m = re.search(r"最新(?:价格|净值)[:：]\s*([\d.]+)", out)
        if m:
            price = float(m.group(1))
            item["current_price"] = price
            if item["avg_sell"] > 0:
                item["post_clear_pct"] = (price - item["avg_sell"]) / item["avg_sell"]
            print(f"  ✅ {item['name']}({code}) 现净值 {price}")
        else:
            print(f"  ⚠️ {item['name']}({code}) 未解析到净值")


# ============== 渲染层 ==============

def fmt_money(v: float) -> str:
    sign = "+" if v >= 0 else "-"
    return f"{sign}¥{abs(v):,.2f}"


def fmt_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v*100:.2f}%"


def cls(v: float) -> str:
    return "up" if v > 0 else ("down" if v < 0 else "")


def build_cleared_rows(cleared: list, is_fund: bool) -> str:
    """构造 tbody HTML"""
    if not cleared:
        return '<tr><td colspan="8" style="text-align:center;color:#64748b;padding:24px;">暂无已清仓标的</td></tr>'

    rows = []
    for item in cleared:
        realized_cls = cls(item["realized"])
        post_cls = cls(item["post_clear_pct"] or 0)
        price_label = "净值" if is_fund else "价格"
        # 清仓后收益率（带现价）
        if item["current_price"] is not None and item["post_clear_pct"] is not None:
            post_cell = (
                f"<td class='{post_cls}'>{fmt_pct(item['post_clear_pct'])}<br>"
                f"<small>现{price_label} ¥{item['current_price']:.{4 if is_fund else 2}f}</small></td>"
            )
        else:
            post_cell = "<td><span style='color:#64748b'>—</span></td>"

        # 盈亏%相对成本
        pct_vs_cost = item["realized_pct"]

        rows.append(
            f"<tr>"
            f"<td class='hide-mobile'><span class='stock-code'>{item['code']}</span></td>"
            f"<td class='col-text'><span class='stock-name'>{item['name']}</span></td>"
            f"<td>{item['clear_date']}</td>"
            f"<td class='{realized_cls}'>{fmt_money(item['realized'])}</td>"
            f"<td class='{realized_cls}'>{fmt_pct(pct_vs_cost)}</td>"
            f"<td>¥{item['avg_sell']:.{4 if is_fund else 2}f}</td>"
            f"<td>¥{item['avg_buy']:.{4 if is_fund else 2}f}</td>"
            f"{post_cell}"
            f"</tr>"
        )
    return "\n        ".join(rows)


def build_summary_row(cleared: list) -> str:
    """v9：已清仓模块不显示汇总行（用户反馈不需要加总）。
    保留函数仅为兼容，返回空字符串。
    """
    return ""


def build_cleared_module(cleared_astock: list, cleared_fund: list) -> str:
    """构造完整模块 HTML（含 A股/基金切换）"""
    a_rows = build_cleared_rows(cleared_astock, is_fund=False)
    a_summary = build_summary_row(cleared_astock)
    f_rows = build_cleared_rows(cleared_fund, is_fund=True)
    f_summary = build_summary_row(cleared_fund)

    return f'''
  <!-- 已清仓标的 — 2026-07-08 新增 -->
  <div class="section" id="clearedSection">
    <h2>📦 已清仓标的</h2>
    <div class="filters" id="clearedFilters" style="margin-bottom:12px;">
      <button class="filter-btn active" onclick="switchClearedTab('astock')">A股</button>
      <button class="filter-btn" onclick="switchClearedTab('fund')">基金</button>
      <span style="margin-left:auto;font-size:12px;color:#64748b;align-self:center;">
        💡 清仓后收益率 = (现价 - 清仓均价) / 清仓均价，正值表示"卖早了"
      </span>
    </div>

    <div class="table-wrap" id="clearedAstockTable">
      <table>
        <thead><tr>
          <th class="hide-mobile">代码</th>
          <th>名称</th>
          <th>清仓日期</th>
          <th>实现盈亏</th>
          <th>盈亏比例</th>
          <th>清仓均价</th>
          <th class="hide-mobile">成本均价</th>
          <th>清仓后距今</th>
        </tr></thead>
        <tbody>
          {a_rows}
          {a_summary}
        </tbody>
      </table>
    </div>

    <div class="table-wrap" id="clearedFundTable" style="display:none;">
      <table>
        <thead><tr>
          <th class="hide-mobile">代码</th>
          <th>名称</th>
          <th>清仓日期</th>
          <th>实现盈亏</th>
          <th>盈亏比例</th>
          <th>清仓净价</th>
          <th class="hide-mobile">成本净价</th>
          <th>清仓后距今</th>
        </tr></thead>
        <tbody>
          {f_rows}
          {f_summary}
        </tbody>
      </table>
    </div>
  </div>

  <script>
    function switchClearedTab(tab) {{
      document.querySelectorAll('#clearedFilters .filter-btn').forEach(b => b.classList.remove('active'));
      const btn = document.querySelector(`#clearedFilters .filter-btn[onclick*="${{tab}}"]`);
      if (btn) btn.classList.add('active');
      document.getElementById('clearedAstockTable').style.display = tab === 'astock' ? '' : 'none';
      document.getElementById('clearedFundTable').style.display = tab === 'fund' ? '' : 'none';
    }}
  </script>
'''


# ============== 注入层 ==============

def inject_cleared_module(html: str, module: str) -> str:
    """把 cleared 模块注入 index.html：插在"近期交易记录"和"历史报告档案"之间"""
    # 先清除已存在的 clearedSection（幂等，兼容多种写法）
    html = re.sub(
        r'\s*<!-- 已清仓标的.*?switchClearedTab.*?</script>\s*',
        '\n\n',
        html, flags=re.DOTALL, count=1,
    )
    # 尝试多个锚点（兼容历史版本）
    for marker in ["  <!-- Report Archive with Filters", "  <!-- Report Archive"]:
        if marker in html:
            return html.replace(marker, module + "\n" + marker, 1)
    # 兜底：搜索"历史报告档案"前的 section
    idx = html.find("历史报告档案")
    if idx > 0:
        section_start = html.rfind("<div class=\"section\"", 0, idx)
        if section_start > 0:
            return html[:section_start] + module + "\n" + html[section_start:]
    raise RuntimeError("未找到插入锚点：Report Archive / 历史报告档案")


# ============== 主流程 ==============

def main():
    with open(ASTOCK_PORTFOLIO, encoding="utf-8") as f:
        a_pf = json.load(f)
    with open(FUND_PORTFOLIO, encoding="utf-8") as f:
        f_pf = json.load(f)

    print("🔍 识别已清仓标的...")
    cleared_a = identify_cleared_positions(a_pf, is_fund=False)
    cleared_f = identify_cleared_positions(f_pf, is_fund=True)

    print(f"\n📊 A股清仓标的 {len(cleared_a)} 只：")
    for c in cleared_a:
        print(f"  {c['name']}({c['code']}) 清仓日={c['clear_date']} 实现={c['realized']:+.2f}({c['realized_pct']*100:+.2f}%)")

    print(f"\n📊 基金清仓标的 {len(cleared_f)} 只：")
    for c in cleared_f:
        print(f"  {c['name']}({c['code']}) 清仓日={c['clear_date']} 实现={c['realized']:+.2f}({c['realized_pct']*100:+.2f}%)")

    print("\n🌐 NeoData 查询清仓标的现价...")
    fetch_current_prices(cleared_a, cleared_f)

    print("\n🏗️  构造 HTML 模块...")
    module = build_cleared_module(cleared_a, cleared_f)

    print("\n📝 注入 index.html...")
    html = INDEX_HTML.read_text(encoding="utf-8")
    new_html = inject_cleared_module(html, module)
    INDEX_HTML.write_text(new_html, encoding="utf-8")

    print("\n✅ 已清仓标的模块注入完成")
    print(f"   A股 {len(cleared_a)} 只 | 基金 {len(cleared_f)} 只")


if __name__ == "__main__":
    main()
