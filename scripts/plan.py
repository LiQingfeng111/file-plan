#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""File Plan - hash-bound, reviewable file operations for AI agents.

Standard library only. Nothing outside the workspace is touched. Deletions are
moves into a quarantine folder, never real deletes. Every apply re-computes the
plan hash so the approved plan and the executed plan must be identical.

    python -X utf8 plan.py <command> --root <workspace> [options]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

FORMAT_VERSION = 1
OPS = ("copy", "move", "rename", "mkdir", "delete")
RISK = {"copy": "低", "move": "中", "rename": "中", "mkdir": "低", "delete": "高"}
DEFAULT_LIMIT = 50


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig")


def write_text(p: Path, text: str, atomic: bool = False) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if atomic:
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, p)
    else:
        p.write_text(text, encoding="utf-8", newline="\n")


def json_dump(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def plan_path(root: Path) -> Path:
    return root / ".fileplan" / "plan.json"


def require_plan(root: Path) -> None:
    if not plan_path(root).is_file():
        raise SystemExit(f"错误：{root} 尚无计划书，请先运行 init。")


def load_plan(root: Path) -> dict:
    return json.loads(read_text(plan_path(root)))


def save_plan(root: Path, data: dict) -> None:
    write_text(plan_path(root), json_dump(data), atomic=True)


def log_event(root: Path, event: str, **fields) -> None:
    p = root / ".fileplan" / "events.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"at": now(), "event": event, **fields}, ensure_ascii=False) + "\n")


def safe_rel(root: Path, rel: str | None, allow_empty: bool = False) -> Path:
    if not rel:
        if allow_empty:
            return root
        raise ValueError("缺少路径")
    p = Path(rel)
    if p.is_absolute() or p.drive or ".." in p.parts:
        raise ValueError(f"不安全的路径（需为工作区内相对路径）: {rel}")
    if p.parts and p.parts[0] == ".fileplan":
        raise ValueError(f"不允许操作计划目录内部: {rel}")
    resolved = (root / p).resolve()
    base = root.resolve()
    if base != resolved and base not in resolved.parents:
        raise ValueError(f"路径越出工作区: {rel}")
    return root / p


def canonical_ops(plan: dict) -> list[dict]:
    """Only the user-approved operation fields participate in the hash."""
    keys = ("op", "src", "dst", "note", "overwrite")
    return [{k: item.get(k) for k in keys} for item in plan.get("items", [])]


def compute_token(plan: dict) -> str:
    payload = json.dumps(canonical_ops(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def human_size(p: Path) -> str:
    if not p.exists():
        return "—"
    if p.is_dir():
        return "目录"
    n = p.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


# --------------------------------------------------------------------------
# preflight
# --------------------------------------------------------------------------

def precheck(root: Path, item: dict) -> tuple[str, str]:
    """Return (status, detail). status in OK / CONFLICT / MISSING / UNSAFE."""
    op = item.get("op")
    try:
        src = safe_rel(root, item.get("src"), allow_empty=(op == "mkdir"))
        dst = safe_rel(root, item.get("dst"), allow_empty=False) if item.get("dst") else None
    except ValueError as exc:
        return "UNSAFE", str(exc)

    if op not in OPS:
        return "UNSAFE", f"未知操作 {op}"
    if op in ("copy", "move", "rename", "delete") and not src.exists():
        return "MISSING", f"源不存在：{item.get('src')}"
    if op == "delete" and src.is_dir():
        return "UNSAFE", "delete 不接受目录，避免递归删除"
    if op in ("copy", "move", "rename") and src.is_dir():
        return "UNSAFE", "本工具只处理文件级移动/复制，目录请逐项登记"
    if op == "rename" and src.parent != (dst.parent if dst else None):
        return "UNSAFE", "rename 要求源与目标在同一目录"
    if op == "mkdir" and dst is None:
        return "UNSAFE", "mkdir 需要 --dst"
    if op in ("copy", "move", "rename", "mkdir") and dst is None:
        return "UNSAFE", f"{op} 需要 --dst"
    if dst is not None and dst.exists() and not item.get("overwrite"):
        return "CONFLICT", f"目标已存在：{item.get('dst')}（需 add --overwrite）"
    if dst is not None and dst.exists() and dst.is_dir() and op != "mkdir":
        return "CONFLICT", f"目标是目录：{item.get('dst')}"
    if op == "delete":
        return "OK", "将移入隔离区，不真删"
    return "OK", "可执行"


def render_plan_md(root: Path, plan: dict, token: str) -> str:
    lines = [
        f"# 文件操作计划书 · {plan.get('name', '未命名')}",
        "",
        f"- 生成时间：{now()}",
        f"- **计划哈希（token）：`{token}`**",
        f"- 操作项：{len(plan.get('items', []))} 项｜工作区：`{root}`",
        "- 本计划书可归档、可复核。任何一项内容变动都会使该 token 作废。",
        "",
        "| # | 操作 | 源 | 目标 / 去向 | 大小 | 危险 | 预检 | 理由 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, item in enumerate(plan.get("items", []), 1):
        src = item.get("src") or "—"
        try:
            size = human_size(safe_rel(root, item.get("src"))) if item.get("src") else "—"
        except ValueError:
            size = "—"
        dst = item.get("dst") or ("→ 隔离区" if item.get("op") == "delete" else "—")
        pre = item.get("precheck", {})
        lines.append(
            f"| {i} | {item.get('op')} | `{src}` | `{dst}` | {size} | {RISK.get(item.get('op'), '—')} | "
            f"{pre.get('status', '未检查')} | {str(item.get('note') or '—').replace('|', '/')} |"
        )
    risks = {r: sum(1 for x in plan.get("items", []) if RISK.get(x.get("op")) == r) for r in ("高", "中", "低")}
    problems = [(i, x) for i, x in enumerate(plan.get("items", []), 1)
                if x.get("precheck", {}).get("status") != "OK"]
    lines += [
        "",
        "## 汇总",
        "",
        f"- 危险等级：高 {risks['高']} 项｜中 {risks['中']} 项｜低 {risks['低']} 项",
        f"- 预检异常：{len(problems)} 项" + ("（存在异常时 apply 会整体拒绝）" if problems else ""),
    ]
    for i, x in problems:
        lines.append(f"  - #{i} {x.get('op')} `{x.get('src') or x.get('dst')}`："
                     f"{x.get('precheck', {}).get('status')} — {x.get('precheck', {}).get('detail')}")
    lines += [
        "",
        "## 安全措施",
        "",
        "- 执行前把受影响文件备份到 `.fileplan/backup/<token8>/`，保持原相对路径。",
        "- `delete` 只接受文件，且只移入 `.fileplan/trash/<token8>/`，**永不真删**。",
        "- 目标已存在默认判冲突；仅 `add --overwrite` 显式允许覆盖。",
        "- 执行中任一项失败即停止，后续项标记 SKIPPED，不重试、不跳过。",
        "",
        "## 确认与执行",
        "",
        "审阅本清单后，由**用户本人**给出确认，再执行：",
        "",
        "```bash",
        f'python -X utf8 scripts/plan.py apply --root "{root}" --token {token} --confirm "<用户确认的原话>"',
        "```",
        "",
        "> 修改计划（增删项、改路径、调顺序）会使 token 变化，必须重新 build 并重新取得确认。",
        "> 工具只保证按计划执行，不判断整理逻辑是否符合真实意图；语义判断由人完成。",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_init(args) -> int:
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if plan_path(root).exists() and not args.force:
        raise SystemExit(f"错误：{root} 已有计划书。确需重置请加 --force（不删除备份与隔离区）。")
    plan = {"format_version": FORMAT_VERSION, "name": args.name, "created": now(),
            "items": [], "token": None, "built_at": None, "applied": False}
    save_plan(root, plan)
    p = root / ".fileplan" / "events.jsonl"
    if not p.exists():
        write_text(p, "")
    log_event(root, "init", name=args.name)
    print(f"已创建计划书：{root}")
    print(f"任务：{args.name}（尚未登记任何操作，未改动任何文件）")
    print("下一步：add 逐项登记，再 build 生成 PLAN.md 交用户审阅。")
    return 0


def cmd_add(args) -> int:
    root = Path(args.root).resolve()
    require_plan(root)
    plan = load_plan(root)
    if plan.get("applied"):
        raise SystemExit("错误：该计划已执行。请重新 init 一份新计划，不要复用已执行的计划。")
    if args.op == "mkdir" and not args.dst:
        raise SystemExit("错误：mkdir 需要 --dst。")
    if args.op in ("copy", "move", "rename") and not (args.src and args.dst):
        raise SystemExit(f"错误：{args.op} 需要 --src 与 --dst。")
    if args.op == "delete" and not args.src:
        raise SystemExit("错误：delete 需要 --src。")
    try:
        if args.src:
            safe_rel(root, args.src)
        if args.dst:
            safe_rel(root, args.dst)
    except ValueError as exc:
        raise SystemExit(f"错误：{exc}")
    plan["items"].append({
        "op": args.op, "src": args.src, "dst": args.dst,
        "note": args.note, "overwrite": bool(args.overwrite),
    })
    plan["token"] = None
    plan["built_at"] = None
    save_plan(root, plan)
    log_event(root, "add", op=args.op, src=args.src, dst=args.dst)
    print(f"已登记 #{len(plan['items'])} {args.op}：{args.src or '—'} → {args.dst or '隔离区'}")
    print("提醒：计划内容变动后旧 token 作废，需重新 build 并重新取得确认。")
    return 0


def cmd_build(args) -> int:
    root = Path(args.root).resolve()
    require_plan(root)
    plan = load_plan(root)
    if plan.get("applied"):
        raise SystemExit("错误：该计划已执行，不能重复 build。请重新 init。")
    if not plan["items"]:
        raise SystemExit("错误：计划书为空，先 add 登记操作。")
    if len(plan["items"]) > args.limit:
        raise SystemExit(f"错误：{len(plan['items'])} 项超过单计划上限 {args.limit}。确需继续请显式提高 --limit。")
    for item in plan["items"]:
        status, detail = precheck(root, item)
        item["precheck"] = {"status": status, "detail": detail}
    token = compute_token(plan)
    plan["token"] = token
    plan["built_at"] = now()
    save_plan(root, plan)
    write_text(root / "PLAN.md", render_plan_md(root, plan, token), atomic=True)
    log_event(root, "build", token=token, items=len(plan["items"]))
    bad = [(i, x) for i, x in enumerate(plan["items"], 1) if x["precheck"]["status"] != "OK"]
    risks = {r: sum(1 for x in plan["items"] if RISK.get(x["op"]) == r) for r in ("高", "中", "低")}
    print(f"已生成计划书：{root / 'PLAN.md'}")
    print(f"token：{token}｜{len(plan['items'])} 项｜高 {risks['高']} / 中 {risks['中']} / 低 {risks['低']}")
    if bad:
        print(f"预检异常 {len(bad)} 项（apply 会整体拒绝，需先修正计划）：")
        for i, x in bad:
            print(f"  #{i} {x['precheck']['status']}：{x['precheck']['detail']}")
    else:
        print("预检全部通过。请把 PLAN.md 交给用户审阅，等待明确确认后再 apply。")
    return 0


def cmd_apply(args) -> int:
    root = Path(args.root).resolve()
    require_plan(root)
    plan = load_plan(root)
    if plan.get("applied"):
        raise SystemExit("错误：该计划已执行过，拒绝重复执行。")
    if not plan.get("token") or not plan.get("built_at"):
        raise SystemExit("错误：尚未 build，或计划内容已变动。请重新 build 并取得确认。")
    expected = compute_token(plan)
    if expected != plan["token"] or args.token != expected:
        raise SystemExit(f"错误：token 不匹配（当前计划应为 {expected}）。计划可能被改动，请重新 build 并取得确认。")
    if not args.confirm.strip():
        raise SystemExit("错误：缺少用户确认原话，拒绝执行。")
    if len(plan["items"]) > args.limit:
        raise SystemExit(f"错误：{len(plan['items'])} 项超过执行上限 {args.limit}。")

    # Re-run preflight immediately before touching anything.
    problems = []
    for i, item in enumerate(plan["items"], 1):
        status, detail = precheck(root, item)
        if status != "OK":
            problems.append((i, status, detail))
    if problems:
        for i, status, detail in problems:
            print(f"  #{i} {status}：{detail}")
        raise SystemExit("错误：执行前预检未通过，未改动任何文件。请修正后重新 build 并重新取得确认。")

    token8 = expected[:8]
    backup_dir = root / ".fileplan" / "backup" / token8
    trash_dir = root / ".fileplan" / "trash" / token8
    results = []
    stopped = False
    for i, item in enumerate(plan["items"], 1):
        if stopped:
            results.append({"idx": i, "op": item["op"], "status": "SKIPPED", "detail": "前序项失败，未执行"})
            continue
        op, src_s, dst_s = item["op"], item.get("src"), item.get("dst")
        try:
            src = safe_rel(root, src_s, allow_empty=(op == "mkdir"))
            dst = safe_rel(root, dst_s) if dst_s else None
            # Backup everything that will be modified or removed.
            for candidate in (src, dst):
                if candidate is not None and candidate.is_file() and (candidate == src or item.get("overwrite")):
                    rel = candidate.relative_to(root)
                    target = backup_dir / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(candidate, target)
            if op == "copy":
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                detail = f"已复制 → {dst_s}"
            elif op in ("move", "rename"):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                detail = f"已移动 → {dst_s}"
            elif op == "mkdir":
                dst.mkdir(parents=True, exist_ok=True)
                detail = f"已创建目录 {dst_s}"
            elif op == "delete":
                target = trash_dir / src.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(target))
                detail = f"已移入隔离区 → .fileplan/trash/{token8}/{src_s}（未真删）"
            else:
                raise ValueError(f"未知操作 {op}")
            results.append({"idx": i, "op": op, "src": src_s, "dst": dst_s,
                            "status": "OK", "detail": detail})
        except (OSError, ValueError, shutil.Error) as exc:
            results.append({"idx": i, "op": op, "src": src_s, "dst": dst_s,
                            "status": "FAILED", "detail": str(exc)})
            stopped = True

    applied = {
        "token": expected, "applied_at": now(), "confirm": args.confirm,
        "backup_dir": str(backup_dir.relative_to(root)),
        "trash_dir": str(trash_dir.relative_to(root)),
        "results": results,
        "summary": {s: sum(1 for r in results if r["status"] == s) for s in ("OK", "FAILED", "SKIPPED")},
    }
    write_text(root / ".fileplan" / "apply.json", json_dump(applied), atomic=True)
    plan["applied"] = True
    save_plan(root, plan)
    log_event(root, "apply", token=expected, confirm=args.confirm, summary=applied["summary"])
    print(f"执行完成：OK {applied['summary']['OK']}｜FAILED {applied['summary']['FAILED']}｜"
          f"SKIPPED {applied['summary']['SKIPPED']}")
    print(f"备份：{backup_dir}")
    print(f"隔离区：{trash_dir}（delete 只移动到这里，未真删）")
    print(f"结果：{root / '.fileplan' / 'apply.json'}")
    if stopped:
        print("注意：中途失败已停止，失败与跳过项见上；不要声称全部完成。")
    print("下一步：运行 verify 复核；隔离区由用户自行检查后决定是否永久清理。")
    return 1 if applied["summary"]["FAILED"] else 0


def cmd_verify(args) -> int:
    root = Path(args.root).resolve()
    require_plan(root)
    p = root / ".fileplan" / "apply.json"
    if not p.is_file():
        raise SystemExit("错误：尚未执行，没有可复核的结果。")
    applied = json.loads(read_text(p))
    plan = load_plan(root)
    if applied.get("token") != compute_token(plan):
        print("警告：计划内容在执行后被改动，复核结果可能失真。")
    ok = bad = skipped = 0
    for r in applied["results"]:
        if r["status"] != "OK":
            skipped += 1
            print(f"  [{r['status']}] #{r['idx']} {r['op']}：{r['detail']}")
            continue
        try:
            src = safe_rel(root, r.get("src"), allow_empty=(r["op"] == "mkdir")) if r.get("src") else None
            dst = safe_rel(root, r.get("dst")) if r.get("dst") else None
        except ValueError:
            src = dst = None
        good = False
        if r["op"] == "copy":
            good = bool(dst and dst.is_file() and src and src.is_file())
        elif r["op"] in ("move", "rename"):
            good = bool(dst and dst.is_file() and src and not src.exists())
        elif r["op"] == "mkdir":
            good = bool(dst and dst.is_dir())
        elif r["op"] == "delete":
            quarantined = root / applied["trash_dir"] / (r.get("src") or "")
            good = bool(src and not src.exists() and quarantined.is_file())
        if good:
            ok += 1
        else:
            bad += 1
            print(f"  [MISMATCH] #{r['idx']} {r['op']}：实际状态与计划预期不符")
    print(f"复核：符合预期 {ok}｜不符 {bad}｜未执行/失败 {skipped}")
    print(f"备份目录：{root / applied['backup_dir']}｜隔离区：{root / applied['trash_dir']}")
    return 1 if bad else 0


def cmd_status(args) -> int:
    root = Path(args.root).resolve()
    require_plan(root)
    plan = load_plan(root)
    applied = root / ".fileplan" / "apply.json"
    print(f"计划：{plan.get('name')}｜{len(plan.get('items', []))} 项")
    print(f"生成：{plan.get('created')}｜最近 build：{plan.get('built_at') or '未 build'}")
    print(f"token：{plan.get('token') or '无（需 build）'}｜当前内容应为：{compute_token(plan)}")
    print(f"状态：{'已执行' if plan.get('applied') else '未执行'}"
          + ("（apply.json 存在）" if applied.is_file() else ""))
    if plan.get("token") and compute_token(plan) != plan["token"]:
        print("警告：计划内容已变动，token 失效，必须重新 build 并重新取得确认。")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", required=True, help="工作区根目录")
    p = argparse.ArgumentParser(description="File Plan · 文件操作的可哈希预演与安全执行（仅标准库）")
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("init", parents=[common], help="创建空计划书")
    q.add_argument("--name", required=True)
    q.add_argument("--force", action="store_true")
    q.set_defaults(func=cmd_init)

    q = sub.add_parser("add", parents=[common], help="登记一项操作")
    q.add_argument("--op", required=True, choices=OPS)
    q.add_argument("--src", help="源（工作区内相对路径）")
    q.add_argument("--dst", help="目标（工作区内相对路径）")
    q.add_argument("--note", default="")
    q.add_argument("--overwrite", action="store_true", help="显式允许目标已存在/覆盖")
    q.set_defaults(func=cmd_add)

    q = sub.add_parser("build", parents=[common], help="生成 PLAN.md 与 token")
    q.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    q.set_defaults(func=cmd_build)

    q = sub.add_parser("apply", parents=[common], help="按已批准的计划执行")
    q.add_argument("--token", required=True)
    q.add_argument("--confirm", required=True, help="用户确认的原话，写入事件日志")
    q.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    q.set_defaults(func=cmd_apply)

    q = sub.add_parser("verify", parents=[common], help="复核执行结果")
    q.set_defaults(func=cmd_verify)

    q = sub.add_parser("status", parents=[common], help="查看计划状态")
    q.set_defaults(func=cmd_status)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 3
    except (OSError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
