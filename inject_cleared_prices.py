"""
v11.16（踩坑日志 #018，2026-07-20）：
cleared_positions.py 因 NeoData token 失效 + 不传 --token 导致"清仓后距今"全显示"—"。
本次先手动用 NeoData 真实查价注入 index.html，并记录到踩坑日志。
"""
import re
from pathlib import Path

INDEX = Path(r"E:\temp\financial-report\index.html")

# 7/20 收盘价（NeoData 真实数据，非 WebSearch）
PRICES = {
    "688300": (131.32, "联瑞新材"),   # 清仓均价 181.90
    "600547": (24.39,  "山东黄金"),    # 清仓均价 24.01
    "002156": (68.39,  "通富微电"),    # 清仓均价 64.40
    "688131": (73.70,  "皓元医药"),    # 清仓均价 84.41
    "600584": (80.16,  "长电科技"),    # 清仓均价 103.57
    "300408": (93.71,  "三环集团"),    # 清仓均价 144.66
}

# 清仓均价（从 index.html 现有内容读取，用于计算 post_clear_pct）
AVG_SELL = {
    "688300": 181.90,
    "600547": 24.01,
    "002156": 64.40,
    "688131": 84.41,
    "600584": 103.57,
    "300408": 144.66,
}


def fmt_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v*100:.2f}%"


def cls(v: float) -> str:
    return "up" if v > 0 else ("down" if v < 0 else "")


html = INDEX.read_text(encoding="utf-8")
changed = 0

for code, (price, name) in PRICES.items():
    avg = AVG_SELL[code]
    pct = (price - avg) / avg
    pct_cls = cls(pct)
    pct_str = fmt_pct(pct)
    # 替换 td 里的 "—"
    # 现有：<td><span style='color:#64748b'>—</span></td>
    # 目标：<td class='up/down'><span>+X.XX%<br><small>现价格 ¥XXX.XX</small></span></td>
    new_cell = (
        f"<td class='{pct_cls}'>{pct_str}<br>"
        f"<small>现价格 ¥{price:.2f}</small></td>"
    )
    # 按 code 锁定行（每行开头有 code span），然后替换该行的 "—" cell
    # 现有结构：<tr><td class='hide-mobile'><span class='stock-code'>CODE</span></td>...<td><span style='color:#64748b'>—</span></td></tr>
    pattern = re.compile(
        r"(<tr><td class='hide-mobile'><span class='stock-code'>" + re.escape(code) + r"</span></td>.*?)(<td><span style='color:#64748b'>—</span></td>)(</tr>)",
        re.DOTALL,
    )
    new_html, n = pattern.subn(
        lambda m: m.group(1) + new_cell + m.group(3), html
    )
    if n > 0:
        html = new_html
        changed += n
        print(f"  ✅ {name}({code}) 现价 ¥{price:.2f} → 清仓后 {pct_str}")
    else:
        print(f"  ⚠️ {name}({code}) 未匹配到待替换行")

if changed > 0:
    INDEX.write_text(html, encoding="utf-8")
    print(f"\n🎉 共注入 {changed} 行清仓现价，已写入 {INDEX.name}")
else:
    print("\n⚠️ 未做任何改动")
