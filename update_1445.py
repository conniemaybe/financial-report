import json
from datetime import datetime

# Load portfolio
with open('C:/Users/conniehe/.workbuddy/astock-simulator/portfolio.json', 'r', encoding='utf-8') as f:
    portfolio = json.load(f)

# Update pending strategies check_results for 14:45
for ps in portfolio.get('pending_strategies', []):
    if ps.get('status') != 'pending':
        continue
    
    code = ps.get('code')
    
    if code == '000988':  # 华工科技
        check_result = {
            "time": "2026-07-01 14:45",
            "action": "SKIP",
            "price": 172.89,
            "reason": "三重否决：①价格172.89<175.00低于区间下限❌ ②自动化设备板块-2.16%<-0.5%板块杀跌否决❌ ③早盘急跌否决继承(10:30已确认昨收184.61→最低179.73跌幅-2.64%>2%)❌。量比1.14≥1.0✅，换手8.58%<15%✅，大盘+0.14%未触-1.5%否决✅，非涨停✅，但三重否决（华工科技全天-6.35%大幅回调）"
        }
        ps['check_results'].append(check_result)
        print(f"Updated 000988 华工科技: 14:45 SKIP")
    
    elif code == '300124':  # 汇川技术
        check_result = {
            "time": "2026-07-01 14:45",
            "action": "SKIP",
            "price": 68.18,
            "reason": "双重否决：①价格68.18>66.00超区间上限❌（+2.79%持续远离区间）②自动化设备板块-2.16%<-1%板块杀跌否决❌。量比1.11≥0.8✅，换手1.85%<15%✅，开盘+0.11%非急跌✅，大盘+0.14%未触-1.5%否决✅，非涨停✅，但价格超区间+板块杀跌双重否决"
        }
        ps['check_results'].append(check_result)
        print(f"Updated 300124 汇川技术: 14:45 SKIP")

# Save portfolio
with open('C:/Users/conniehe/.workbuddy/astock-simulator/portfolio.json', 'w', encoding='utf-8') as f:
    json.dump(portfolio, f, ensure_ascii=False, indent=2)

print("Portfolio updated successfully")
