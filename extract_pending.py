import json

# A-share portfolio
with open('C:/Users/conniehe/.workbuddy/astock-simulator/portfolio.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

print('=== PENDING STRATEGIES ===')
ps = d.get('pending_strategies', [])
print(json.dumps(ps, ensure_ascii=False, indent=2))
print(f'Total pending: {len(ps)}')
print('=== CASH INFO ===')
print(f'cash: {d.get("cash", 0)}')
print(f'total_assets: {d.get("total_assets", 0)}')

# Fund portfolio
with open('C:/Users/conniehe/.workbuddy/astock-simulator/fund_portfolio.json', 'r', encoding='utf-8') as f:
    fd = json.load(f)

print('\n=== FUND PENDING STRATEGIES ===')
fps = fd.get('fund_pending_strategies', [])
print(json.dumps(fps, ensure_ascii=False, indent=2))
print(f'Total fund pending: {len(fps)}')
print('=== FUND CASH INFO ===')
print(f'cash: {fd.get("cash", 0)}')
print(f'total_assets: {fd.get("total_assets", 0)}')
