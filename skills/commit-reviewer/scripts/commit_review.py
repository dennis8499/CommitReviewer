#!/usr/bin/env python3
"""Commit Reviewer CLI: inspect, verify, and render reports from fixed Git facts."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from string import Template

from git_evidence import GitError, TAIPEI, digest, inspect, issues, safe_error, verify


LABELS = {"pass": "符合", "fail": "不符合", "unknown": "資料不足", "pending": "待人工確認"}


def write_json(path, value):
    # ASCII escaping also preserves filenames that cannot be rendered as Unicode.
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.write("\n")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_snapshot(path):
    path = Path(path).resolve()
    facts = read_json(path)
    if facts.get("schema") != "commit-reviewer-facts/v1":
        raise ValueError("不支援的快照格式。")
    content = {k: v for k, v in facts.items() if k != "snapshot_id"}
    if facts.get("snapshot_id") != digest(content):
        raise ValueError("快照內容已變動；請重新 inspect。")
    patch = path.with_name("changes.patch")
    if hashlib.sha256(patch.read_bytes()).hexdigest() != facts["patch_sha256"]:
        raise ValueError("差異證據已變動或缺失；請重新 inspect。")
    return facts


def capture(args):
    for ref in args.issue:
        if issues(ref) != [ref]:
            raise ValueError("--issue 需使用 #123 或 group/project#123。")
    facts, patch = inspect(args.repo, args.mode, remote=args.remote, target=args.target,
                           self_emails=args.self_email, requested_issues=args.issue)
    if args.output_dir:
        directory = Path(args.output_dir).resolve()
        directory.mkdir(parents=True, exist_ok=False)
    else:
        # Python 3.13+ applies a special DACL for mkdir(0700) on Windows. Inherit
        # the user's temp-directory ACL so restricted Codex tokens retain access.
        directory = Path(tempfile.gettempdir()) / ("commit-reviewer-" + uuid.uuid4().hex)
        directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
    with (directory / "changes.patch").open("xb") as stream:
        stream.write(patch)
    write_json(directory / "snapshot.json", facts)
    analysis = dict(snapshot_id=facts["snapshot_id"], summary="", diff_reviewed=False,
                    commit_message="", confirmations=[], project_findings=[])
    write_json(directory / "analysis.json", analysis)
    return dict(schema="commit-reviewer-inspect/v1", snapshot_id=facts["snapshot_id"],
                snapshot=str(directory / "snapshot.json"), analysis=str(directory / "analysis.json"),
                patch=str(directory / "changes.patch"), mode=facts["mode"], repo=facts["repo"],
                checks=facts["checks"], gaps=facts["gaps"],
                manual_item_ids=[item["id"] for item in facts["manual_items"]],
                file_count=len(facts["files"]), commit_count=len(facts["commits"]))


def evaluate(facts, analysis, fresh):
    checks = [dict(item) for item in facts["checks"]]
    pending, confirmed = [], {}
    gaps = list(facts["gaps"]) + fresh["reasons"]
    if not isinstance(analysis, dict) or analysis.get("snapshot_id") != facts["snapshot_id"]:
        raise ValueError("分析與人工確認必須屬於同一 snapshot_id。")
    findings = analysis.get("project_findings", [])
    if not isinstance(findings, list):
        raise ValueError("project_findings 必須是陣列。")
    for index, finding in enumerate(findings, 1):
        if (not isinstance(finding, dict) or finding.get("status") not in ("fail", "unknown")
                or not all(isinstance(finding.get(k), str) and finding[k].strip()
                           for k in ("rule", "detail"))):
            raise ValueError("專案規範發現需有 rule、detail 及 fail／unknown 狀態。")
        detail = finding["rule"] + "：" + finding["detail"]
        checks.append(dict(id=f"project_rule_{index}", status=finding["status"], detail=detail))
        if finding["status"] == "unknown":
            gaps.append(detail)
    records = analysis.get("confirmations", [])
    if not isinstance(records, list):
        raise ValueError("confirmations 必須是陣列。")
    known = {item["id"] for item in facts["manual_items"]}
    for record in records:
        if not isinstance(record, dict) or record.get("item_id") not in known:
            raise ValueError("人工確認項目不存在於本次快照。")
        if record["item_id"] in confirmed:
            raise ValueError("人工確認項目重複。")
        if not all(isinstance(record.get(key), str) and record[key].strip()
                   for key in ("confirmed_by", "reason")):
            raise ValueError("人工確認需有 confirmed_by 與 reason。")
        confirmed[record["item_id"]] = record
    for item in facts["manual_items"]:
        if item["id"] not in confirmed:
            pending.append(item["id"])
    summary = analysis.get("summary", "")
    if not isinstance(summary, str):
        raise ValueError("summary 必須是文字。")
    reviewed = analysis.get("diff_reviewed") is True and bool(summary.strip())
    checks.append(dict(id="diff_summary_review", status="pass" if reviewed else "pending",
                       detail="已依差異核對變更摘要。" if reviewed else "尚待閱讀差異並完成變更摘要。"))
    if facts["mode"] == "commit":
        message = analysis.get("commit_message", "")
        if not isinstance(message, str):
            raise ValueError("commit_message 必須是文字。")
        refs = issues(message)
        valid = bool(refs) and set(facts["requested_issues"]).issubset(refs)
        checks.append(dict(id="draft_issue_references", status="pass" if valid else "fail",
                           detail="草稿包含指定／有效的 Issue 編號。" if valid else
                           "草稿缺少 Issue 編號，或未包含本次指定的 Issue。"))
        checks.append(dict(id="draft_present", status="pass" if message.strip() else "pending",
                           detail="已提供 Commit 草稿；尚未提交。" if message.strip() else "尚待撰寫 Commit 草稿。"))
    checks.append(dict(id="manual_confirmation", status="pending" if pending else "pass",
                       detail=f"尚有 {len(pending)} 項他人異動待人工核對。" if pending else
                       "他人異動已確認，或本次沒有需確認項目。"))
    checks.append(dict(id="evidence_complete", status="unknown" if gaps else "pass",
                       detail="存在資料／版本缺口。" if gaps else "必要 Git 資料完整，且發布前版本一致。"))
    failures = [item["id"] for item in checks if item["status"] == "fail"]
    waiting = [item["id"] for item in checks if item["status"] == "pending"]
    status = "fail" if failures else "unknown" if gaps else "pending" if waiting else "pass"
    return dict(status=status, ready=status == "pass", checks=checks, gaps=gaps,
                pending_items=pending, confirmations=confirmed, verification=fresh)


def md(value):
    text = html.escape(str(value) if value is not None else "未取得", quote=False)
    text = re.sub(r"([\\`|\[\]*_])", r"\\\1", text)
    return text.replace("\r", "").replace("\n", "<br>")


def table(headers, rows):
    result = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    result.extend("| " + " | ".join(md(cell) for cell in row) + " |" for row in rows)
    return "\n".join(result)


def fence(text):
    marker = "`" * max(3, max((len(m[0]) + 1 for m in re.finditer(r"`+", text)), default=3))
    return f"{marker}text\n{text.rstrip()}\n{marker}"


def render(facts, analysis, result):
    metadata = [("Repo", facts["repo"]), ("來源分支", facts["branch"]),
                ("目標遠端／分支", f"{facts['remote']}/{facts['target_branch']}" if facts["remote"] else "不適用"),
                ("遠端預設主線", facts["default_branch"]), ("擷取時間（Asia/Taipei）", facts["captured_at"]),
                ("發布前重查時間", result["verification"]["checked_at"]),
                ("HEAD 完整 SHA", facts["head_sha"] or "尚無已提交 Commit"),
                ("目標完整 SHA", facts["target_sha"]), ("共同祖先完整 SHA", facts["merge_base_sha"]),
                ("Snapshot ID", facts["snapshot_id"]), ("Git 版本", facts["git_version"])]
    checks = table(["檢查", "結果", "說明"],
                   [(c["id"], LABELS[c["status"]], c["detail"]) for c in result["checks"]])
    commit_table = (table(["完整 Commit hash", "作者", "摘要", "Issue"],
                          [(c["sha"], f"{c['author']} <{c['email']}>", c["subject"],
                            ", ".join(c["issues"]) or "缺少") for c in facts["commits"]])
                    if facts["commits"] else "尚未提交；未產生本次新 Commit hash。" if facts["mode"] == "commit"
                    else "本次未取得可列出的新增 Commit；請查看檢查結果與缺口。")
    manual = []
    for item in facts["manual_items"]:
        record = result["confirmations"].get(item["id"])
        location = (f"{item['old_path']}:{item['old_start']}-{item['old_end']}（比較前版本 {item['left_sha']}）"
                    if item["kind"] == "lines" else "分支新增 Commit")
        sha = item.get("origin_sha", item.get("sha"))
        state = f"[x] {record['confirmed_by']}：{record['reason']}" if record else "[ ] 待人工確認"
        manual.append((item["id"], location, f"{item['author']} <{item['email']}>", sha, state))
    manual_table = table(["項目 ID", "位置", "歷史作者", "完整 hash", "人工核對"], manual) if manual else "本次沒有辨識出的他人異動項目。"
    gaps = "\n".join("- " + md(item) for item in result["gaps"]) or "無已知必要資料缺口。"
    notices = "\n".join("- " + md(item) for item in facts["notices"]) or "無。"
    files = table(["狀態", "比較前路徑", "變更後路徑"],
                  [(f["status"], f["old_path"], f["path"]) for f in facts["files"]]) if facts["files"] else "無可列出的檔案差異。"
    draft = ("## Commit 草稿\n\n" + fence(analysis.get("commit_message", "") or "尚待撰寫")
             + "\n\n此草稿尚未提交，沒有新的 Commit hash。" if facts["mode"] == "commit" else "")
    scope = ("已暫存差異；未暫存與未追蹤內容不在本次範圍。" if facts["mode"] == "commit" else
             "固定共同祖先至 HEAD 的差異，以及目標分支尚未包含的 Commit。")
    template = Path(__file__).resolve().parents[1] / "assets" / "mr-report.md"
    return Template(template.read_text(encoding="utf-8")).substitute(
        mode="Commit 準備" if facts["mode"] == "commit" else "MR 前檢查",
        conclusion=LABELS[result["status"]], scope=scope,
        summary=md(analysis.get("summary", "").strip()) or "尚待閱讀差異並填寫。",
        metadata=table(["欄位", "固定值"], metadata), checks=checks, files=files,
        commits=commit_table, manual=manual_table, gaps=gaps, notices=notices, draft=draft)


def publish(snapshot_path, analysis_path=None, output=None):
    facts = load_snapshot(snapshot_path)
    analysis_path = analysis_path or Path(snapshot_path).with_name("analysis.json")
    analysis = read_json(analysis_path)
    fresh = verify(facts)
    result = evaluate(facts, analysis, fresh)
    content = render(facts, analysis, result)
    root = Path(facts["repo"]).resolve() / "review-reports"
    if root.is_symlink() or root.resolve() != root:
        raise ValueError("review-reports 必須位於受檢 Repo，不能經符號連結改指其他位置。")
    root.mkdir(exist_ok=True)
    if output:
        report = Path(output).resolve()
        if report.parent != root or report.suffix.lower() != ".md":
            raise ValueError("--output 必須是此 Repo/review-reports/ 下的 Markdown 檔案。")
    else:
        stamp = datetime.now(TAIPEI).strftime("%Y%m%d-%H%M%S-%f")
        report = root / f"commit-review-{stamp}-{facts['snapshot_id'][:8]}.md"
    with report.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return dict(schema="commit-reviewer-report/v1", report=str(report),
                snapshot_id=facts["snapshot_id"], status=result["status"], ready=result["ready"],
                pending_items=result["pending_items"], gaps=result["gaps"], checks=result["checks"])


def parser():
    cli = argparse.ArgumentParser(description="Commit 與 MR 前檢查；只產生草稿與本機報告。")
    commands = cli.add_subparsers(dest="command", required=True)
    gather = commands.add_parser("inspect", help="固定 Git 證據並建立分析草稿")
    gather.add_argument("--repo", required=True)
    gather.add_argument("--mode", choices=("commit", "mr"), required=True)
    gather.add_argument("--remote")
    gather.add_argument("--target", help="遠端上的分支名稱，例如 main；不含 origin/ 前綴")
    gather.add_argument("--self-email", action="append", default=[])
    gather.add_argument("--issue", action="append", default=[])
    gather.add_argument("--output-dir", help="新建的證據目錄；預設使用系統暫存目錄")
    for name in ("verify", "report"):
        command = commands.add_parser(name, help="重查固定版本" if name == "verify" else "重查版本並產生報告")
        command.add_argument("--snapshot", required=True)
        if name == "report":
            command.add_argument("--analysis")
            command.add_argument("--output")
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "inspect":
            result, code = capture(args), 0
        elif args.command == "verify":
            result = verify(load_snapshot(args.snapshot))
            code = 0 if result["current"] else 3
        else:
            result = publish(args.snapshot, args.analysis, args.output)
            code = 0 if result["ready"] else 3
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return code
    except (GitError, OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(error=safe_error(str(exc))), ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
