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

# 6. 验证 git 本地状态（必须 ahead=0 才算 push 真正成功）
REMAINING=$(git log origin/main..HEAD --oneline 2>/dev/null | wc -l)
if [ "$REMAINING" -gt 0 ]; then
    echo "❌ 验证失败：仍有 $REMAINING 个 commit 未推送到 origin/main"
    echo "   git 显示 push 成功但本地仍 ahead，说明 push 被静默拒绝"
    exit 1
fi
echo "✅ git 验证通过：本地与 origin/main 同步"

# 7. 强制验证 raw.githubusercontent.com 上的实际内容（绕过 GitHub Pages CDN 缓存）
#    ⚠️ 教训：2026-07-02 出现"git push 显示成功但 GitHub Pages 没更新"的误判，
#    根因是 Pages CDN 缓存延迟（5-10分钟），不是 push 失败。
#    raw.githubusercontent.com 是直读 git 仓库，无 CDN，可作为"push 是否真正生效"的判据。
LOCAL_UPDATE_TIME=$(grep -oE '数据更新：[0-9-]+ [0-9:]+' "$REPO_DIR/index.html" | head -1)
if [ -n "$LOCAL_UPDATE_TIME" ]; then
    echo "🔍 验证 raw 仓库内容是否已更新（本地 updateTime: $LOCAL_UPDATE_TIME）"
    sleep 3  # 给 GitHub 一点时间把 commit 同步到 raw CDN
    for i in $(seq 1 3); do
        REMOTE_UPDATE_TIME=$(curl -s "https://raw.githubusercontent.com/conniemaybe/financial-report/main/index.html?_t=$(date +%s%N)" \
            | grep -oE '数据更新：[0-9-]+ [0-9:]+' | head -1)
        if [ "$REMOTE_UPDATE_TIME" = "$LOCAL_UPDATE_TIME" ]; then
            echo "✅ raw 验证通过：远程已更新到 $REMOTE_UPDATE_TIME"
            break
        else
            echo "⚠️ 第 $i 次校验：远程=$REMOTE_UPDATE_TIME（预期 $LOCAL_UPDATE_TIME）"
            if [ "$i" -lt 3 ]; then sleep 5; fi
        fi
    done

    if [ "$REMOTE_UPDATE_TIME" != "$LOCAL_UPDATE_TIME" ]; then
        echo "❌ raw 验证失败：3 次重试后远程仍不是最新内容"
        echo "   可能原因：push 被静默拒绝 / GitHub 后端同步延迟"
        echo "   建议：手动执行 'git push origin main' 确认"
        exit 1
    fi
fi

# 8. 第三层验证：GitHub Actions Pages deploy 状态（2026-07-03 教训补丁）
#    ⚠️ 教训：2026-07-03 push + raw 都验证通过，但 GitHub Pages deploy job 失败
#    （"Deployment failed, try again later." - GitHub 服务端错误）
#    导致网站 1 小时未更新，raw 验证无法发现此问题
#    解决方案：push 后等 60s 查 Actions API，deploy 失败则自动重试

GH_TOKEN=""
# 2026-07-03 修复：token 不能硬编码（触发 GitHub Push Protection）
# 从本地文件读取（该文件不进 git，由用户手动放置）
TOKEN_FILE="$HOME/.workbuddy/astock-simulator/.gh_token"
if [ -f "$TOKEN_FILE" ]; then
    GH_TOKEN=$(cat "$TOKEN_FILE" | tr -d '[:space:]')
elif [ -n "$GH_TOKEN_ENV" ]; then
    GH_TOKEN="$GH_TOKEN_ENV"
else
    echo "⚠️ 未找到 GitHub Token（$TOKEN_FILE 不存在，GH_TOKEN_ENV 未设置）"
    echo "   Pages deploy 验证将跳过，仅做 git + raw 双重验证"
fi
REPO="conniemaybe/financial-report"

