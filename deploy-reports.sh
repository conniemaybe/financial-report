#!/bin/bash
# ============================================================
# deploy-reports.sh — 将新生成的报告同步到 GitHub Pages 并推送
# 用法: bash deploy-reports.sh "commit说明"
#
# 前置条件（一次性配置，已完成）：
#   git config --global http.proxy "http://127.0.0.1:7892"
#   git config --global https.proxy "http://127.0.0.1:7892"
#   （让 git 主动走小云朵代理，而非被动被 Windows 系统代理劫持）
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

echo -e "${CYAN}deploy-reports.sh${NC} — 同步报告到 GitHub Pages"
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

# === Step 2: 检查变更 + commit ===
echo ""
echo -e "${YELLOW}[2/3]${NC} 检查变更并提交..."
cd "$REPO_DIR"
CHANGED=$(git status --short | wc -l)
if [ "$CHANGED" -eq 0 ]; then
    echo -e "  无任何变更，跳过推送"
    exit 0
fi
git status --short
git add -A
git commit -m "$COMMIT_MSG"

# === Step 3: 推送（git 全局已配置走代理 http://127.0.0.1:7892） ===
echo ""
echo -e "${YELLOW}[3/3]${NC} 推送到 GitHub Pages..."

MAX_RETRIES=3
PUSH_SUCCESS=0
for i in $(seq 1 $MAX_RETRIES); do
    if git push origin main 2>&1; then
        echo ""
        echo -e "${GREEN}✅ 推送成功！(第 $i 次尝试)${NC}"
        echo -e "   GitHub Pages 将在 1-2 分钟后更新"
        PUSH_SUCCESS=1
        break
    else
        if [ $i -lt $MAX_RETRIES ]; then
            echo -e "${YELLOW}  ⚠️ 第 $i 次失败，重试...${NC}"
            sleep 2
        fi
    fi
done

if [ "$PUSH_SUCCESS" -eq 0 ]; then
    echo ""
    echo -e "${RED}❌ 推送失败 ($MAX_RETRIES 次重试均失败)${NC}"
    echo -e "   排查：检查小云朵代理是否运行在 127.0.0.1:7892"
    echo -e "   或手动执行: cd $REPO_DIR && git push origin main"
    exit 1
fi
