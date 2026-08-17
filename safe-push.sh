#!/bin/bash
# safe-push.sh — 推送 financial-report 到 GitHub Pages（自适应代理，开/关都能推）
# 用法: bash /e/temp/financial-report/safe-push.sh "commit message"
#
# 原理：先探测 127.0.0.1:7892 端口是否在监听
#   - 开着代理 → 走代理（git -c http.proxy=http://127.0.0.1:7892）
#   - 没开代理 → 直连（不传 proxy 配置，用 git 默认行为）
#   不依赖全局 git config，避免"代理关了推不了"的陷阱
#
# P0 修复 (2026-08-07): Git for Windows 自带的 minimal bash 缺 seq / sleep 等外部命令，
# 导致原 `for i in $(seq ...)` + `sleep N` 直接报错跳过，"3 次重试"从来没真正执行过。
# 改用 bash 内置 C 风格 for 循环 + Python 实现跨平台 sleep。

set -e
REPO_DIR="/e/temp/financial-report"
COMMIT_MSG="${1:-auto sync}"
MAX_RETRIES=3

# 跨平台 sleep：Git for Windows minimal bash 无 sleep 命令，用 Python time.sleep 替代
psleep() {
    python -c "import time; time.sleep($1)" 2>/dev/null || python3 -c "import time; time.sleep($1)" 2>/dev/null || true
}

cd "$REPO_DIR"

# 0. Token 健康检查（架构审查 #203，2026-07-23 新增）
#    痛点：token 过期导致 push 401，但等用户发现已经晚了一天
#    方案：push 前先用 GitHub API 验证 token，失效立即告警+退出
TOKEN_HEALTH_SCRIPT="C:/Users/conniehe/.workbuddy/astock-simulator/scripts/gh_token_health_check.py"
if [ -f "$TOKEN_HEALTH_SCRIPT" ] && command -v python &>/dev/null; then
    echo "🔑 执行 token 健康检查..."
    if ! python "$TOKEN_HEALTH_SCRIPT"; then
        echo "❌ Token 健康检查失败，拒绝 push（避免 401 静默失败）"
        echo "   修复：见告警消息中的步骤，或手动执行 gh_token_health_check.py 查看详情"
        exit 1
    fi
fi

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

# 4. 推送策略：直连优先，代理兜底（2026-08-17 P0 反转）
# 旧逻辑：检测到 Clash 7892 端口就走代理推送 → 实测 git 显式走该代理会无限挂起
#   （Clash 规则本身分流 GitHub，git 再指定代理端口等于双层代理死锁，8/17 卡死根因之一）。
# 新逻辑：① 第 1 轮直连（不传任何 proxy 配置，避免 libcurl getsockname 报错）
#         ② 直连失败才试代理（限超时 60s，防挂起）
PUSH_TIMEOUT=60   # 单次 push 超时秒数

try_push() {
    # $1 = 描述; 其余为 git 额外参数
    local desc="$1"; shift
    echo "🔄 $desc ..."
    if timeout $PUSH_TIMEOUT git "$@" push origin main 2>&1; then
        return 0
    fi
    return 1
}

PROXY_OPTS="-c http.proxy=http://127.0.0.1:$PROXY_PORT -c https.proxy=http://127.0.0.1:$PROXY_PORT"

# 4.8 防凭据交互卡死护栏（2026-08-17 P0 修复）
# 事故：remote URL 的 token 曾被重写为空值（conniemaybe:@github.com），
#       push 回退到 Windows credential-helper-selector 交互弹窗，非交互环境永久挂起
#       （8/17 晨报收尾卡死 41 分钟的根因）。
# 护栏：① push 前自检 remote token 非空，空则立即从 .gh_token 重嵌并报警
#       ② 环境强制 GIT_TERMINAL_PROMPT=0（凭据缺失时报错退出而非挂起等待输入）
REMOTE_URL=$(git remote get-url origin)
case "$REMOTE_URL" in
    *":@github.com"*|*"github.com"*":@"*)
        echo "❌ remote token 为空（$REMOTE_URL 中凭据位缺失），将导致 credential 弹窗卡死"
        if [ -f "$HOME/.workbuddy/astock-simulator/.gh_token" ]; then
            GH_TOKEN=$(tr -d '\r\n ' < "$HOME/.workbuddy/astock-simulator/.gh_token")
            git remote set-url origin "https://conniemaybe:${GH_TOKEN}@github.com/conniemaybe/financial-report.git"
            echo "🔧 已自动重嵌 token（来自 .gh_token），继续推送"
        else
            echo "   且 .gh_token 不存在，无法自动修复，退出"
            exit 1
        fi
        ;;
