"""Package the self-contained skill, excluding test/runtime and Python cache files."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def build():
    root = Path(__file__).resolve().parent
    source = root / "skills" / "commit-reviewer"
    output = root / "dist" / "commit-reviewer.zip"
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo"):
                archive.write(path, (Path("commit-reviewer") / path.relative_to(source)).as_posix())
        archive.writestr("INSTALL.md", r"""# 安裝 Commit Reviewer

需要 Python 3.10+ 與 Git。解壓後，將整個 `commit-reviewer/` 資料夾放入個人 Codex 技能目錄，或目標工作區的 `.agents/skills/`。

在解壓目錄開啟 PowerShell，可安裝到預設個人技能位置：

```powershell
$destinationRoot = Join-Path $env:USERPROFILE '.codex\skills'
$destination = Join-Path $destinationRoot 'commit-reviewer'
if (Test-Path -LiteralPath $destination) { throw '目的地已存在，請先決定升級方式。' }
New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
Copy-Item -LiteralPath '.\commit-reviewer' -Destination $destination -Recurse
```

若設定 CODEX_HOME，請改用該目錄下的 skills。安裝後下一輪對話可輸入：

```text
使用 $commit-reviewer 檢查目前 Repo 是否可以發 MR，並產生繁中報告。
```

只有精確 Fetch、本機檢查、草稿與報告；Commit／Rebase／Push／建立 MR 由開發者執行。
操作細節見 [CLI 與人工確認](commit-reviewer/references/workflow.md)。
""")
    print(output)
    return output


if __name__ == "__main__":
    build()
