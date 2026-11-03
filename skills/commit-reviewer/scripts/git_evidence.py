"""Collect immutable Git facts. Only fetch_target() changes repository metadata."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


TAIPEI = timezone(timedelta(hours=8), "Asia/Taipei")
ISSUE = re.compile(r"(?<![\w/#&!\\])(?:(?:[\w.-]+/)+[\w.-]+)?#[1-9][0-9]*(?![\w])")
OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def now():
    return datetime.now(TAIPEI).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode("ascii")).hexdigest()


def issues(message):
    return list(dict.fromkeys(m.group(0) for m in ISSUE.finditer(message)))


def safe_error(message):
    return re.sub(r"([a-z][a-z0-9+.-]*://)[^/\s@]+@", r"\1[redacted]@", message,
                  flags=re.I)[:2000]


class GitError(RuntimeError):
    pass


class Git:
    def __init__(self, path):
        self.root = Path(path).resolve()
        self.root = Path(self.text("rev-parse", "--show-toplevel").strip()).resolve()

    def run(self, *args, input=None, allowed=(0,), timeout=120):
        env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
                   GCM_INTERACTIVE="Never", GIT_NO_LAZY_FETCH="1", LC_ALL="C")
        # Ignore ambient repository/index/object-directory overrides, not identity.
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
            env.pop(key, None)
        try:
            result = subprocess.run(
                ["git", "--literal-pathspecs", "-c", "core.quotepath=false", "-C",
                 str(self.root), *args], input=input, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, timeout=timeout, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitError(safe_error(str(exc))) from exc
        if result.returncode not in allowed:
            raise GitError(safe_error(result.stderr.decode("utf-8", "replace").strip()
                                     or f"git {args[0]} exited {result.returncode}"))
        return result

    def text(self, *args, **kwargs):
        try:
            return self.run(*args, **kwargs).stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GitError("Git 輸出不是可完整解碼的 UTF-8；無法完整核對。") from exc

    def head(self):
        result = self.run("rev-parse", "--verify", "HEAD^{commit}", allowed=(0, 1, 128))
        return result.stdout.decode("ascii").strip() if result.returncode == 0 else None

    def branch(self):
        result = self.run("symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1))
        return result.stdout.decode("utf-8").strip() if result.returncode == 0 else None

    def index_digest(self):
        return hashlib.sha256(self.run("ls-files", "--stage", "-z").stdout).hexdigest()

    def remote_tip(self, remote, target):
        output = self.text("ls-remote", "--refs", remote, f"refs/heads/{target}")
        rows = [row.split("\t") for row in output.splitlines() if row]
        expected = f"refs/heads/{target}"
        matches = [row[0] for row in rows if len(row) == 2 and row[1] == expected
                   and OID.fullmatch(row[0])]
        if len(matches) != 1:
            raise GitError(f"遠端目標分支 {target} 不存在或無法唯一識別。")
        return matches[0]

    def default_branch(self, remote):
        output = self.text("ls-remote", "--symref", remote, "HEAD")
        for row in output.splitlines():
            if row.startswith("ref: refs/heads/") and row.endswith("\tHEAD"):
                return row.split("\t")[0][len("ref: refs/heads/"):]
        raise GitError("遠端沒有提供預設分支；請確認遠端 HEAD 設定。")

    def fetch_target(self, remote, target):
        self.run("check-ref-format", f"refs/heads/{target}")
        tracking = f"refs/remotes/{remote}/{target}"
        self.run("check-ref-format", tracking)
        self.run("-c", "fetch.writeCommitGraph=false", "-c", "gc.auto=0", "-c",
                 "maintenance.auto=false", "fetch", "--no-tags", "--no-recurse-submodules",
                 "--no-write-fetch-head", "--no-auto-maintenance", "--refmap=", remote,
                 f"+refs/heads/{target}:{tracking}")
        sha = self.text("rev-parse", "--verify", f"{tracking}^{{commit}}").strip()
        if sha != self.remote_tip(remote, target):
            raise GitError("Fetch 後遠端目標已變動；請重新檢查。")
        return sha


class Identities:
    def __init__(self, git, aliases):
        self.git = git
        self.cache = {}
        configured = git.text("config", "--get", "user.email", allowed=(0, 1)).strip()
        primary = os.environ.get("GIT_AUTHOR_EMAIL") or configured
        self.emails = set()
        for email in ([primary] if primary else []) + list(aliases):
            self.emails.add(self.canonical("", email).casefold())
        if not self.emails:
            raise GitError("沒有本人 email；請提供 --self-email，或由開發者設定 Git 身分。")

    def canonical(self, name, email):
        key = (name, email)
        if key not in self.cache:
            if not email or any(c in email for c in "<>\r\n\0"):
                raise GitError("作者 email 無效，無法核對身分。")
            if any(c in name for c in "\r\n\0<>"):
                raise GitError("作者名稱無法安全解析。")
            mapped = self.git.text("check-mailmap", "--stdin",
                                   input=f"{name} <{email}>\n".encode("utf-8"))
            match = re.search(r"<([^<>\r\n]+)>\s*$", mapped)
            if not match:
                raise GitError("無法解析 .mailmap 的作者資訊。")
            self.cache[key] = match.group(1)
        return self.cache[key]

    def is_self(self, name, email):
        return self.canonical(name, email).casefold() in self.emails


def check(facts, key, status, detail):
    facts["checks"].append(dict(id=key, status=status, detail=detail))


def gap(facts, detail):
    if detail not in facts["gaps"]:
        facts["gaps"].append(detail)


def raw_files(data):
    parts = data.split(b"\0")
    files, pos = [], 0
    while pos < len(parts) and parts[pos]:
        header = parts[pos].decode("ascii").split()
        if len(header) != 5 or not header[0].startswith(":"):
            raise GitError("無法完整解析 Git raw diff。")
        status = header[4]
        old_path = parts[pos + 1].decode("utf-8")
        path = parts[pos + 2].decode("utf-8") if status[0] in "RC" else old_path
        pos += 3 if status[0] in "RC" else 2
        files.append(dict(status=status, old_path=old_path, path=path,
                          old_mode=header[0][1:], new_mode=header[1],
                          old_oid=header[2], new_oid=header[3]))
    return files


def changed_ranges(git, file):
    old, new = file["old_oid"], file["new_oid"]
    if not old.strip("0") or old == new:
        return []
    if file["old_mode"] == "160000" or file["new_mode"] == "160000":
        raise GitError(f"{file['path']} 是 submodule，無法用行作者核對。")
    old_text = git.run("cat-file", "blob", old).stdout
    if b"\0" in old_text:
        raise GitError(f"{file['path']} 的既有內容為二進位，無法判定行作者。")
    try:
        old_text.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GitError(f"{file['path']} 的既有文字無法完整解碼。") from exc
    if not new.strip("0"):
        return [(1, len(old_text.splitlines()))] if old_text else []
    patch = git.run("diff", "--no-ext-diff", "--no-textconv", "--no-color", "-U0",
                    old, new, "--").stdout
    if b"Binary files " in patch:
        raise GitError(f"{file['path']} 的二進位變更無法判定行作者。")
    return [(int(m[0]), int(m[1] or b"1"))
            for m in re.findall(rb"(?m)^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", patch)
            if int(m[1] or b"1") > 0]


def blame_rows(git, left, path, start, count):
    output = git.text("blame", "--root", "--line-porcelain", "-L",
                      f"{start},{start + count - 1}", left, "--", path)
    rows, current = [], None
    for line in output.splitlines():
        header = re.fullmatch(r"([0-9a-f]{40}|[0-9a-f]{64}) (\d+) (\d+)(?: \d+)?", line)
        if header:
            current = dict(origin_sha=header[1], line=int(header[3]))
        elif line.startswith("author ") and current is not None:
            current["author"] = line[7:]
        elif line.startswith("author-mail ") and current is not None:
            current["email"] = line[12:].strip("<>")
        elif line.startswith("\t") and current is not None:
            if "author" not in current or "email" not in current:
                raise GitError("Blame 的作者資料不完整。")
            rows.append(current)
            current = None
    if len(rows) != count:
        raise GitError(f"{path} 的 blame 行數不完整。")
    return rows


def attribution(git, facts, identities, left):
    if identities is None:
        return
    for file in facts["files"]:
        try:
            groups = []
            for start, count in changed_ranges(git, file):
                for row in blame_rows(git, left, file["old_path"], start, count):
                    if identities.is_self(row["author"], row["email"]):
                        continue
                    email = identities.canonical(row["author"], row["email"])
                    if (groups and groups[-1]["origin_sha"] == row["origin_sha"]
                            and groups[-1]["email"] == email
                            and groups[-1]["old_end"] + 1 == row["line"]):
                        groups[-1]["old_end"] = row["line"]
                    else:
                        groups.append(dict(kind="lines", path=file["path"],
                                           old_path=file["old_path"], left_sha=left,
                                           old_start=row["line"], old_end=row["line"],
                                           origin_sha=row["origin_sha"], author=row["author"],
                                           email=email))
            for item in groups:
                item["id"] = "lines-" + digest(item)[:20]
                facts["manual_items"].append(item)
        except (GitError, UnicodeError) as exc:
            gap(facts, safe_error(str(exc)))


def commits(git, facts, identities):
    left, head = facts["target_sha"], facts["head_sha"]
    rows = git.text("rev-list", "--reverse", "--topo-order", "--parents",
                    f"{left}..{head}").splitlines()
    for row in rows:
        sha, *parents = row.split()
        fields = git.text("show", "-s", "--encoding=UTF-8",
                          "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B", sha, "--").split("\0", 5)
        if len(fields) != 6 or fields[0] != sha:
            raise GitError("Commit 證據無法完整解析。")
        _, author, email, committer, committer_email, message = fields
        message = message.rstrip("\n")
        item = dict(sha=sha, parents=parents, author=author, email=email,
                    committer=committer, committer_email=committer_email,
                    subject=message.splitlines()[0] if message else "",
                    message=message, issues=issues(message))
        facts["commits"].append(item)
        if identities is not None and not identities.is_self(author, email):
            facts["manual_items"].append(dict(id="commit-" + sha, kind="commit",
                                               sha=sha, author=author,
                                               email=identities.canonical(author, email)))


def inspect(repo, mode, *, remote=None, target=None, self_emails=(), requested_issues=()):
    git = Git(repo)
    facts = dict(schema="commit-reviewer-facts/v1", mode=mode, repo=str(git.root),
                 captured_at=now(), git_version=git.text("--version").strip(),
                 head_sha=git.head(), branch=git.branch(), index_digest=git.index_digest(),
                 remote=None, target_branch=None, default_branch=None, target_sha=None,
                 merge_base_sha=None, checks=[], gaps=[], notices=[], files=[], commits=[],
                 manual_items=[], self_emails=[], requested_issues=list(requested_issues))
    identities = None
    try:
        identities = Identities(git, self_emails)
        facts["self_emails"] = sorted(identities.emails)
    except GitError as exc:
        gap(facts, str(exc))
    if git.text("rev-parse", "--is-shallow-repository").strip() == "true":
        gap(facts, "這是 shallow Repo；歷史／作者資料可能不完整，請補齊後重新檢查。")
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        marker_path = Path(git.text("rev-parse", "--git-path", marker).strip())
        if not marker_path.is_absolute():
            marker_path = git.root / marker_path
        if marker_path.exists():
            check(facts, "operation_in_progress", "fail", f"Git 操作尚未完成：{marker}")
    if git.run("ls-files", "--unmerged", "-z").stdout:
        check(facts, "unmerged_index", "fail", "暫存區仍有未解決的衝突。")
        gap(facts, "衝突內容無法完整核對。")
    if git.run("diff", "--quiet", "--no-ext-diff", "--no-textconv", allowed=(0, 1)).returncode:
        facts["notices"].append("工作目錄尚有未暫存變更；不包含在本次提交範圍。")
    if git.run("ls-files", "--others", "--exclude-standard", "-z").stdout:
        facts["notices"].append("存在未追蹤檔案；不包含在本次提交範圍。")
    patch = b""
    diff_args = None
    if mode == "commit":
        diff_args = ["--cached"] + ([facts["head_sha"]] if facts["head_sha"] else [])
        left = facts["head_sha"]
    else:
        left = None
        check(facts, "attached_branch", "pass" if facts["branch"] else "fail",
              f"目前分支：{facts['branch']}" if facts["branch"] else "目前為 detached HEAD。")
        if not facts["head_sha"]:
            check(facts, "has_head", "fail", "目前尚未有 Commit，無法檢查 MR。")
        try:
            remotes = git.text("remote").splitlines()
            if remote is None:
                if len(remotes) != 1:
                    raise GitError("遠端不唯一；請明確提供 --remote。")
                remote = remotes[0]
            if remote not in remotes:
                raise GitError("指定的遠端不在此 Repo；請確認 --remote。")
            facts["remote"] = remote
            try:
                facts["default_branch"] = git.default_branch(remote)
            except GitError as exc:
                gap(facts, str(exc))
            target = target or facts["default_branch"]
            if not target:
                raise GitError("無法決定目標分支；請提供 --target。")
            facts["target_branch"] = target
            facts["target_sha"] = git.fetch_target(remote, target)
            if facts["branch"]:
                mainline = facts["branch"] in (target, facts["default_branch"])
                check(facts, "feature_branch", "fail" if mainline else "pass",
                      "目前位於目標分支或預設主線。" if mainline else "目前位於功能／修正分支。")
            if facts["head_sha"]:
                ancestor = git.run("merge-base", "--is-ancestor", facts["target_sha"],
                                   facts["head_sha"], allowed=(0, 1)).returncode == 0
                check(facts, "latest_target", "pass" if ancestor else "fail",
                      "分支包含同步當下的最新目標分支。" if ancestor else "尚未以最新目標分支為基礎；請完成 Rebase 後重檢。")
                bases = git.text("merge-base", "--all", facts["target_sha"],
                                 facts["head_sha"], allowed=(0, 1)).splitlines()
                if len(bases) != 1:
                    raise GitError("共同祖先不存在或不唯一，無法完整比較。")
                left = facts["merge_base_sha"] = bases[0]
                diff_args = [left, facts["head_sha"]]
                commits(git, facts, identities)
                check(facts, "has_commits", "pass" if facts["commits"] else "fail",
                      f"本次新增 {len(facts['commits'])} 筆 Commit。")
                merges = [c["sha"] for c in facts["commits"] if len(c["parents"]) > 1]
                check(facts, "linear_history", "fail" if merges else "pass",
                      "本次範圍含 Merge Commit：" + ", ".join(merges) if merges else "本次範圍為線性歷史。")
                missing = [c["sha"] for c in facts["commits"] if not c["issues"]]
                check(facts, "issue_references", "fail" if missing else "pass",
                      "缺少 Issue 編號：" + ", ".join(missing) if missing else "本次每筆 Commit 均附 Issue 編號。")
        except GitError as exc:
            gap(facts, str(exc))
    if diff_args is not None:
        try:
            raw = git.run("diff", "--raw", "-z", "--no-abbrev", "--full-index", "-M",
                          "--no-ext-diff", "--no-textconv", *diff_args, "--").stdout
            facts["files"] = raw_files(raw)
            patch = git.run("diff", "--no-ext-diff", "--no-textconv", "--no-color",
                            "--full-index", "-M", *diff_args, "--").stdout
            patch.decode("utf-8")
            check(facts, "has_changes", "pass" if facts["files"] else "fail",
                  f"本次包含 {len(facts['files'])} 個檔案差異。" if facts["files"] else
                  ("沒有已暫存變更；請先選定提交範圍。" if mode == "commit" else "沒有可提出 MR 的檔案差異。"))
            attribution(git, facts, identities, left)
        except (GitError, UnicodeError, IndexError) as exc:
            gap(facts, safe_error(str(exc)))
    if mode == "mr":
        # This notice is informational: MR scope is committed HEAD only.
        if git.run("diff", "--cached", "--quiet", "--no-ext-diff", "--no-textconv",
                   allowed=(0, 1)).returncode:
            facts["notices"].append("暫存區另有尚未提交的內容；MR 報告只涵蓋已提交 HEAD。")
    if git.head() != facts["head_sha"] or git.branch() != facts["branch"]:
        gap(facts, "擷取期間 HEAD 或目前分支已變動；請重新檢查。")
    if mode == "commit" and git.index_digest() != facts["index_digest"]:
        gap(facts, "擷取期間暫存區已變動；請重新檢查。")
    facts["patch_sha256"] = hashlib.sha256(patch).hexdigest()
    facts["snapshot_id"] = digest(facts)
    return facts, patch


def verify(facts):
    reasons = []
    try:
        git = Git(facts["repo"])
        if git.head() != facts["head_sha"] or git.branch() != facts["branch"]:
            reasons.append("HEAD 或目前分支已改變，舊確認不再適用。")
        if facts["mode"] == "commit" and git.index_digest() != facts["index_digest"]:
            reasons.append("暫存區已改變，需重新核對 Commit 草稿。")
        if facts["mode"] == "mr":
            if not facts["remote"] or not facts["target_branch"] or not facts["target_sha"]:
                reasons.append("沒有成功同步並固定遠端目標版本。")
            elif git.remote_tip(facts["remote"], facts["target_branch"]) != facts["target_sha"]:
                reasons.append("遠端目標已前進或改寫，請重新檢查。")
    except GitError as exc:
        reasons.append(str(exc))
    return dict(checked_at=now(), current=not reasons, reasons=reasons)
