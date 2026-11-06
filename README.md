# Commit Reviewer

給開發者使用的 Codex 技能：準備 Commit 訊息，並在發 GitLab MR 前檢查分支、最新主線、線性歷史、Issue 編號與他人異動。MR 報告逐筆列出完整 Commit hash，方便 Reviewer 核對。

## 使用方式

安裝後可在 Codex 輸入：

~~~text
使用 $commit-reviewer，幫我檢查這個 Repo 的已暫存變更，準備 #123 的 Commit 草稿。

使用 $commit-reviewer，檢查 C:/work/my-repo 是否可以發 MR。
目標是 origin 的 main，請產生繁中報告。
~~~

技能支援正常自動辨識，也能以 `$commit-reviewer` 明確呼叫。

| 能力 | 行為 |
| --- | --- |
| Commit 草稿 | 只依已暫存內容，優先專案規範，否則採 Conventional Commits 加 `Refs: #123` |
| MR 條件 | 功能分支、包含最新目標、無 Merge Commit、具新增 Commit 與檔案差異 |
| 他人異動 | 分開列出其他作者 Commit，以及修改／刪除行的歷史最後修改者，待開發者確認 |
| Issue | 每筆 Commit 包含 `#123` 或 `group/project#123`；草稿可指定多張卡 |
| 報告 | Repo 的 `review-reports/` 內產生 Markdown，附完整 SHA、比較基準、作者與人工核對欄位 |

只精確 Fetch 本次目標分支。Commit、Stage、Rebase、Push 與建立 MR 由開發者執行。Commit 草稿不會有新 hash；MR 報告只包含已提交的 HEAD。

## 安裝

需要 Python 3.10+ 與 Git，helper 不依賴額外 Python 套件或 GitLab Token。本次以 Python 3.14、Git 2.55.0.windows.5 驗證。技能來源在 `skills/commit-reviewer/`，版本為 0.1.0，可從 [GitHub Releases](https://github.com/dennis8499/CommitReviewer/releases) 下載獨立套件，或透過 WorkflowKit 安裝。

在本專案根目錄開 PowerShell，安裝至個人 Codex 技能目錄：

~~~powershell
$destinationRoot = Join-Path $env:USERPROFILE '.codex\skills'
$destination = Join-Path $destinationRoot 'commit-reviewer'
if (Test-Path -LiteralPath $destination) { throw '目的地已存在，請先決定升級方式，避免覆蓋既有技能。' }
New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
Copy-Item -LiteralPath '.\skills\commit-reviewer' -Destination $destination -Recurse
~~~

若有設定 `CODEX_HOME`，將目的根目錄改為該目錄下的 `skills`。也可安裝到目標工作區的 `.agents/skills/commit-reviewer/`，只供該工作區使用。安裝後下一輪對話即可使用。來源目錄本身也可透過完整 SKILL.md 路徑明確交給 agent 使用。

## CLI 與人工確認

兩種模式都先收集固定證據，agent 讀完差異後編輯分析，再重查版本並產生報告：

~~~powershell
python -X utf8 -B .\skills\commit-reviewer\scripts\commit_review.py inspect --repo 'C:\work\my-repo' --mode commit --issue '#123'
python -X utf8 -B .\skills\commit-reviewer\scripts\commit_review.py inspect --repo 'C:\work\my-repo' --mode mr --remote origin --target main
~~~

stdout 回傳 snapshot、analysis 與 patch 路徑。保留 snapshot／patch，閱讀差異後編輯 analysis 的摘要、草稿與真實人工確認，再執行：

~~~powershell
python -X utf8 -B .\skills\commit-reviewer\scripts\commit_review.py report --snapshot '<回傳的 snapshot.json>' --analysis '<回傳的 analysis.json>'
~~~

限制寫入的執行環境可用 `--output-dir` 指定工作區內尚未存在的證據目錄。多遠端需指定 `--remote`；目標採 MR 目標或遠端預設分支。額外本人 email 可重複傳 `--self-email`，不修改 Git 設定。

詳見 [CLI 與人工確認](skills/commit-reviewer/references/workflow.md)。CLI 不會自行寫出功能摘要；直接執行 inspect 不代表已完成 agent 的內容核對。report 的 exit 3 代表報告已保存，仍有不符合、缺口或待確認項目。

報告發布前重查 HEAD、目前分支、暫存區或遠端目標。人工確認綁定快照；SHA 改變就重檢。手動勾選報告不會更新程式判斷，需編輯同一 analysis 的確認紀錄後重新產生。

## 驗證與打包

~~~powershell
python -X utf8 -B -m unittest discover -s tests -v
python -X utf8 -B package_skill.py
~~~

測試只使用 `.test-runs/` 內的隔離 Repo 與本機 bare remote，不連接外部服務。完成後清理自己建立的 Repo。Windows 沙箱若阻擋 Git 內部 sh 程序，需在允許 Git 子程序的終端執行測試。

打包輸出 `dist/commit-reviewer-0.1.0.zip` 與 `.zip.sha256`，ZIP 僅包含 `commit-reviewer/` 完整技能。解壓後將技能資料夾放入安裝位置即可。封裝固定檔案順序、換行及 ZIP metadata；`--tag v0.1.0` 可核對版本。

推送至 `main` 會執行 Windows／Python 3.10、3.14 測試。待 CI 通過後，將 `v0.1.0` 標籤推送到相同提交；發布流程核對 main、標籤、CI 證據與資產雜湊，下載校驗後才轉為正式 Release。

## 判斷範圍

歷史作者代表最後修改紀錄，不代表程式碼所有權；二進位變更、shallow 歷史及其他來源不足會保留缺口。GitLab CI、權限、核准、卡號是否存在與實際任務對應性沿用團隊既有流程。線性且包含最新主線的結果，只對檢查當下固定 SHA 有效。

設計參考：[GitLab AI Skills](https://gitlab.com/gitlab-org/ai/skills)、[Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)、[GitLab Issue 關聯](https://docs.gitlab.com/user/markdown/#gitlab-specific-references)。本專案未複製其技能程式碼。
