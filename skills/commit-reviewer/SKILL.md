---
name: commit-reviewer
description: 協助撰寫與核對已暫存的 Commit 訊息，或檢查 GitLab 發 MR 前的主線同步、線性歷史、Issue 編號與他人異動；產生含完整 Commit hash 的繁中報告。完整程式碼合併審查與上版檢查另循其流程。
---

# Commit Reviewer

對單一 Repo 提供 Commit 準備與 MR 前檢查。helper 收集固定 Git 事實，agent 閱讀差異、撰寫摘要與訊息。helper 成功不代表已理解變更或已符合 MR 條件。

## 共同規則

- 只執行檢查、精確 Fetch 及建立本機證據／報告。Commit、Stage、Rebase、Push 與建立 MR 由開發者執行；修正建議呈現為可供開發者採用的指令。
- 使用 helper 固定的 SHA、差異及作者資料。現有 Commit 的 hash 必須來自 Git；草稿標示「尚未提交」，沒有新 hash。
- 他人行內容以**比較前版本**的歷史最後修改者判斷，與其他作者 Commit 分開列出；歷史作者不等於程式碼所有權。以 email、`.mailmap` 與明示別名辨識本人，不以 committer 或姓名代替 author。
- 每項他人異動需要真實開發者確認。沿用版本與項目均相同的既有明確確認，只詢問尚未確認的項目；agent 不代填人工核准。未確認仍可交付待確認報告。
- 資料缺口、網路失敗及版本變動保持未確認，不以舊遠端 ref 代替最新主線。程式碼及暫存區保持原樣，報告不自動加入版控。

## 1. 選定 Repo 與模式

使用者提供 Repo 時解析該 Repo；否則使用當前 Git Repo。多 Repo 工作區沒有明確入口時，先詢問受檢 Repo。

- 「幫我寫 Commit／核對提交訊息」使用 `commit`：只涵蓋已暫存差異。
- 「能否發 MR／檢查 Rebase／準備 MR 報告」使用 `mr`：只涵蓋已提交 HEAD。
- 兩項都要求時各建立獨立快照，不能把尚未提交的內容寫入 MR Commit 證據。

先讀適用的 `AGENTS.md`、專案提交規範／commitlint 設定；無明確規範才參考最近 20 筆訊息。只在遠端有歧義、目標不明、本人 email 缺少或 Issue 不明且需要寫草稿時補問。MR 目標採使用者明確指定，否則由選定遠端的預設分支決定；多遠端須指定 `--remote`。

命令與分析欄位見 [CLI 與人工確認](references/workflow.md)。證據目錄用每次新建的路徑，優先放在受檢 Repo 外、工作區內的 `.commit-reviewer-runtime/`。

## 2. 固定證據與閱讀差異

~~~powershell
python -X utf8 -B "<skill-dir>/scripts/commit_review.py" inspect --repo "<repo>" --mode commit --issue "#123" --output-dir "<workspace>/.commit-reviewer-runtime/<unique>"
python -X utf8 -B "<skill-dir>/scripts/commit_review.py" inspect --repo "<repo>" --mode mr --remote "origin" --target "main" --output-dir "<workspace>/.commit-reviewer-runtime/<unique>"
~~~

按實際模式執行其中一條。MR 的 `--remote`、`--target` 在可唯一判斷時可省略；`--target` 是遠端分支名稱，不含 `origin/` 前綴。本人多個 email 可重複提供 `--self-email`，不更改 Git 設定。

保存回傳的 `snapshot`、`analysis`、`patch` 路徑與 snapshot_id。閱讀完整 `snapshot.json`、`changes.patch` 及理解差異所需的專案規範／背景。大型差異分段續讀，不以摘要或檔名代替實際差異。

- Commit 模式沒有已暫存變更時，提示開發者先選定提交範圍；不自行 Stage，也不把未暫存內容混入草稿。
- MR 模式以精確 Fetch 的最新目標 SHA 檢查：目前不在主線／目標分支、目標為 HEAD 祖先、本次範圍無 Merge Commit、具有新增 Commit 與檔案差異、每筆 Commit 含 Issue 引用。
- 修改／刪除行的作者證據錨定比較前的檔案及行號。二進位、非完整文字、shallow 歷史或其他缺口照實保留；純改名不誤稱修改原有行。

## 3. 撰寫草稿與人工核對

只編輯回傳的 `analysis.json`，維持 snapshot_id；不可改寫 snapshot／patch 讓檢查通過。

寫出差異支持的繁中 `summary`。讀完必要差異、核對摘要與草稿確實符合本次範圍後，才設 `diff_reviewed: true`；這表示提交內容核對，不宣稱完成完整 Code Review。

專案明確提交規範有違反或無法確認時，在 `project_findings` 記錄規範來源、具體發現及 fail／unknown；不得只寫在摘要而讓報告判定符合。

Commit 草稿優先遵循專案明確規範，否則使用 Conventional Commits：

~~~text
feat(login): 新增雙因素驗證

Refs: #123
~~~

依實際變更選類型與摘要，scope／body 按需要提供；動機只寫使用者或證據已支持的內容。Issue 採使用者指定的 `#123` 或 `group/project#123`，可多筆；不從分支名稱自行確定卡號。`Refs:` 是預設關聯方式，只有使用者要求自動關卡才建議 `Closes`。Conventional Commits 是草稿預設；既有 Commit 的共同硬性訊息要求只有有效 Issue 引用及專案明確規範。

逐筆呈現 `manual_items` 的 ID、作者、檔案行段／Commit 與完整 hash。收到開發者明確確認後，在 confirmations 記錄該項 ID、確認人及理由。analysis 綁定本次 snapshot_id。另建快照時，只有 Repo、HEAD／目標／共同祖先 SHA、身分、Commit 模式的暫存摘要與項目 ID 全部相同，才可沿用原本明確確認；任何版本條件變動均重新核對。

## 4. 重查並交付報告

~~~powershell
python -X utf8 -B "<skill-dir>/scripts/commit_review.py" report --snapshot "<snapshot.json>" --analysis "<analysis.json>"
~~~

helper 發布前重查 HEAD、目前分支、Commit 模式的暫存區，以及 MR 模式的遠端目標 SHA。版本變動時交付需重新檢查的報告，不能宣稱符合。產生檔案依 [報告模板](assets/mr-report.md)，位於受檢 Repo 的 `review-reports/`，內容包含實際完整 hash 與人工核對欄位。

`inspect` exit 0 只表示已保存資料；`report` exit 0 表示本次條件符合，exit 3 表示報告已產生但仍不符合／待確認／資料不足，exit 2 是錯誤。不可把 exit 3 當作命令失敗而反覆重跑。

回覆報告連結、主要不符合條件與待人工核對事項。結論只適用報告固定版本與本技能條件；GitLab CI、權限、核准與 Issue 存在性沿用既有流程。手動勾選 Markdown 不會重算結果，需以同一分析紀錄重新產生報告。
