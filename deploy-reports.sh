#!/bin/bash
# ============================================================
# deploy-reports.sh — 将新生成的报告同步到 GitHub Pages 本地仓库（不含 push）
# 用法: bash deploy-reports.sh "commit说明"
#
# ⚠️ 重要：此脚本只做 拷贝+commit，不做 push！
#   因为 git push 需要「先在 PowerShell 进程禁用 Windows 系统代理，
#    再在 Bash 进程执行 push」两步跨进程操作，无法在单个 bash 脚本内完成。
#
#   push 操作请由调用方（AI/自动化任务）在脚本执行完毕后，
#   按以下顺序执行两个独立工具调用：
#     1. PowerShell: Set-ItemProperty 'HKCU:\...\Internet Settings' -Name ProxyEnable -Value 0
#     2. Bash: cd /c/temp/financial-report && git push origin main
#
#   或调用 push-after-deploy.sh（封装了两步，但需 dangerouslyDisableSandbox）
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

echo -e "${CYAN}deploy-reports.sh${NC} — 同步报告到本地仓库（push 请单独执行）"
echo ""

# === Step 1: 拷贝报告文件 ===
echo -e "${YELLOW}[1/3]${NC} 拷贝报告文件..."
if [ ! -d "$SRC_DIR" ]; then
    echo -e "${RED}源目录不存在: $SRC_DIR${NC}"
    exit 1
fi

REPORT_COUNT=0
for f in "$SRC_DIR"/*.html; do
    [ -f "$f" ] || continue
    fname=$(basename "$f")
    dest="$REPO_DIR/reports/$fname"
    if [ ! -f "$dest" ] || ! diff -q "$f" "$dest" > /dev/null 2>&1; then
        cp "$f" "$dest"
        echo -e "  ${GREEN}更新${NC} $fname"
        ((REPORT_COUNT++))
    fi
done

if [ "$REPORT_COUNT" -eq 0 ]; then
    echo -e "  无新报告需要更新"
fi

# === Step 2: 检查是否有变更 ===
echo ""
echo -e "${YELLOW}[2/3]${NC} 检查变更..."
cd "$REPO_DIR"
CHANGED=$(git status --short | wc -l)
if [ "$CHANGED" -eq 0 ]; then
    echo -e "  无任何变更，无需推送"
    rm -f "$REPO_DIR/.need-push"
    exit 0
fi
git status --short

# === Step 3: commit ===
echo ""
echo -e "${YELLOW}[3/3]${NC} 提交..."
git config --global --unset http.proxy 2>/dev/null || true
git config --global --unset https.proxy 2>/dev/null || true
git add -A
git commit -m "$COMMIT_MSG" 2>/dev/null || echo "  (无可提交的内容或已提交)"

# 写入 flag 文件，告知调用方需要 push
touch "$REPO_DIR/.need-push"

echo ""
echo -e "${YELLOW}本地 commit 完成。需要推送时执行：${NC}"
echo -e "  ${CYAN}bash /c/temp/financial-report/push-after-deploy.sh${NC}"
echo -e "  或由 AI 分别调用 PowerShell(禁用代理) + Bash(git push)"
