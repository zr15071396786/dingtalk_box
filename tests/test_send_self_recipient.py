"""test_send_self_recipient.py — v0.3.11 send.py fallback 修复验证

v0.3.10 bug: send.py used singleChat=true conversation ID as --group target,
dws routed it to send_personal_message to the other party of that 1v1 chat
(not to ourselves). User reported: "sent to whoever is pinned at the top".

v0.3.11 fix: contact user me -> userId + name -> contact user search ->
match by userId strictly -> --open-dingtalk-id. No fallback to first result.

This test verifies:
  1. _resolve_self_open_dingtalk_id(user_id) returns our own openDingTalkId
  2. The returned oid's userId matches ours (no false positive on duplicate names)
  3. dws accepts the oid as --open-dingtalk-id for sending file (dry-run)
  4. The old buggy path (--group singleChat=true id) DOES route to other party
     (proves the bug existed and the fix is needed)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 把 sidecar 加到 path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sidecar.core import dws_runner, paths  # noqa: E402

OK = "[OK]"
BAD = "[BAD]"
WARN = "[WARN]"


def test_get_self_user_id() -> str:
    """contact user me -> our userId + name"""
    me = dws_runner.run(["contact", "user", "me"], timeout=30)
    items = me.get("result") or []
    assert items, "contact user me returned empty (not logged in?)"
    oem = items[0].get("orgEmployeeModel", {}) or {}
    user_id = str(oem.get("userId") or items[0].get("userId") or "")
    name = oem.get("orgUserName") or items[0].get("name") or ""
    assert user_id, "contact user me no userId"
    assert name, "contact user me no name"
    print(f"  {OK} me.userId = {user_id}")
    print(f"  {OK} me.name   = {name}")
    return user_id


def test_resolve_self_open_dingtalk_id(user_id: str) -> str:
    """Verify the patched send._resolve_self_open_dingtalk_id returns OUR oid"""
    from sidecar.core import send
    oid = send._resolve_self_open_dingtalk_id(user_id)
    assert oid, "_resolve_self_open_dingtalk_id returned empty"
    print(f"  {OK} self.openDingTalkId = {oid[:16]}...")

    # Reverse check: search by name, verify the userId-matched record has our oid
    me_name_resp = dws_runner.run(["contact", "user", "me"], timeout=30)
    me_name = (me_name_resp.get("result") or [{}])[0].get("orgEmployeeModel", {}).get("orgUserName", "")
    search_resp = dws_runner.run(["contact", "user", "search", "--keyword", me_name], timeout=30)
    items = search_resp.get("result") or []
    matched = [it for it in items if str(it.get("userId") or "") == user_id]
    assert matched, f"contact user search '{me_name}' has no record with userId={user_id}"
    assert matched[0].get("openDingTalkId") == oid, (
        f"search oid ({matched[0].get('openDingTalkId')}) != _resolve_self oid ({oid})"
    )
    print(f"  {OK} search reverse-check: userId matched, oid consistent")
    return oid


def test_dry_run_send_with_oid(oid: str) -> None:
    """dws --dry-run: oid as --open-dingtalk-id routes to send_personal_message"""
    dws = paths.dws_exe_path()
    cmd = [
        dws, "chat", "message", "send",
        "--open-dingtalk-id", oid,
        "--title", "v0.3.11 test",
        "--msg-type", "file",
        "--dentry-id", "1", "--space-id", "1",
        "--file-name", "test.txt", "--file-size", "1",
        "--dry-run", "--format", "json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert proc.returncode == 0, f"dws dry-run exit {proc.returncode}: {proc.stderr}"
    data = json.loads(proc.stdout)
    inv = data.get("invocation", {})
    assert inv.get("tool") == "send_personal_message", (
        f"expected send_personal_message, got {inv.get('tool')} -- "
        f"proves oid is a personal chat target, not a group"
    )
    assert inv.get("params", {}).get("receiverOpenDingTalkId") == oid
    print(f"  {OK} dws tool = send_personal_message (personal chat, file OK)")
    print(f"  {OK} receiverOpenDingTalkId = {oid[:16]}...")


def test_old_buggy_path_returns_other_user() -> None:
    """Reproduce v0.3.10 bug: singleChat=true id as --group -> send_personal_message to other"""
    res = dws_runner.run(["chat", "list-top-conversations", "--limit", "50"], timeout=30)
    convs = (res.get("result") or {}).get("conversations") or []
    single_chats = [c for c in convs if c.get("singleChat") is True and c.get("openConversationId")]
    if not single_chats:
        print(f"  {WARN} skipped: no singleChat=true conversations in this account")
        return
    target = single_chats[0]
    dws = paths.dws_exe_path()
    cmd = [
        dws, "chat", "message", "send",
        "--group", target["openConversationId"],
        "--title", "v0.3.10 buggy path repro",
        "--text", "test",
        "--dry-run", "--format", "json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert proc.returncode == 0, f"dws dry-run exit {proc.returncode}: {proc.stderr}"
    data = json.loads(proc.stdout)
    tool = data.get("invocation", {}).get("tool", "")
    title = target.get("title", "")
    print(f"  {WARN} v0.3.10 OLD path sends send_personal_message to singleChat=true chat")
    print(f"     this account first singleChat=true chat: title={title!r}")
    print(f"     dws tool: {tool}")
    assert tool == "send_personal_message", (
        f"expected dws to route singleChat=true id as send_personal_message "
        f"(proves it sends to that chat's other party), got {tool}"
    )


if __name__ == "__main__":
    print("=" * 60)
    print("v0.3.11 send.py fallback fix verification")
    print("=" * 60)
    print()
    print("[1/4] get our userId + name")
    user_id = test_get_self_user_id()
    print()
    print("[2/4] _resolve_self_open_dingtalk_id resolves our oid")
    oid = test_resolve_self_open_dingtalk_id(user_id)
    print()
    print("[3/4] dws --dry-run: oid as --open-dingtalk-id for file send")
    test_dry_run_send_with_oid(oid)
    print()
    print("[4/4] reproduce v0.3.10 old buggy path")
    test_old_buggy_path_returns_other_user()
    print()
    print("=" * 60)
    print("ALL CHECKS PASSED -- v0.3.11 fix verified")
    print("=" * 60)