esac
export GIT_TERMINAL_PROMPT=0

# 5. 推送：直连优先（每轮最多 2 次直连），全失败后若代理在线再试 1 次代理
# P0 修复 (2026-08-07): 原 `for i in $(seq 1 $MAX_RETRIES)` 因 `seq: command not found`
# 直接跳过整个循环。改用 bash 内置 C 风格 for 循环，不依赖外部命令。
PUSH_SUCCESS=0
for ((i=1; i<=MAX_RETRIES; i++)); do
    if try_push "第 $i 次推送（直连）"; then
        echo "✅ 第 $i 次 PUSH 成功（直连）"
        PUSH_SUCCESS=1
        break
    fi
    echo "⚠️ 第 $i 次直连 push 失败"
    if [ "$i" -lt "$MAX_RETRIES" ]; then
        psleep 2
    fi
done

# 5.5 代理兜底：直连 3 次全败 + Clash 端口在监听 → 走代理最后一搏（限 60s）
if [ "$PUSH_SUCCESS" -eq 0 ] && [ "$PROXY_ACTIVE" -eq 1 ]; then
    echo "🛟 直连全部失败，检测到代理 $PROXY_PORT 在线 → 代理兜底尝试（限 ${PUSH_TIMEOUT}s）"
    if try_push "代理兜底" $PROXY_OPTS; then
        echo "✅ 代理兜底 PUSH 成功"
        PUSH_SUCCESS=1
    else
        echo "⚠️ 代理兜底也失败"
    fi
fi

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
    psleep 3  # 给 GitHub 一点时间把 commit 同步到 raw CDN
    for ((i=1; i<=3; i++)); do
        REMOTE_UPDATE_TIME=$(curl -s "https://raw.githubusercontent.com/conniemaybe/financial-report/main/index.html?_t=$(date +%s%N)" \
            | grep -oE '数据更新：[0-9-]+ [0-9:]+' | head -1)
        if [ "$REMOTE_UPDATE_TIME" = "$LOCAL_UPDATE_TIME" ]; then
            echo "✅ raw 验证通过：远程已更新到 $REMOTE_UPDATE_TIME"
            break
        else
            echo "⚠️ 第 $i 次校验：远程=$REMOTE_UPDATE_TIME（预期 $LOCAL_UPDATE_TIME）"
            if [ "$i" -lt 3 ]; then psleep 5; fi
        fi
    done

    if [ "$REMOTE_UPDATE_TIME" != "$LOCAL_UPDATE_TIME" ]; then
        echo "❌ raw 验证失败：3 次重试后远程仍不是最新内容"
        echo "   可能原因：push 被静默拒绝 / GitHub 后端同步延迟"
        echo "   建议：手动执行 'git push origin main' 确认"
        exit 1
    fi
fi

# 8. 第三层验证：GitHub Actions Pages deploy 状态
#    ⚠️ 教训链：
#      - 2026-07-03: push + raw 都过，但 Pages deploy job 失败 → 加了 deploy 验证
#      - 2026-07-06: deploy 失败后用"空 commit 重试"，GitHub 服务端故障时空 commit 也失败；
#                    且 60s 太短，GitHub 排队 80s+ 还没开始执行 → 重试基本无效
#    解决方案（v2）：
#      a) 等 Pages run 完成用轮询，不再固定 sleep 60
#      b) 失败重试用 GitHub API rerun（不新建 commit），避免污染 git 历史
#      c) 指数退避：30s → 90s → 180s，给 GitHub 服务端恢复时间
#      d) 最终失败明确告警 + 建议

GH_TOKEN=""
# 从本地文件读取（不进 git，避免触发 GitHub Push Protection）
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

