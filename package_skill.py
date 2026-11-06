"""Build the canonical, self-contained CommitReviewer release assets."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import re
from zipfile import ZIP_STORED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent
TEXT = {".md", ".py", ".yaml", ".yml", ".json", ".txt", ".toml"}


def build(*, tag=None, source=None, output_dir=None):
    source = Path(source) if source is not None else ROOT / "skills/commit-reviewer"
    output_dir = Path(output_dir) if output_dir is not None else ROOT / "dist"
    version = (source / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ValueError("VERSION must be stable X.Y.Z")
    if tag is not None and tag != "v" + version:
        raise ValueError("tag must match VERSION")
    if source.is_symlink() or not (source / "SKILL.md").is_file():
        raise ValueError("invalid Skill directory")
    entries = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Skill package cannot contain links")
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        content = path.read_bytes()
        if path.name == "VERSION" or path.suffix.lower() in TEXT:
            content = content.replace(b"\r\n", b"\n")
        entries["commit-reviewer/" + path.relative_to(source).as_posix()] = content
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"commit-reviewer-{version}.zip"
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for name, content in entries.items():
            info = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = ZIP_STORED
            archive.writestr(info, content)
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{checksum}  {output.name}\n", encoding="ascii", newline="\n")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    args = parser.parse_args()
    try:
        print(build(tag=args.tag))
    except (ValueError, OSError) as error:
        parser.exit(2, f"package-skill: {error}\n")
