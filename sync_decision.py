#!/usr/bin/env python3
"""盘中决策同步脚本 —— 每次检查完自动更新GitHub Pages网站"""
import sys, json, re, subprocess, os
from datetime import datetime

def run(cmd, cwd=None):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd or REPO)
    return result.stdout.strip(), result.stderr.strip(), result.returncode

REPO = r"E:\temp\financial-report"
INDEX = os.path.join(REPO, "index.html")

def update_decision(time_slot, status, body, market_banner=None):
    """
    time_slot: "10:00" | "10:30" | "13:30" | "14:45"
    status: "done" | "pending"
    body: HTML content for d-body div
    market_banner: optional, update market banner at the same time
    """
    with open(INDEX, "r", encoding="utf-8") as f:
        html = f.read()

    # Find the decision node for this time slot and update it
    # Pattern: <div class="d-time">10:00</div>...<div class="d-body">...</div>
    time_escaped = re.escape(time_slot)

    # Update status class and status text
    old_classes = {"done": "pending", "pending": "done"}
    old_class = old_classes.get(status, "pending")

    # Replace the decision node class
    pattern_class = rf'(<div class="decision-node ){old_class}(">\s*<div>\s*<div class="d-time">{time_escaped}</div>)'
    repl_class = rf'\1{status}\2'
    html = re.sub(pattern_class, repl_class, html, count=1)

    # Replace status text
    if status == "done":
        old_status_text = "⏳ 待执行"
        new_status_text = "✅ 已完成"
    else:
        old_status_text = "✅ 已完成"
        new_status_text = "⏳ 待执行"

    pattern_status = rf'(<div class="d-time">{time_escaped}</div>\s*<div class="d-status">){old_status_text}(</div>)'
    repl_status = rf'\1{new_status_text}\2'
    html = re.sub(pattern_status, repl_status, html, count=1)

    # Update body - find the d-body div that follows this time slot
    pattern_body = rf'(<div class="d-time">{time_escaped}</div>.*?</div>\s*<div class="d-body">\s*).*?(</div>\s*</div>\s*(?:<div class="decision-node|</div>\s*</div>\s*<div class="market-banner))'
    repl_body = rf'\1{body}\n        \2'
    html = re.sub(pattern_body, repl_body, html, flags=re.DOTALL, count=1)

    # Update market banner if provided
    if market_banner:
        old_banner = r'(<div class="market-banner[^"]*">).*?(</div>)'
        new_banner = rf'\1{market_banner}\2'
        html = re.sub(old_banner, new_banner, html, count=1)

    # Update last-updated time
    now_str = datetime.now().strftime("%H:%M")
    old_time = r'(最后更新 )\d{2}:\d{2}'
    new_time = rf'\1{now_str}'
    html = re.sub(old_time, new_time, html, count=1)

    with open(INDEX, "w", encoding="utf-8") as f:
        f.write(html)

    # Git operations
    _, err, code = run("git add -A")
    if code != 0:
        print(f"git add failed: {err}")
        return False

    commit_msg = f"盘中决策更新 {time_slot} — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    _, err, code = run(f'git commit -m "{commit_msg}"')
    if code != 0 and "nothing to commit" not in err:
        print(f"git commit failed: {err}")
        # Continue anyway - might have already been committed

    out, err, code = run("git push origin main")
    if code != 0:
        print(f"git push failed: {err}")
        return False

    print(f"[OK] {time_slot} 决策已同步到网站")
    return True

def update_from_json(json_str):
    """从JSON字符串批量更新多个时间窗口"""
    try:
        data = json.loads(json_str)
    except:
        print("Invalid JSON input")
        return False

    updates = data.get("updates", [])
    market_banner = data.get("market_banner")

    success = True
    for u in updates:
        # First update without market_banner (only last one updates banner)
        mb = market_banner if u is updates[-1] else None
        if not update_decision(u["time"], u["status"], u["body"], mb):
            success = False
    return success

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sync_decision.py '<json>'")
        print('JSON: {"updates":[{"time":"10:00","status":"done","body":"<b>...</b>"}],"market_banner":"..."}')
        sys.exit(1)

    json_input = sys.argv[1]
    update_from_json(json_input)