# 等待 Pages run 到终态（completed）。最多等 6 分钟。
# 用法: wait_pages_run_complete <run_id>  → 输出 conclusion 或 "timeout"
wait_pages_run_complete() {
    local RUN_ID="$1"
    local MAX_WAIT=360  # 6 分钟
    local ELAPSED=0
    while [ "$ELAPSED" -lt "$MAX_WAIT" ]; do
        local STATUS_CONCL=$(curl -s -H "Authorization: token $GH_TOKEN" \
            "https://api.github.com/repos/$REPO/actions/runs/$RUN_ID" \
            | python -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('status','?') + '|' + str(d.get('conclusion') or '-'))
except:
    print('?|?')
" 2>/dev/null)
        local STATUS=$(echo "$STATUS_CONCL" | cut -d'|' -f1)
        local CONCL=$(echo "$STATUS_CONCL" | cut -d'|' -f2)
        if [ "$STATUS" = "completed" ]; then
            echo "$CONCL"
            return 0
        fi
        psleep 15
        ELAPSED=$((ELAPSED + 15))
    done
    echo "timeout"
    return 1
}

if [ -z "$GH_TOKEN" ]; then
    echo "⏭️  跳过 Pages deploy 验证（无 token）"
    DEPLOY_RESULT="skipped"
else
    echo "🏗️  查询最新 Pages run 状态（轮询，最长 6 分钟）..."

    # 拿最新一次 Pages workflow run
    LATEST_RUN=$(curl -s -H "Authorization: token $GH_TOKEN" \
        "https://api.github.com/repos/$REPO/actions/runs?per_page=1" \
        | python -c "
import sys, json
d = json.load(sys.stdin)
runs = d.get('workflow_runs', [])
print(runs[0]['id'] if runs else '')
" 2>/dev/null)

    DEPLOY_RESULT=""
    if [ -z "$LATEST_RUN" ]; then
        echo "⚠️ 没找到 Pages run，跳过 deploy 验证"
        DEPLOY_RESULT="no_run"
    else
        echo "   最新 run: $LATEST_RUN，开始轮询..."
        CONCL=$(wait_pages_run_complete "$LATEST_RUN")
        echo "   run $LATEST_RUN 结论: $CONCL"

        if [ "$CONCL" = "success" ]; then
            echo "✅ Pages deploy 验证通过（run $LATEST_RUN）"
            DEPLOY_RESULT="success"
        elif [ "$CONCL" = "failure" ] || [ "$CONCL" = "timeout" ]; then
            # 失败重试：用 API rerun，指数退避
            MAX_DEPLOY_RETRIES=3
            for ((RETRY=1; RETRY<=MAX_DEPLOY_RETRIES; RETRY++)); do
                echo "❌ Pages deploy $CONCL，触发 API rerun（$RETRY/$MAX_DEPLOY_RETRIES）"
                HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
                    -H "Authorization: token $GH_TOKEN" \
                    "https://api.github.com/repos/$REPO/actions/runs/$LATEST_RUN/rerun")
                if [ "$HTTP_CODE" != "201" ] && [ "$HTTP_CODE" != "200" ]; then
                    echo "   rerun API 返回 HTTP $HTTP_CODE（非 201/200）"
                fi
                # 指数退避：60s → 120s → 180s
                BACKOFF=$((60 * RETRY))
                echo "   退避 ${BACKOFF}s 后重新检查..."
                psleep "$BACKOFF"
                CONCL=$(wait_pages_run_complete "$LATEST_RUN")
                echo "   rerun 后 run $LATEST_RUN 结论: $CONCL"
                if [ "$CONCL" = "success" ]; then
                    echo "✅ Pages deploy 第 $RETRY 次重试成功"
                    DEPLOY_RESULT="success"
                    break
                fi
            done
            if [ "$DEPLOY_RESULT" != "success" ]; then
                DEPLOY_RESULT="failure"
            fi
        else
            echo "⚠️ Pages run 结论异常：$CONCL"
            DEPLOY_RESULT="unknown"
        fi
    fi
fi

# 最终 Pages 状态查询
if [ -n "$GH_TOKEN" ]; then
    PAGES_STATUS=$(curl -s -H "Authorization: token $GH_TOKEN" \
        "https://api.github.com/repos/$REPO/pages" \
        | python -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('status','?'))
