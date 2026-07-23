import sys
sys.path.insert(0, r'E:\temp\financial-report')
sys.path.insert(0, r'C:\Users\conniehe\.workbuddy\skills\astock-simulator\scripts')

# 直接测 Jinja2 的 format 过滤器
from jinja2 import Environment
env = Environment(autoescape=False)
tmpl = env.from_string('{{ "%.2f"|format(541139.96) }}')
result = tmpl.render()
print(f'Jinja2 format 输出: "{result}"')
print(f'期望: "541139.96"')

# 测试千分位
tmpl2 = env.from_string('{{ "{:,.2f}".format(541139.96) }}')
result2 = tmpl2.render()
print(f'千分位 format 输出: "{result2}"')

# 测试 a_nav 直接渲染
tmpl3 = env.from_string('A股 NAV: ¥{{ "{:,.2f}".format(a_nav) }}')
result3 = tmpl3.render(a_nav=541139.96)
print(f'模板3 输出: "{result3}"')
