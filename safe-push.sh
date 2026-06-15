#!/bin/bash
# safe-push.sh - 自动清除 git 代理后推送（防止 Clash 残留代理 + sandbox 拦截导致 push 失败）
# 用法: bash /c/temp/financial-report/safe-push.sh "commit message"
# 重要：这是自动化任务同步网站的关键脚本，push 失败必须重试！

set -e
REPO_DIR="/c/temp/financial-report"
COMMIT_MSG="${1:-auto sync}"
MAX_RETRIES=3

cd "$REPO_DIR"

# 1. 清除 git 全局代理配置（Clash 残留的 http.proxy=127.0.0.1:7892 会拦截 push）
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

# 4. 推送（多次重试，sandbox 可能偶发拦截 git-remote-https 子进程）
echo "🔄 正在推送..."
for i in $(seq 1 $MAX_RETRIES); do
    # 清除环境变量代理 + git 配置代理双保险
    if env HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= NO_PROXY="*" \
       git -c http.proxy= -c https.proxy= push origin main 2>&1; then
        echo "✅ 第 $i 次 PUSH 成功"
        break
    else
        echo "⚠️ 第 $i 次 push 失败，清除代理后重试..."
        git config --global --unset http.proxy 2>/dev/null || true
        git config --global --unset https.proxy 2>/dev/null || true
        if [ "$i" -eq "$MAX_RETRIES" ]; then
            echo "❌ PUSH 失败 ($MAX_RETRIES 次重试均失败)"
            echo "⚠️ 网站将无法同步本次更新，请手动运行："
            echo "   cd C:/temp/financial-report && git push origin main"
            exit 1
        fi
        sleep 2
    fi
done

# 5. 验证：检查是否有未推送的 commit
REMAINING=$(git log origin/main..HEAD --oneline 2>/dev/null | wc -l)
if [ "$REMAINING" -gt 0 ]; then
    echo "⚠️ 仍有 $REMAINING 个 commit 未推送"
    exit 1
else
    echo "🎉 同步完成，所有 commit 已推送到 GitHub Pages"
fi