except:
    print('?')
" 2>/dev/null)
    echo "📊 GitHub Pages 最终状态：$PAGES_STATUS"
fi

# === v11.18 新增 #020：Pages build 状态校验（第四重验证）===
# 背景：2026-07-18~07-20 线上停更 3 天，根因是 Jekyll 构建失败（缺 .nojekyll），
# 但 safe-push 只验证 actions/runs workflow，legacy Pages 模式不走 workflow，
# 导致构建失败完全沉默。现在增加 /pages/builds API 校验。
#
# 注意：即使仓库走 Actions workflow 部署 Pages，/pages/builds 也会记录部署结果，
# 所以这个校验对两种模式都有效。
PAGES_BUILD_OK=0
if [ -n "$GH_TOKEN" ]; then
    echo "🏗️  校验最新一次 Pages build 状态（第四重验证）..."
    psleep 10  # 等 Pages build pipeline 启动
    for ((i=1; i<=6; i++)); do
        LATEST_BUILD_STATUS=$(curl -s -H "Authorization: token $GH_TOKEN" \
            "https://api.github.com/repos/$REPO/pages/builds?per_page=1" \
            | python -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if isinstance(d, list) and d:
        print(d[0].get('status','?'))
    else:
        print('?')
except:
    print('?')
" 2>/dev/null)
        if [ "$LATEST_BUILD_STATUS" = "built" ]; then
            echo "✅ Pages build 验证通过（status: built）"
            PAGES_BUILD_OK=1
            break
        elif [ "$LATEST_BUILD_STATUS" = "errored" ] || [ "$LATEST_BUILD_STATUS" = "errored" ]; then
            echo "❌ Pages build 失败（status: $LATEST_BUILD_STATUS）"
            echo "   可能原因：Jekyll 处理失败（检查 .nojekyll 是否存在）/ 文件名含特殊字符 / 仓库配置问题"
            echo "   排查：https://github.com/$REPO/settings/pages"
            break
        else
            echo "⏳ 第 $i 次查询：status=$LATEST_BUILD_STATUS（构建中），15s 后重试..."
            psleep 15
        fi
    done
fi

echo ""
if [ "$DEPLOY_RESULT" = "success" ] && { [ -z "$GH_TOKEN" ] || [ "$PAGES_BUILD_OK" -eq 1 ]; }; then
    echo "🎉 同步完成（git + raw + Pages deploy + Pages build 四重验证全部通过）"
    echo "   网站：https://conniemaybe.github.io/financial-report/"
    echo "   注：GitHub Pages CDN 可能有 1-5 分钟缓存延迟，强制刷新请 Ctrl+F5"
elif [ "$DEPLOY_RESULT" = "skipped" ] || [ "$DEPLOY_RESULT" = "no_run" ]; then
    if [ "$PAGES_BUILD_OK" -eq 1 ]; then
        echo "🎉 同步完成（git + raw + Pages build 验证通过；Actions deploy 验证已跳过）"
    else
        echo "🎉 同步完成（git + raw 验证通过；Pages 相关验证已跳过）"
        echo "   建议：检查 token 配置以启用 Pages build 自动验证"
    fi
else
    echo "⚠️  ⚠️  ⚠️  部署验证失败！"
    if [ "$DEPLOY_RESULT" != "success" ]; then
        echo "   - Actions workflow deploy: 失败"
    fi
    if [ "$PAGES_BUILD_OK" -ne 1 ] && [ -n "$GH_TOKEN" ]; then
        echo "   - Pages build: 失败或未确认"
    fi
    echo "   请手动访问 https://github.com/$REPO/actions 和 https://github.com/$REPO/settings/pages 查看失败原因"
    echo "   若是 GitHub 服务端故障，等 10-30 分钟后手动执行："
    echo "     bash /e/temp/financial-report/safe-push.sh 'retry after GitHub recovered'"
    # 注意：不 exit 1，因为 git push 已成功，只是 Pages 部署/构建失败
    # 让调用方看到告警但流程继续，避免阻塞 automation 后续步骤
fi
