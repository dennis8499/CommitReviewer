# CLI 與人工確認

需要 Python 3.10+ 與 Git（本次以 2.55.0.windows.5 驗證）；僅使用 Python 標準庫，不呼叫 GitLab API。以下命令從任意 cwd 執行，Repo 以 `--repo` 選定。PowerShell 的 `#123` 必須加引號。

## inspect

~~~text
python -X utf8 -B <skill-dir>/scripts/commit_review.py inspect
  --repo <repo> --mode commit|mr
  [--remote <remote-name>] [--target <branch-name>]
  [--self-email <email> ...] [--issue "#123" ...]
  [--output-dir <new-directory>]
~~~

- 一次一個 Repo／模式。`--target` 例如 `main`、`master`、`release/1.0`，不是本機 ref 或 `origin/main`。
- MR 模式只有一個遠端時自動選定；多個／沒有遠端時記錄缺口，補齊後另建快照。沒有 target 時向所選遠端查詢預設分支。Commit 模式完全不連網，remote／target 不適用。
- MR 只 Fetch `refs/heads/<target>` 至對應遠端追蹤 ref，停用 tag、submodule、額外 ref mapping、FETCH_HEAD 寫入與自動維護。Fetch 後再查遠端 tip；失敗不能套用既有 tracking ref。
- 本人 email 來自 `GIT_AUTHOR_EMAIL` 或 Repo 有效 `user.email`；附加別名用重複的 `--self-email`。Git 的 `.mailmap` 會套用於本人與歷史作者；只比 email，不以姓名推定同一人。
- `--issue` 是 Commit 草稿需包含的引用，可重複。MR 已有 Commit 各自至少一個有效引用，不強制全部使用同一卡號。Issue 是否存在及是否對應本次任務需由開發者確認。
- `--output-dir` 必須是尚未存在的目錄。省略時在系統暫存目錄建立唯一目錄；限制檔案寫入的環境建議明確提供工作區內的目錄。

stdout 是 JSON，指向三個本機檔案：

- `snapshot.json`：`commit-reviewer-facts/v1`。含 snapshot_id、mode、repo、目前分支、完整 SHA、index 摘要、checks、gaps、notices、files、commits、manual_items、self_emails 與 patch checksum。
- `changes.patch`：固定比較的完整 Git diff。Commit 使用已暫存內容；MR 使用唯一共同祖先至固定 HEAD。snapshot 與 patch 是唯讀證據。
- `analysis.json`：agent 可編輯的摘要、Commit 草稿與人工確認。保存路徑以便續跑，不將原始 diff 或作者資料自動上傳。

checks.status 為 `pass`／`fail`；資料不足記錄於 gaps。inspect exit 0 表示保存成功，仍可能有不符合條件或缺口。嚴重輸入／執行錯誤 exit 2。

## 分析與確認

~~~json
{
  "snapshot_id": "從本次 snapshot 複製的完整 ID",
  "summary": "新增登入的雙因素驗證，並調整驗證流程。",
  "diff_reviewed": true,
  "commit_message": "feat(login): 新增雙因素驗證\n\nRefs: #123",
  "project_findings": [],
  "confirmations": [
    {
      "item_id": "從本次 manual_items 複製的完整 ID",
      "confirmed_by": "實際確認的開發者",
      "reason": "已確認此項為 #123 所需的異動"
    }
  ]
}
~~~

初始 summary／commit_message 為空、diff_reviewed 為 false、confirmations 為空陣列。不要原樣照抄範例 ID。只有真實確認才加入 confirmations；開發者未確認時維持空陣列或只記已確認項目。MR 模式不使用 commit_message。

`diff_reviewed` 是 agent 對變更摘要／草稿與差異的核對，不是人類核准，也不是完整程式碼安全檢查。資料缺口不會因為設為 true 或填確認而消失。

有明確專案提交規範違反時，在 project_findings 加入 `{"rule":"AGENTS.md 的具體規範與位置","status":"fail","detail":"本次違反的 Commit 或草稿及原因"}`。規範適用性無法確認則用 unknown。沒有此類發現維持空陣列，不把一般建議升級為硬性規範。

現有 `.mailmap`／設定或新增別名影響判斷時，另建快照並重新核對；不要直接修改舊 facts 中的 email。相同姓名、不同 email 的作者仍需明示別名／mailmap 或人工確認。

## verify 與 report

~~~powershell
python -X utf8 -B "<skill-dir>/scripts/commit_review.py" verify --snapshot "<snapshot.json>"
python -X utf8 -B "<skill-dir>/scripts/commit_review.py" report --snapshot "<snapshot.json>" --analysis "<analysis.json>"
~~~

verify 純重查：HEAD、目前分支、Commit 模式暫存區及 MR 遠端目標 hash；不 Fetch。版本一致 exit 0，已變動或無法確認 exit 3，檔案／格式錯誤 exit 2。MR 需要遠端可連線，無法確認時不沿用過去結果。

report 驗證 snapshot／patch checksum 與 analysis.snapshot_id，重查版本，再依固定事實填報告。預設檔名為 `review-reports/commit-review-<Taipei timestamp>-<snapshot ID prefix>.md`。`--output` 可指定同一目錄下尚未存在的 `.md`，不覆寫既有檔案。

報告結果：

| status | 意義 |
| --- | --- |
| pass | 本次檢查符合，必要資料完整、摘要已核對、所有他人異動已人工確認 |
| fail | 至少有一項明確不符合條件 |
| unknown | 沒有明確失敗，但必要來源不足或版本已變動 |
| pending | Git 條件與資料完整，仍待摘要／人工確認 |

失敗、缺口與待確認仍分開列於報告，不因整體狀態優先順序而省略。report exit 0 = pass；exit 3 = 報告已保存但尚未符合；exit 2 = 無法產生報告。

確認完同一版本的人工項目後，可重新 report 產生新檔案。HEAD、分支、暫存區或目標版本改變時，重新 inspect，重新閱讀必要差異與核對。新 analysis 必須使用新 snapshot_id；只有 Repo、分支、HEAD／目標／共同祖先 SHA、本人身分、Commit 模式的暫存摘要與項目 ID 全部一致，才可引用先前已明確給出的確認，並保留確認人及理由。版本條件改變則重新核對，不直接移植舊 analysis。

snapshot checksum 用於檢查意外變動，不是簽章或防竄改認證。原始證據在本機供續跑；需要保存時由使用者選擇位置。
