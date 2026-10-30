#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_push.py — 无代理 GitHub 推送（Git Data API 通道，2026-10-30 新增）

背景：github.com:443 直连被阻断（TCP 超时），Clash 7892 关闭时 git push 必败。
但 api.github.com:443 直连畅通且 token 有效（实测 2026-10-30）。

原理：用 Git Data API 在远端直接构造 commit（blobs → tree → commit → update ref），
绕过 github.com 的 git 协议端口。推送的是"本地当前 HEAD 的文件快照 vs 远端 head"
的差异文件，单 commit 落地。

副作用与对冲：
  API commit 不在本地 git 历史中 → 下次 git push 会 non-fast-forward。
  对冲：成功后把 API sha 写入 .api_push_head，safe-push.sh 在代理恢复的
  git push 前检测到它则先 fetch+merge -X ours（内容同快照，merge 为无操作）再推。

用法：
  python api_push.py                    # 推送本地 HEAD 相对远端 main 的变更
  python api_push.py --dry-run          # 只列变更不推送
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = "conniemaybe/financial-report"
SITE = Path(__file__).parent
TOKEN_FILE = Path.home() / ".workbuddy/astock-simulator/.gh_token"
API_HEAD_FILE = SITE / ".api_push_head"

# 关键防御：沙箱/Clash 关闭时残留的代理环境变量会把 urllib 劫持到不可用端口（502）。
# 本通道的设计前提就是"无代理直连 api.github.com"，启动时强制清掉。
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))


def _token() -> str:
    return TOKEN_FILE.read_text(encoding="utf-8").strip().strip("\r\n ")


def _api(method: str, path: str, body: dict | None = None, expect: tuple = (200, 201)) -> dict:
    url = f"https://api.github.com/{path}"
    data = json.dumps(body).encode() if body else None
    last_err = None
    for attempt in range(4):  # 2026-10-30 实测直连抖动约 50%，必重试
        req = urllib.request.Request(url, method=method, data=data, headers={
            "Authorization": f"token {_token()}",
            "User-Agent": "financial-report-sync",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                if r.status in expect:
                    return json.loads(r.read().decode() or "{}")
                raise RuntimeError(f"HTTP {r.status}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")
        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            print(f"   ↻ 网络抖动重试 {attempt + 1}/4（{wait}s 后）: {type(e).__name__}")
            time.sleep(wait)
    raise RuntimeError(f"API {method} {path} 重试 4 次仍失败: {last_err}")


def _git(args: list[str]) -> str:
    r = subprocess.run(["git", "-C", str(SITE)] + args,
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    opts = ap.parse_args()

    # 1) 远端 head
    ref = _api("GET", f"repos/{REPO}/git/ref/heads/main")
    remote_sha = ref["object"]["sha"]
    local_sha = _git(["rev-parse", "HEAD"])

    # 2) 变更文件：远端 head 不在本地历史（上次 API push 会形成 fork），git diff 不可用。
    #    改用 tree 快照对比：远端递归 tree（blob sha） vs 本地 ls-tree -r -z HEAD（blob sha）。
    #    两边 sha 同为 git blob 哈希，可直接比对，精确且免上传未变文件。
    head_commit = _api("GET", f"repos/{REPO}/git/commits/{remote_sha}")
    remote_tree = _api("GET", f"repos/{REPO}/git/trees/{head_commit['tree']['sha']}?recursive=1")
    if remote_tree.get("truncated"):
        raise RuntimeError("远端 tree 被截断（文件过多），需分页处理")
    remote_blobs = {e["path"]: e["sha"] for e in remote_tree["tree"]
                    if e["type"] == "blob"}

    # 本地 HEAD 快照；-z + \0 分割防止中文路径被引号八进制转义
    ls = subprocess.run(["git", "-C", str(SITE), "ls-tree", "-r", "-z", "HEAD"],
                        capture_output=True)
    if ls.returncode != 0:
        raise RuntimeError(f"git ls-tree: {ls.stderr.decode(errors='replace')[:200]}")
    local_blobs = {}
    for rec in ls.stdout.split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        _mode, _typ, sha = meta.decode().split()
        local_blobs[path.decode("utf-8")] = sha

    changed = sorted(
        [p for p, s in local_blobs.items() if remote_blobs.get(p) != s]      # 新增+修改
        + [p for p in remote_blobs if p not in local_blobs]                   # 删除
    )

    changed = [c for c in changed if c.strip()]
    if not changed:
        print("✅ 无变更需要推送（本地与远端内容一致）")
        return 0

    print(f"📦 待推送 {len(changed)} 个文件：")
    for c in changed[:20]:
        print(f"   {c}")
    if len(changed) > 20:
        print(f"   ...等共 {len(changed)} 个")
    if opts.dry_run:
        print("[dry-run] 未推送")
        return 0

    # 3) 逐文件建 blob（base64，二进制安全）
    #    关键：内容从 git 对象库取（git cat-file），不读工作区文件。
    #    本仓库 core.autocrlf=true，工作区是 CRLF 而对象库归一化为 LF；
    #    直接读工作区会导致上传 blob 的 sha 与本地 tree 永远不一致 → 每次重推死循环。
    tree_items = []
    for path in changed:
        fp = SITE / path
        if not fp.exists():
            tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": None})  # 删除
            continue
        cat = subprocess.run(["git", "-C", str(SITE), "cat-file", "blob", f"HEAD:{path}"],
                             capture_output=True)
        if cat.returncode != 0:
            raise RuntimeError(f"git cat-file HEAD:{path}: {cat.stderr.decode(errors='replace')[:200]}")
        raw = cat.stdout
        blob = _api("POST", f"repos/{REPO}/git/blobs",
                    {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        tree_items.append({"path": path.replace("\\", "/"), "mode": "100644",
                           "type": "blob", "sha": blob["sha"]})
        print(f"   blob ✅ {path}")

    # 4) 基于远端 head 的 tree 构造新 tree
    base_tree = head_commit["tree"]["sha"]
    new_tree = _api("POST", f"repos/{REPO}/git/trees",
                    {"base_tree": base_tree, "tree": tree_items})

    # 5) commit + 更新 ref
    short_log = _git(["log", "--oneline", "-1"]).split(" ", 1)[-1]
    msg = f"api-push（无代理通道）: 同步本地快照 [{short_log[:40]}]"
    new_commit = _api("POST", f"repos/{REPO}/git/commits",
                      {"message": msg, "tree": new_tree["sha"],
                       "parents": [remote_sha]})
    _api("PATCH", f"repos/{REPO}/git/refs/heads/main",
         {"sha": new_commit["sha"], "force": False})

    API_HEAD_FILE.write_text(new_commit["sha"], encoding="utf-8")
    print(f"\n✅ API 推送成功：远端 main = {new_commit['sha'][:7]}（{msg}）")
    print("   已记录到 .api_push_head，代理恢复后 git push 会自动合并该提交")
    return 0


if __name__ == "__main__":
    sys.exit(main())