if [ -z "$GH_TOKEN" ]; then
    echo "⏭️  跳过 Pages deploy 验证（无 token）"
else
    echo "🏗️  等待 GitHub Pages 构建部署（60s）..."
    sleep 60

    DEPLOY_RESULT=""
    RETRY_COUNT=0
    MAX_DEPLOY_RETRIES=2

    while [ "$RETRY_COUNT" -lt "$MAX_DEPLOY_RETRIES" ]; do
        RETRY_COUNT=$((RETRY_COUNT + 1))

        # 查最新一次 Pages build run
        LATEST_RUN=$(curl -s -H "Authorization: token $GH_TOKEN" \
            "https://api.github.com/repos/$REPO/actions/runs?per_page=1" \
            | grep -oE '"id":[0-9]+' | head -1 | cut -d':' -f2)

        # 查 run 结论
        RUN_CONCLUSION=$(curl -s -H "Authorization: token $GH_TOKEN" \
            "https://api.github.com/repos/$REPO/actions/runs/$LATEST_RUN" \
            | grep -oE '"conclusion":"[^"]*"' | head -1 | cut -d':' -f2 | tr -d '"')

        if [ "$RUN_CONCLUSION" = "success" ]; then
            echo "✅ Pages deploy 验证通过（run $LATEST_RUN conclusion=success）"
            DEPLOY_RESULT="success"
            break
        elif [ "$RUN_CONCLUSION" = "failure" ]; then
            echo "❌ Pages deploy 失败（run $LATEST_RUN conclusion=failure）"
            if [ "$RETRY_COUNT" -lt "$MAX_DEPLOY_RETRIES" ]; then
                echo "🔄 自动重试（$RETRY_COUNT/$MAX_DEPLOY_RETRIES）：创建空 commit 触发重新部署"
                git commit --allow-empty -m "safe-push 自动重试 Pages deploy（第 $RETRY_COUNT 次）" > /dev/null 2>&1
                git $GIT_PROXY_OPTS push origin main > /dev/null 2>&1
                echo "   等待 60s 后重新检查..."
                sleep 60
            else
                echo "❌ Pages deploy 重试 $MAX_DEPLOY_RETRIES 次仍失败"
                DEPLOY_RESULT="failure"
            fi
        else
            echo "⏳ Pages 构建中（conclusion=$RUN_CONCLUSION），再等 30s..."
            sleep 30
            # 再查一次
            RUN_CONCLUSION_2=$(curl -s -H "Authorization: token $GH_TOKEN" \
                "https://api.github.com/repos/$REPO/actions/runs/$LATEST_RUN" \
                | grep -oE '"conclusion":"[^"]*"' | head -1 | cut -d':' -f2 | tr -d '"')
            if [ "$RUN_CONCLUSION_2" = "success" ]; then
                echo "✅ Pages deploy 验证通过（延迟检查）"
                DEPLOY_RESULT="success"
                break
            fi
        fi
    done

    # 最终 Pages 状态查询
    PAGES_STATUS=$(curl -s -H "Authorization: token $GH_TOKEN" \
        "https://api.github.com/repos/$REPO/pages" \
        | grep -oE '"status":"[^"]*"' | head -1 | cut -d':' -f2 | tr -d '"')
    echo "📊 GitHub Pages 最终状态：$PAGES_STATUS"

    if [ "$PAGES_STATUS" != "built" ] && [ "$DEPLOY_RESULT" != "success" ]; then
        echo "⚠️ Pages 状态异常（$PAGES_STATUS），网站可能未更新"
        echo "   访问 https://github.com/$REPO/settings/pages 检查"
    fi
fi

echo "🎉 同步完成（git + raw 验证通过，Pages deploy 验证已根据 token 可用性跳过/执行）"
echo "   网站：https://conniemaybe.github.io/financial-report/"
echo "   注：GitHub Pages CDN 可能有 5-10 分钟缓存延迟，强制刷新请 Ctrl+F5"
