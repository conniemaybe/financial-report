#!/bin/bash
# safe-push.sh — 推送 financial-report 到 GitHub Pages
# 用法: bash /c/temp/financial-report/safe-push.sh "commit message"
#
# 前置条件（一次性配置，已完成）：
#   git config --global http.proxy "http://127.0.0.1:7892"
#   git config --global https.proxy "http://127.0.0.1:7892"
#   （让 git 主动走小云朵代理，而非被动被 Windows 系统代理劫持）

set -e
REPO_DIR="/c/temp/financial-report"
COMMIT_MSG="${1:-auto sync}"
MAX_RETRIES=3

cd "$REPO_DIR"

# 1. 暂存所有变更
git add -A

# 2. 检查是否有变更需要提交
if git diff --cached --quiet; then
    echo "ℹ️  没有变更需要提交"
else
    git commit -m "$COMMIT_MSG"
    echo "✅ commit 完成: $COMMIT_MSG"
fi

# 3. 推送（git 全局已配置走代理 http://127.0.0.1:7892，3次重试）
echo "🔄 正在推送..."
for i in $(seq 1 $MAX_RETRIES); do
    if git push origin main 2>&1; then
        echo "✅ 第 $i 次 PUSH 成功"
        break
    else
        echo "⚠️ 第 $i 次 push 失败"
        if [ "$i" -eq "$MAX_RETRIES" ]; then
            echo "❌ PUSH 失败 ($MAX_RETRIES 次重试均失败)"
            echo "⚠️ 排查：检查小云朵代理是否运行在 127.0.0.1:7892"
            exit 1
        fi
        sleep 2
    fi
done

# 4. 验证
REMAINING=$(git log origin/main..HEAD --oneline 2>/dev/null | wc -l)
if [ "$REMAINING" -gt 0 ]; then
    echo "⚠️ 仍有 $REMAINING 个 commit 未推送"
    exit 1
else
    echo "🎉 同步完成，所有 commit 已推送到 GitHub Pages"
fi
