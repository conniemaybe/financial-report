#!/bin/bash
# safe-push.sh — 推送 financial-report 到 GitHub Pages（自适应代理，开/关都能推）
# 用法: bash /c/temp/financial-report/safe-push.sh "commit message"
#
# 原理：先探测 127.0.0.1:7892 端口是否在监听
#   - 开着代理 → 走代理（git -c http.proxy=http://127.0.0.1:7892）
#   - 没开代理 → 直连（git -c http.proxy= 清空配置）
#   不依赖全局 git config，避免"代理关了推不了"的陷阱

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

# 3. 探测代理端口是否在监听
PROXY_PORT=7892
if timeout 2 bash -c "echo > /dev/tcp/127.0.0.1/$PROXY_PORT" 2>/dev/null; then
    PROXY_ACTIVE=1
else
    PROXY_ACTIVE=0
fi

# 4. 根据代理状态决定推送策略
if [ "$PROXY_ACTIVE" -eq 1 ]; then
    echo "🌐 检测到代理在 127.0.0.1:$PROXY_PORT → 走代理推送"
    GIT_PROXY_OPTS="-c http.proxy=http://127.0.0.1:$PROXY_PORT -c https.proxy=http://127.0.0.1:$PROXY_PORT"
else
    echo "📡 未检测到代理 → 直连推送"
    GIT_PROXY_OPTS="-c http.proxy= -c https.proxy="
fi

# 5. 推送（3次重试）
echo "🔄 正在推送..."
PUSH_SUCCESS=0
for i in $(seq 1 $MAX_RETRIES); do
    if git $GIT_PROXY_OPTS push origin main 2>&1; then
        echo "✅ 第 $i 次 PUSH 成功"
        PUSH_SUCCESS=1
        break
    else
        echo "⚠️ 第 $i 次 push 失败"
        if [ "$i" -lt "$MAX_RETRIES" ]; then
            sleep 2
        fi
    fi
done

if [ "$PUSH_SUCCESS" -eq 0 ]; then
    echo "❌ PUSH 失败 ($MAX_RETRIES 次重试均失败)"
    echo "   排查：① 确认网络能访问 github.com  ② 代理端口是否正确"
    exit 1
fi

# 6. 验证
REMAINING=$(git log origin/main..HEAD --oneline 2>/dev/null | wc -l)
if [ "$REMAINING" -gt 0 ]; then
    echo "⚠️ 仍有 $REMAINING 个 commit 未推送"
    exit 1
else
    echo "🎉 同步完成，所有 commit 已推送到 GitHub Pages"
fi
