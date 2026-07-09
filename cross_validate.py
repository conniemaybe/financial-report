#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cross_validate.py — 日报数据 vs 网站首页数据 差值比对交叉验证

背景（v10.3 用户反馈）：
  日报 HTML 和 index.html（网站首页）虽然数据源不同（日报从 portfolio.json 渲染，
  index.html 由 update_holdings.py 直接写），但更新后两边持仓表应当一致。
  若不一致 → 说明某一侧数据有问题（如 v10.1 浪潮信息日报显示 +21.01% 但实际 +10%）。

用法：
  python cross_validate.py <YYYYMMDD>          # 默认比对今天的日报 vs index.html
  python cross_validate.py 2026-07-09          # 比对指定日期

输出：
  - 控制台打印比对结果表格
  - 差值超阈值时返回非 0 退出码（可被 automation 调用做告警）
  - 详细日志写入 ~/.workbuddy/astock-simulator/cross_validate.log
"""

import re
import sys
import json
import logging
from datetime import datetime
from pathlib import Path

# ===== 路径常量 =====
REPORT_DIR = Path(r"E:/workbuddyProject/Project_01_invest")
INDEX_HTML = Path(r"C:/temp/financial-report/index.html")
LOG_FILE = Path.home() / ".workbuddy" / "astock-simulator" / "cross_validate.log"

# 比对阈值（当日盈亏率% / 总盈亏率% / 市值元 的容差）
TOLERANCE_PCT = 0.5    # 盈亏率容差 0.5%
TOLERANCE_AMOUNT = 50  # 金额容差 50 元

# ===== 日志配置 =====
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("cross_validate")


# ===== HTML 解析工具 =====

def parse_money(text: str) -> float | None:
    """从 '¥85,990.00' / '+¥7,820.00' / '-¥110.00' 解析出 float。"""
    if not text:
        return None
    s = re.sub(r"[¥,\s\u2191\u2193]", "", text.strip())  # 去掉 ¥, 千分位, 箭头
    if not s or s in ("—", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_pct(text: str) -> float | None:
    """从 '+10.00%' / '-3.46%' 解析出 float（单位 %）。"""
    if not text:
        return None
    m = re.search(r"([-+]?\d+(?:\.\d+)?)\s*%", text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def extract_position_rows_from_html(html: str, table_keyword: str) -> list[dict]:
    """
    从 HTML 中提取持仓表的每一行。
    table_keyword: 'A股持仓' 或 '基金持仓'，用于定位到正确的 <table>
    返回: [{code, name, current_price, market_value, daily_pnl, daily_pnl_pct, total_pnl, total_pnl_pct}, ...]
    """
    # 找到表头关键字所在位置，然后向后找第一个 <tbody>...</tbody>
    key_idx = html.find(table_keyword)
    if key_idx < 0:
        return []
    tbody_start = html.find("<tbody", key_idx)
    tbody_end = html.find("</tbody>", tbody_start)
    if tbody_start < 0 or tbody_end < 0:
        return []
    tbody = html[tbody_start:tbody_end]

    rows = []
    # 匹配 <tr>...</tr>
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", tbody, re.DOTALL):
        row_html = m.group(1)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL)
        if len(tds) < 6:
            continue
        # 清理 HTML tag、空白
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in tds]

        row = {"raw_cells": cells}
        # 提取代码（stock-code）
        code_match = re.search(r"stock-code'>(\d+)<", row_html)
        row["code"] = code_match.group(1) if code_match else None
        # 提取名称（stock-name）
        name_match = re.search(r"stock-name'>([^<]+)<", row_html)
        row["name"] = name_match.group(1).strip() if name_match else None

        # A股表头: 代码 | 名称 | 持仓 | 成本 | 现价 | 市值 | 当日盈亏 | 总盈亏
        # 基金表头: 代码 | 名称 | 类型 | 持仓 | 成本净值 | 当前净值 | 市值 | 当日盈亏 | 总盈亏
        try:
            if table_keyword == "A股持仓":
                # 现价在 cells[4], 市值在 cells[5], 当日盈亏在 cells[6], 总盈亏在 cells[7]
                row["current_price"] = parse_money(cells[4])
                row["market_value"] = parse_money(cells[5])
                row["daily_pnl"] = parse_money(cells[6])
                row["daily_pnl_pct"] = parse_pct(cells[6])
                row["total_pnl"] = parse_money(cells[7])
                row["total_pnl_pct"] = parse_pct(cells[7])
            else:  # 基金
                # 当前净值在 cells[5], 市值在 cells[6], 当日盈亏在 cells[7], 总盈亏在 cells[8]
                row["current_nav"] = parse_money(cells[5])
                row["market_value"] = parse_money(cells[6])
                row["daily_pnl"] = parse_money(cells[7])
                row["daily_pnl_pct"] = parse_pct(cells[7])
                row["total_pnl"] = parse_money(cells[8])
                row["total_pnl_pct"] = parse_pct(cells[8])
        except IndexError:
            pass
        if row.get("code"):
            rows.append(row)
    return rows


def load_daily_report_positions(report_path: Path) -> dict:
    """从日报 HTML 提取 A股+基金 持仓表数据。
    日报结构：
      <div class="section"><div class="section-title">收盘持仓</div>   ← A股持仓
        <table>...</table>
      （基金 section 后面会有第二个 "收盘持仓" 或 "基金持仓"）
    用 "收盘持仓" 标题作为锚点，第一个是 A股，第二个是基金。
    """
    if not report_path.exists():
        log.warning(f"日报不存在: {report_path}")
        return {"stock": [], "fund": []}
    html = report_path.read_text(encoding="utf-8", errors="ignore")

    # 找所有 "收盘持仓" 的位置
    anchor = "收盘持仓"
    positions = []
    start = 0
    while True:
        idx = html.find(anchor, start)
        if idx < 0:
            break
        # 找紧随其后的 <tbody>...</tbody>
        tbody_start = html.find("<tbody", idx)
        tbody_end = html.find("</tbody>", tbody_start)
        if 0 < tbody_start - idx < 2000 and tbody_end > tbody_start:  # 确保是同一个 section 的表
            tbody = html[tbody_start:tbody_end]
            rows = _parse_daily_tbody(tbody)
            if rows:
                positions.append(rows)
        start = idx + 1

    # 第一个 "收盘持仓" → A股，第二个 → 基金
    stock_rows = positions[0] if len(positions) >= 1 else []
    fund_rows = positions[1] if len(positions) >= 2 else []
    return {"stock": stock_rows, "fund": fund_rows}


def _parse_daily_tbody(tbody: str) -> list[dict]:
    """解析日报持仓表的 tbody，返回标准化行字典列表。
    日报 A股表头: 股票 | 持股数 | 成本价 | 收盘价 | 市值 | 占比 | 当日盈亏 | 总盈亏
    日报 基金表头: 基金 | 份额 | 成本净值 | 最新净值 | 市值 | 占比 | 当日盈亏 | 总盈亏
    """
    rows = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", tbody, re.DOTALL):
        row_html = m.group(1)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL)
        if len(tds) < 6:
            continue
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in tds]

        row = {"raw_cells": cells}
        # 日报 A股格式：<td class="col-text">浪潮信息<br><small ...>000977</small></td>
        # 日报 基金格式：<td class="col-text">沪深300ETF<small ...><br>510300</small> ...</td>（注意 <br> 在 small 内部）
        # 通用做法：直接在 row_html 里找 6 位数字（基金/A股代码都是 6 位）
        all_codes = re.findall(r"\b(\d{6})\b", row_html)
        # 过滤掉明显非代码的数字（如成交量 27000.00、市值 132732 等，它们不是独立的 6 位边界）
        # 代码特征：通常被 <small> 包裹，或在 <br> 之后
        code = None
        small_match = re.search(r"<small[^>]*>(?:\s*<br>)?\s*(\d{6})", row_html)
        if small_match:
            code = small_match.group(1)
        elif all_codes:
            # 兜底：取第一个独立的 6 位数字
            code = all_codes[0]
        row["code"] = code
        # 名称：col-text 后第一个 <br> 或 <small> 前的文字
        name_match = re.search(r"class=['\"]col-text['\"][^>]*>\s*([^<\r\n]+?)(?:<br|<small)", row_html)
        row["name"] = name_match.group(1).strip() if name_match else None

        # 表头固定 8 列，索引：0名称 1持股数 2成本 3现价/最新净值 4市值 5占比 6当日盈亏 7总盈亏
        try:
            row["current_price"] = parse_money(cells[3])  # A股
            row["current_nav"] = parse_money(cells[3])    # 基金（同一个字段，不同语义）
            row["market_value"] = parse_money(cells[4])
            row["daily_pnl"] = parse_money(cells[6])
            row["daily_pnl_pct"] = parse_pct(cells[6])
            row["total_pnl"] = parse_money(cells[7])
            row["total_pnl_pct"] = parse_pct(cells[7])
        except IndexError:
            pass
        if row.get("code"):
            rows.append(row)
    return rows


def load_index_positions() -> dict:
    """从 index.html 提取 A股+基金 持仓表数据。"""
    if not INDEX_HTML.exists():
        log.error(f"index.html 不存在: {INDEX_HTML}")
        return {"stock": [], "fund": []}
    html = INDEX_HTML.read_text(encoding="utf-8", errors="ignore")
    stock_rows = extract_position_rows_from_html(html, "A股持仓")
    fund_rows = extract_position_rows_from_html(html, "基金持仓")
    return {"stock": stock_rows, "fund": fund_rows}


# ===== 比对逻辑 =====

def compare_rows(daily: list[dict], index: list[dict], side: str) -> list[dict]:
    """
    对比同一边（A股或基金）的持仓行。
    返回差异列表 [{code, name, field, daily_val, index_val, diff, severity}]
    """
    diffs = []
    index_by_code = {r["code"]: r for r in index if r.get("code")}
    daily_by_code = {r["code"]: r for r in daily if r.get("code")}

    all_codes = set(daily_by_code.keys()) | set(index_by_code.keys())

    for code in all_codes:
        d = daily_by_code.get(code, {})
        i = index_by_code.get(code, {})
        name = (d.get("name") or i.get("name") or code)

        # 缺失告警
        if code not in daily_by_code:
            diffs.append({
                "code": code, "name": name, "field": "存在于日报否",
                "daily_val": "❌日报缺失", "index_val": "✅首页有",
                "diff": None, "severity": "WARN",
            })
            continue
        if code not in index_by_code:
            diffs.append({
                "code": code, "name": name, "field": "存在于首页否",
                "daily_val": "✅日报有", "index_val": "❌首页缺失",
                "diff": None, "severity": "WARN",
            })
            continue

        # 逐字段对比
        fields = ["market_value", "daily_pnl", "daily_pnl_pct", "total_pnl", "total_pnl_pct"]
        for f in fields:
            dv = d.get(f)
            iv = i.get(f)
            if dv is None or iv is None:
                continue
            diff = abs(dv - iv)
            if f.endswith("_pct"):
                if diff > TOLERANCE_PCT:
                    diffs.append({
                        "code": code, "name": name, "field": f,
                        "daily_val": dv, "index_val": iv,
                        "diff": round(diff, 4), "severity": "FAIL",
                    })
            else:  # amount
                if diff > TOLERANCE_AMOUNT:
                    diffs.append({
                        "code": code, "name": name, "field": f,
                        "daily_val": dv, "index_val": iv,
                        "diff": round(diff, 2), "severity": "FAIL",
                    })
    return diffs


def run(target_date: str | None = None) -> int:
    """主流程：返回告警数量（0 表示全通过）"""
    today = target_date or datetime.now().strftime("%Y-%m-%d")
    log.info(f"========== 交叉验证 {today} ==========")

    # 定位日报文件
    yyyymmdd = today.replace("-", "")
    daily_path = REPORT_DIR / f"{yyyymmdd}_日报.html"
    if not daily_path.exists():
        log.warning(f"今日无日报文件: {daily_path}")
        # 无日报时，跳过日报侧，但仍然打印 index.html 数据快照
        index_data = load_index_positions()
        log.info(f"index.html A股 {len(index_data['stock'])} 只 / 基金 {len(index_data['fund'])} 只")
        return 0

    daily_data = load_daily_report_positions(daily_path)
    index_data = load_index_positions()

    log.info(f"日报 A股 {len(daily_data['stock'])} 只 / 基金 {len(daily_data['fund'])} 只")
    log.info(f"首页 A股 {len(index_data['stock'])} 只 / 基金 {len(index_data['fund'])} 只")

    total_fail = 0
    for side, side_name in [("stock", "A股"), ("fund", "基金")]:
        diffs = compare_rows(daily_data[side], index_data[side], side)
        if not diffs:
            log.info(f"✅ {side_name} 侧：全部持仓一致（容差 内）")
            continue
        log.warning(f"⚠️ {side_name} 侧发现 {len(diffs)} 处差异：")
        print(f"\n{'='*80}\n【{side_name}侧差异】\n{'='*80}")
        print(f"{'代码':<8}{'名称':<14}{'字段':<18}{'日报值':<16}{'首页值':<16}{'差值':<12}{'级别':<6}")
        print("-" * 90)
        for d in diffs:
            sev = d["severity"]
            total_fail += (1 if sev == "FAIL" else 0)
            print(f"{d['code']:<8}{(d['name'] or '')[:12]:<14}{d['field']:<18}"
                  f"{str(d['daily_val']):<16}{str(d['index_val']):<16}"
                  f"{str(d['diff']):<12}{sev:<6}")
        print()

    # 汇总
    print(f"\n{'='*80}")
    if total_fail == 0:
        print(f"✅ 交叉验证通过 — 日报与首页数据一致（容差：盈亏率±{TOLERANCE_PCT}% / 金额±¥{TOLERANCE_AMOUNT}）")
    else:
        print(f"❌ 交叉验证失败 — 共 {total_fail} 处差值超阈值")
        print(f"   可能原因：① 日报 generate_evening_report 缓存了旧数据 ② patch_holding_table 未生效")
        print(f"   修复建议：python ~/.workbuddy/skills/astock-simulator/scripts/patch_holding_table.py "
              f"{REPORT_DIR.name}/{yyyymmdd}_日报.html {today}")
    print(f"{'='*80}\n")

    log.info(f"交叉验证完成，FAIL 数量：{total_fail}")
    return total_fail


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(run(target))
