#!/bin/bash
# deploy-reports.sh — 已于 2026-09-10 退役，统一转发到 safe-push.sh
#
# 退役原因：本脚本原含裸 git push（无认证头、无凭据禁用），在 Windows 凭据管理器
# 被系统清理清空时会触发 credential-helper-selector GUI 弹窗并挂起无人值守会话
# （9/7、9/10 事故链一环）。网站部署唯一通道已收敛为 safe-push.sh
# （内置 token 认证头 + credential.helper 禁用 + 重试 + 代理兜底）。
#
# 所有对 deploy-reports.sh 的调用自动转发到 safe-push.sh，参数语义不变。

exec bash "$(dirname "$0")/safe-push.sh" "$@"
