import sys
sys.path.insert(0, r'E:\temp\financial-report')
from render_index import render_index
html = render_index(check_only=True)
import re
m = re.search(r'A股账户净值.{0,400}', html, re.DOTALL)
if m:
    print('=== A股卡片片段 ===')
    print(m.group(0)[:400])
else:
    print('NOT FOUND')
    print('html len:', len(html))
    # 找所有 NAV 相关
    for kw in ['541139', '541,139', '¥541']:
        if kw in html:
            print(f'找到 {kw} at index:', html.find(kw))
