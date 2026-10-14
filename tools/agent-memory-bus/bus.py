#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享记忆总线 (Cross-Agent Memory Bus)

多个 AI agent（Codex / DeepSeek Harness / Hermes / Claude）共用一个记忆文件，
谁干了什么都写进来，开工前先读别人写了什么，这样换一个 agent 也能接上上下文。

只用标准库。所有子命令：

  log      --agent codex --topic 项目 --text "干了什么" [--pin] [--stdin] [--file path]
  read     --for hermes [-n 30] [--all] [--peek]      # 只显示别人在我上次读取之后写的新条目
  recent   [-n 20] [--agent codex] [--topic 项目]
  search   关键词
  digest   [--for hermes] [--advance] [--max-chars 1500]   # 紧凑摘要，给 prompt / hook 用
  status
  install  [--dry-run]        # 把「读/写总线」的规矩写进各 agent 的指令文件（幂等）
  hook                        # 给 Hermes pre_llm_call 用的入口，stdin 收 JSON、stdout 吐 {"context": ...}
"""
from __future__ import annotations

import argparse
import os
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get('AGENT_MEMORY_HOME', Path(__file__).resolve().parent))
LOG = ROOT / "总线.jsonl"
MD = ROOT / "总线.md"
CURSORS = ROOT / ".cursors"
MANIFEST = ROOT / ".wired.json"

MAX_TEXT = 2000          # 单条正文上限
MD_RECENT = 60           # 总线.md 最多渲染多少条
MD_PINNED = 20           # 常青要点最多渲染多少条
DIGEST_ITEM = 220        # digest 每条截断长度

BEGIN = "<!-- >>> 共享记忆总线 BEGIN"
END = "<!-- <<< 共享记忆总线 END <<< -->"

BLOCK = f"""{BEGIN} >>> -->
## Shared agent memory
Read new entries before work: `python {Path(__file__).resolve().as_posix()} read --for <agent>`
Record useful progress: `python {Path(__file__).resolve().as_posix()} log --agent <agent> --topic project --text \"facts, conclusion, next step\"`
Only record context that other agents need.
{END}
"""

TITLE = "项目指令"


# --------------------------------------------------------------------------- io
def _utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load() -> list[dict]:
    if not LOG.exists():
        return []
    out: list[dict] = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _append(e: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _cursor_path(agent: str) -> Path:
    return CURSORS / f"{agent}.json"


def _cursor(agent: str) -> int:
    p = _cursor_path(agent)
    if p.exists():
        try:
            return int(json.loads(p.read_text(encoding="utf-8")).get("last_id", 0))
        except Exception:
            return 0
    return 0


def _set_cursor(agent: str, value: int) -> None:
    CURSORS.mkdir(parents=True, exist_ok=True)
    _cursor_path(agent).write_text(
        json.dumps({"last_id": int(value), "updated": _now()}, ensure_ascii=False),
        encoding="utf-8",
    )


def _one_line(text: str, limit: int) -> str:
    s = " ".join(text.split())
    return s if len(s) <= limit else s[:limit] + "…"


def _head(e: dict) -> str:
    return f"[{e.get('ts','')}] {e.get('agent','?')} / {e.get('topic','')}"


# ------------------------------------------------------------------------ render
def _render() -> None:
    entries = _load()
    pinned = [e for e in entries if e.get("pin")][-MD_PINNED:]
    recent = sorted(entries, key=lambda e: e.get("id", 0), reverse=True)[:MD_RECENT]
    recent.reverse()

    counts: dict[str, int] = {}
    for e in entries:
        counts[str(e.get("agent", "?"))] = counts.get(str(e.get("agent", "?")), 0) + 1
    stat = " / ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))

    L: list[str] = []
    L.append("# 跨 Agent 共享记忆总线")
    L.append("")
    L.append("> 自动生成，勿手改。写入：`python bus.py log --agent <名> --topic <主题> --text \"...\"`")
    L.append(f"> 更新时间 {_now()}｜共 {len(entries)} 条｜{stat or '还没有记录'}")
    L.append("")
    if pinned:
        L.append("## 常青要点（pin）")
        L.append("")
        for e in pinned:
            L.append(f"- **[{e.get('topic','')}]** {_one_line(str(e.get('text','')), 300)}  ⟨{e.get('agent','')} · {e.get('ts','')}⟩")
        L.append("")
    L.append(f"## 最近 {len(recent)} 条")
    L.append("")
    day = ""
    for e in recent:
        d = str(e.get("ts", ""))[:10]
        if d != day:
            day = d
            L.append(f"### {d}")
            L.append("")
        pin = " 📌" if e.get("pin") else ""
        L.append(f"**{str(e.get('ts',''))[11:]} · {e.get('agent','')} · {e.get('topic','')}**{pin}")
        L.append("")
        L.append(str(e.get("text", "")).strip())
        L.append("")
    MD.write_text("\n".join(L).rstrip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------- commands
def cmd_log(a: argparse.Namespace) -> int:
    text = a.text or ""
    if a.file:
        text = Path(a.file).read_text(encoding="utf-8")
    if a.stdin:
        text = sys.stdin.read()
    text = text.strip()
    if not text:
        print("！没有正文：用 --text \"...\" 或 --stdin / --file")
        return 2
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + " …(已截断)"
    entries = _load()
    e: dict = {
        "id": len(entries) + 1,
        "ts": _now(),
        "agent": a.agent,
        "topic": a.topic or "动态",
        "text": text,
    }
    if a.pin:
        e["pin"] = True
    if a.tag:
        e["tags"] = list(a.tag)
    _append(e)
    _render()
    print(f"✔ #{e['id']} [{e['agent']}/{e['topic']}] {_one_line(text, 60)}")
    return 0


def _print_entries(entries: list[dict]) -> None:
    for e in entries:
        print(f"{_head(e)}  #{e.get('id')}")
        print(f"  {str(e.get('text','')).strip()}")
        if e.get("tags"):
            print(f"  tags: {', '.join(map(str, e['tags']))}")
        print()


def cmd_read(a: argparse.Namespace) -> int:
    entries = _load()
    cur = 0 if a.all else _cursor(a.for_agent)
    new = [e for e in entries if int(e.get("id", 0)) > cur]
    others = [e for e in new if str(e.get("agent")) != str(a.for_agent)] if a.for_agent else new
    if not others:
        who = f"{a.for_agent} " if a.for_agent else ""
        print(f"（共享记忆总线：{who}没有新消息，上次游标 #{cur}，共 {len(entries)} 条）")
        return 0
    print(f"【共享记忆总线 · 给 {a.for_agent or '你'} 的新消息】{len(others)} 条（游标 #{cur} → #{new[-1]['id']}）")
    print("=" * 56)
    print()
    _print_entries(others)
    print(f"完整记录：python {ROOT / 'bus.py'} recent -n 40")
    if a.for_agent and not a.peek:
        _set_cursor(a.for_agent, int(new[-1]["id"]))
    return 0


def cmd_recent(a: argparse.Namespace) -> int:
    entries = _load()
    if a.agent:
        entries = [e for e in entries if str(e.get("agent")) == a.agent]
    if a.topic:
        entries = [e for e in entries if a.topic in str(e.get("topic", ""))]
    tail = entries[-a.n:]
    if not tail:
        print("（还没有记录）")
        return 0
    print(f"【共享记忆总线 · 最近 {len(tail)} 条】共 {len(entries)} 条")
    print("=" * 56)
    print()
    _print_entries(tail)
    return 0


def cmd_search(a: argparse.Namespace) -> int:
    kw = a.keyword.lower()
    hits = [
        e for e in _load()
        if kw in str(e.get("text", "")).lower()
        or kw in str(e.get("topic", "")).lower()
        or kw in str(e.get("agent", "")).lower()
        or kw in " ".join(map(str, e.get("tags", []))).lower()
    ]
    if not hits:
        print(f"（没有匹配 “{a.keyword}” 的记录）")
        return 0
    print(f"【共享记忆总线 · 命中 {len(hits)} 条】关键词：{a.keyword}")
    print("=" * 56)
    print()
    _print_entries(hits[-a.n:])
    return 0


def cmd_digest(a: argparse.Namespace) -> int:
    entries = _load()
    if a.for_agent:
        cur = _cursor(a.for_agent)
        new = [e for e in entries if int(e.get("id", 0)) > cur]
        new = [e for e in new if str(e.get("agent")) != str(a.for_agent)]
    else:
        new = entries[-a.n:]
    if not new:
        if a.advance and a.for_agent and entries:
            _set_cursor(a.for_agent, int(entries[-1]["id"]))
        return 0
    lines = [
        f"- [{e.get('topic','')}] {e.get('agent','')} @{str(e.get('ts',''))[5:]}: {_one_line(str(e.get('text','')), DIGEST_ITEM)}"
        for e in new
    ]
    body = "\n".join(lines)
    header = "【跨 Agent 共享记忆总线 · 别的 agent 的新消息】\n"
    footer = f"\n（完整记录 / 要回复：python {ROOT / 'bus.py'} recent -n 40）"
    room = max(200, a.max_chars - len(header) - len(footer))
    if len(body) > room:
        body = body[:room] + "\n…（更多见总线）"
    print(header + body + footer)
    if a.advance and a.for_agent:
        _set_cursor(a.for_agent, int(new[-1]["id"]))
    return 0


def _wired(paths: list[dict]) -> None:
    MANIFEST.write_text(json.dumps(paths, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_status(a: argparse.Namespace) -> int:
    entries = _load()
    print(f"共享记忆总线：{ROOT}")
    print(f"记录 {len(entries)} 条" + (f"，最新 {entries[-1].get('ts')}（{entries[-1].get('agent')}）" if entries else "（空）"))
    print(f"总线.md 更新于 {datetime.fromtimestamp(MD.stat().st_mtime).strftime('%Y-%m-%d %H:%M') if MD.exists() else '—'}")
    print()
    print("游标（每个 agent 读到第几条）：")
    if CURSORS.exists():
        for p in sorted(CURSORS.glob("*.json")):
            print(f"  {p.stem:<10} #{_cursor(p.stem)}")
    else:
        print("  （还没有 agent 读过）")
    print()
    print("接线状态（谁的指令文件里刻了这条规矩）：")
    if MANIFEST.exists():
        for item in json.loads(MANIFEST.read_text(encoding="utf-8")):
            p = Path(item["path"])
            ok = p.exists() and BEGIN in p.read_text(encoding="utf-8", errors="replace")
            print(f"  {'✔' if ok else '✘'} {item['who']:<26} {item['path']}")
    else:
        print("  （还没跑过 install）")
    return 0


def _targets() -> list[dict]:
    values = os.environ.get("AGENT_MEMORY_TARGETS", "")
    return [{"path": str(Path(p).expanduser()), "who": "configured agent", "title": "Agent instructions"} for p in values.split(os.pathsep) if p.strip()]


def _upsert(path: Path, block: str, title: str) -> str:
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if BEGIN in old and END in old:
        i = old.index(BEGIN)
        j = old.index(END) + len(END)
        new = old[:i] + block.strip() + old[j:]
        action = "更新"
    else:
        if old:
            sep = "" if old.endswith("\n\n") else ("\n" if old.endswith("\n") else "\n\n")
            new = old + sep + block.strip() + "\n"
            action = "追加"
        else:
            new = f"# {title}\n\n" + block.strip() + "\n"
            action = "新建"
    if new != old:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
    return action


def cmd_install(a: argparse.Namespace) -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    LOG.touch(exist_ok=True)
    written: list[dict] = []
    for t in _targets():
        p = Path(t["path"])
        if a.dry_run:
            action = "更新" if (p.exists() and BEGIN in p.read_text(encoding="utf-8", errors="replace")) else ("追加" if p.exists() else "新建")
        else:
            action = _upsert(p, BLOCK, t["title"])
        written.append({"who": t["who"], "path": t["path"], "action": action})
        print(f"  {action}  {t['who']:<26} {t['path']}")
    if not a.dry_run:
        _wired(written)
        _render()
        _write_readme()
    print(f"\n总线就绪：{ROOT}")
    return 0


def _write_readme() -> None:
    """Keep the distribution README unchanged."""
    return None


def cmd_hook(a: argparse.Namespace) -> int:
    """Hermes pre_llm_call 入口：stdin 收 hook payload，stdout 吐 {"context": ...}"""
    try:
        sys.stdin.read()
    except Exception:
        pass
    ctx = ""
    try:
        import subprocess

        out = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "digest", "--for", "hermes", "--advance", "--max-chars", "1600"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
        )
        ctx = (out.stdout or "").strip()
    except Exception:
        ctx = ""
    sys.stdout.buffer.write(json.dumps({"context": ctx} if ctx else {}, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


# -------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    _utf8()
    p = argparse.ArgumentParser(prog="bus.py", description="跨 agent 共享记忆总线")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("log", help="写一条记忆")
    s.add_argument("--agent", required=True)
    s.add_argument("--topic", default="动态")
    s.add_argument("--text", default="")
    s.add_argument("--stdin", action="store_true")
    s.add_argument("--file")
    s.add_argument("--pin", action="store_true")
    s.add_argument("--tag", action="append")
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("read", help="读别人的新消息（增量）")
    s.add_argument("--for", dest="for_agent", required=True)
    s.add_argument("-n", type=int, default=30)
    s.add_argument("--all", action="store_true")
    s.add_argument("--peek", action="store_true", help="不动游标")
    s.set_defaults(func=cmd_read)

    s = sub.add_parser("recent", help="最近若干条")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--agent")
    s.add_argument("--topic")
    s.set_defaults(func=cmd_recent)

    s = sub.add_parser("search", help="搜关键词")
    s.add_argument("keyword")
    s.add_argument("-n", type=int, default=20)
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("digest", help="紧凑摘要（给 prompt / hook）")
    s.add_argument("--for", dest="for_agent")
    s.add_argument("--advance", action="store_true")
    s.add_argument("--max-chars", type=int, default=1500)
    s.add_argument("-n", type=int, default=8)
    s.set_defaults(func=cmd_digest)

    s = sub.add_parser("status", help="状态与接线检查")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("install", help="把规矩写进各 agent 指令文件")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("hook", help="Hermes pre_llm_call hook 入口")
    s.set_defaults(func=cmd_hook)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
