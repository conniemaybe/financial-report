#!/bin/bash
# ============================================================
# deploy-reports.sh — 将新生成的报告同步到 GitHub Pages 并推送
# 用法: bash deploy-reports.sh "commit说明"
# 会自动:
#   1. 从 E:\workbuddyProject\Project_01_invest\ 拷贝最新报告
#   2. 检查是否有变更
#   3. 清除所有代理（git config + 环境变量 env 前缀 + Windows IE 系统代理）
#   4. commit + push (3次重试，每次 env 前缀清除代理)
# ============================================================

REPO_DIR="/c/temp/financial-report"
SRC_DIR="/e/workbuddyProject/Project_01_invest"
COMMIT_MSG="${1:-auto: sync reports to GitHub Pages}"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}📝 deploy-reports.sh${NC} — 同步报告到 GitHub Pages"
echo ""

# === Step 1: 拷贝报告文件 ===
echo -e "${YELLOW}[1/4]${NC} 拷贝报告文件..."
if [ ! -d "$SRC_DIR" ]; then
    echo -e "${RED}❌ 源目录不存在: $SRC_DIR${NC}"
    exit 1
fi

REPORT_COUNT=0
for f in "$SRC_DIR"/*.html; do
    [ -f "$f" ] || continue
    fname=$(basename "$f")
    dest="$REPO_DIR/reports/$fname"
    # 只在文件有变化时拷贝
    if [ ! -f "$dest" ] || ! diff -q "$f" "$dest" > /dev/null 2>&1; then
        cp "$f" "$dest"
        echo -e "  ${GREEN}更新${NC} $fname"
        ((REPORT_COUNT++))
    fi
done

if [ "$REPORT_COUNT" -eq 0 ]; then
    echo -e "  无新报告需要更新"
fi

# === Step 2: 检查 index.html 是否有变化 ===
echo -e "${YELLOW}[2/4]${NC} 检查变更..."
cd "$REPO_DIR"
CHANGED=$(git status --short | wc -l)
if [ "$CHANGED" -eq 0 ]; then
    echo -e "  无任何变更，跳过推送"
    exit 0
fi
git status --short

# === Step 3: 清除所有代理（git config + 环境变量 + Windows 系统代理） ===
echo ""
echo -e "${YELLOW}[3/4]${NC} 清除所有代理残留..."

# 3a. git config 代理（删除而非设为空字符串，更干净）
git config --global --unset http.proxy 2>/dev/null || true
git config --global --unset https.proxy 2>/dev/null || true

# 3b. Windows IE 系统代理（Clash 写入的 ProxyEnable=1 会被 WinINET 子进程读取）
powershell.exe -NoProfile -Command "
    \$p = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings';
    if (\$p.ProxyEnable -ne 0) {
        Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -Name ProxyEnable -Value 0;
        Write-Output '  Windows IE 代理已禁用';
    } else {
        Write-Output '  Windows IE 代理已是禁用状态';
    }
" 2>/dev/null

echo -e "  ${GREEN}✓${NC} 代理已清除（git config + Windows 系统代理）"
echo -e "  ${YELLOW}注意${NC}: 环境变量 HTTP_PROXY 会在每次 push 命令前通过 env 前缀清除"

# === Step 4: commit + push (3次重试，每次都清除环境变量代理) ===
echo ""
echo -e "${YELLOW}[4/4]${NC} 提交并推送..."
git add -A
git commit -m "$COMMIT_MSG" 2>/dev/null || echo "  (无可提交的内容或已提交)"

MAX_RETRIES=3
PUSH_SUCCESS=0
for i in $(seq 1 $MAX_RETRIES); do
    # 关键：用 env 前缀清除当前 session 的环境变量代理 + NO_PROXY=* 禁用所有代理 + git -c 内联覆盖
    if env HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= NO_PROXY="*" \
       git -c http.proxy= -c https.proxy= push origin main 2>&1; then
        echo ""
        echo -e "${GREEN}✅ 推送成功！(第 $i 次尝试)${NC}"
        echo -e "   GitHub Pages 将在 1-2 分钟后更新"
        PUSH_SUCCESS=1
        break
    else
        if [ $i -lt $MAX_RETRIES ]; then
            echo -e "${YELLOW}  ⚠️ 第 $i 次失败，清除代理后重试...${NC}"
            # 再次清除所有代理
            git config --global --unset http.proxy 2>/dev/null || true
            git config --global --unset https.proxy 2>/dev/null || true
            sleep 2
        fi
    fi
done

if [ "$PUSH_SUCCESS" -eq 0 ]; then
    echo ""
    echo -e "${RED}❌ 推送失败 ($MAX_RETRIES 次重试均失败)${NC}"
    echo -e "   请手动执行: cd $REPO_DIR && git push origin main"
    exit 1
fi
