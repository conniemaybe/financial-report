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

# === Step 3+4: commit + 清除Windows系统代理 + 立即push（三步必须连续执行） ===
# 核心原理：Clash 每隔几秒会重写 HKCU ProxyEnable=1，
# 而 Windows 版 git-remote-https.exe 通过 WinINET API 读取该值，
# 强制走 127.0.0.1:7892 代理（即使 git config/环境变量都设为空也无济于事）。
# 唯一有效方案：禁用系统代理后【零延迟】执行 git push，抢在 Clash 改回之前完成。
echo ""
echo -e "${YELLOW}[3/4]${NC} 提交变更..."
git add -A
git commit -m "$COMMIT_MSG" 2>/dev/null || echo "  (无可提交的内容或已提交)"

# 顺带清除 git config 代理（有些环境会写入）
git config --global --unset http.proxy 2>/dev/null || true
git config --global --unset https.proxy 2>/dev/null || true

echo ""
echo -e "${YELLOW}[4/4]${NC} 禁用 Windows 系统代理后立即推送..."
MAX_RETRIES=5  # 增加重试次数，因为 Clash 可能在我们 push 前抢写
PUSH_SUCCESS=0
for i in $(seq 1 $MAX_RETRIES); do
    # ★ 关键：用 PowerShell 禁用系统代理后【立即】在同一秒内执行 git push ★
    # powershell.exe 和 git push 必须紧挨着，中间不能有 sleep 或其他耗时操作
    powershell.exe -NoProfile -Command "Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -Name ProxyEnable -Value 0" 2>/dev/null

    # env 前缀清除环境变量代理 + git -c 内联覆盖 git config 代理（双保险）
    if env HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= NO_PROXY="*" \
       git -c http.proxy= -c https.proxy= push origin main 2>&1; then
        echo ""
        echo -e "${GREEN}✅ 推送成功！(第 $i 次尝试)${NC}"
        echo -e "   GitHub Pages 将在 1-2 分钟后更新"
        PUSH_SUCCESS=1
        break
    else
        if [ $i -lt $MAX_RETRIES ]; then
            echo -e "${YELLOW}  ⚠️ 第 $i 次失败（可能 Clash 抢写代理），立即重试...${NC}"
            # 不 sleep！直接重试，减少 Clash 抢写窗口
        fi
    fi
done

if [ "$PUSH_SUCCESS" -eq 0 ]; then
    echo ""
    echo -e "${RED}❌ 推送失败 ($MAX_RETRIES 次重试均失败)${NC}"
    echo -e "   根因：Clash 持续重写 Windows 系统代理，git-remote-https 强制走代理失败"
    echo -e "   建议手动执行（需 Clash 已退出或改为 TUN 模式）："
    echo -e "     powershell -Command \"Set-ItemProperty 'HKCU:\\...\\Internet Settings' -Name ProxyEnable -Value 0\""
    echo -e "     cd $REPO_DIR && git push origin main"
    exit 1
fi
