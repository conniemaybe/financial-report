#!/bin/bash
# safe-push.sh - 自动清除 git 代理后推送（防止 Clash 残留代理导致 push 失败）
# 用法: bash safe-push.sh "commit message"

set -e
REPO_DIR="/c/temp/financial-report"
COMMIT_MSG="${1:-auto sync}"

cd "$REPO_DIR"

# 1. 清除 git 全局代理配置（Clash 残留的 http.proxy=127.0.0.1:7892）
git config --global --unset http.proxy 2>/dev/null || true
git config --global --unset https.proxy 2>/dev/null || true

# 2. 暂存所有变更
git add -A

# 3. 检查是否有变更需要提交
if git diff --cached --quiet; then
    echo "ℹ️  没有变更需要提交"
else
    git commit -m "$COMMIT_MSG"
    echo "✅ commit 完成: $COMMIT_MSG"
fi

# 4. 推送（强制不走代理）
echo "🔄 正在推送..."
if git -c http.proxy= -c https.proxy= push origin main 2>&1; then
    echo "✅ PUSH 成功"
else
    echo "⚠️ 第一次 push 失败，重试（再次清除代理）..."
    git config --global --unset http.proxy 2>/dev/null || true
    git config --global --unset https.proxy 2>/dev/null || true
    if git -c http.proxy= -c https.proxy= push origin main 2>&1; then
        echo "✅ 重试 PUSH 成功"
    else
        echo "❌ PUSH 失败 - 请手动检查 git config --global --list 是否有残留 proxy"
        exit 1
    fi
fi

# 5. 验证：检查是否有未推送的 commit
REMAINING=$(git log origin/main..HEAD --oneline 2>/dev/null | wc -l)
if [ "$REMAINING" -gt 0 ]; then
    echo "⚠️ 仍有 $REMAINING 个 commit 未推送"
    exit 1
else
    echo "🎉 同步完成，所有 commit 已推送到 GitHub"
fi
