#!/bin/bash
# ============================================================
# deploy-reports.sh — 将新生成的报告同步到 GitHub Pages 并推送
# 用法: bash deploy-reports.sh "commit说明"
# 会自动:
#   1. 从 E:\workbuddyProject\Project_01_invest\ 拷贝最新报告
#   2. 更新 index.html (如果主页面有变化)
#   3. 清除 git 代理残留
#   4. commit + push (3次重试)
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

# === Step 3: 清除 git 代理 ===
echo ""
echo -e "${YELLOW}[3/4]${NC} 清除 git 代理残留..."
git config --global http.proxy ""
git config --global https.proxy ""
echo -e "  ${GREEN}✓${NC} 代理已清除"

# === Step 4: commit + push (3次重试) ===
echo ""
echo -e "${YELLOW}[4/4]${NC} 提交并推送..."
git add -A
git commit -m "$COMMIT_MSG" 2>/dev/null || echo "  (无可提交的内容或已提交)"

MAX_RETRIES=3
for i in $(seq 1 $MAX_RETRIES); do
    if git push origin main 2>&1; then
        echo ""
        echo -e "${GREEN}✅ 推送成功！(第 $i 次尝试)${NC}"
        echo -e "   GitHub Pages 将在 1-2 分钟后更新"
        exit 0
    else
        if [ $i -lt $MAX_RETRIES ]; then
            echo -e "${YELLOW}  ⚠️ 第 $i 次失败，清除代理后重试...${NC}"
            git config --global http.proxy ""
            git config --global https.proxy ""
            sleep 2
        fi
    fi
done

echo ""
echo -e "${RED}❌ 推送失败 ($MAX_RETRIES 次重试均失败)${NC}"
echo -e "   请手动执行: cd $REPO_DIR && git push origin main"
exit 1
