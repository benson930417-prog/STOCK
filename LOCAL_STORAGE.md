# OneDrive 原始碼與本機執行目錄

2026-10-13 起，本文件優先於舊 README 內在 OneDrive 建立 venv、直接跑研究或寫入 tmp 的做法。依使用者最新要求，備份也只存在本機。

## 兩種位置

- 長期保存：`%LOCALAPPDATA%\Programs\TradingWorkspace`。內含 `workspaces` 執行副本、研究結果、SDK、DB、log，`git` 完整 Git metadata，`history` 歷史封存，以及 `state`／`migration-20261013` 的核對清冊。
- 短期可重建：`%TEMP%\TradingWorkspace`。內含 Python／pytest／pip／Matplotlib／npm／UV 快取及新程序 TEMP/TMP。停止相關程序後可清除；不存唯一的帳務或研究歷史。
- OneDrive 只保留程式、文件、設定範本、少量靜態資料素材，以及小型 `.git` 指標。沒有執行環境、完整 Git 目錄或備份封存。

每個專案的本機工作目錄仍保留原相對路徑結構。venv 在執行副本的 `.venv`；Playwright 瀏覽器在本機根目錄 `browsers`。不使用 junction/symlink 把高頻檔案接回 OneDrive。

## 使用方式

- 原有帳務網站、策略网站與研究啟動腳本會先準備本機副本，再從本機執行。
- 任意命令：`local-run.cmd python relative/script.py ...` 或 `local-run.cmd node relative/test.cjs`；不帶參數會開啟本機工作目錄的 PowerShell。
- 初始化／套件更新：雙擊 `initialize-local.cmd`。使用 `requirements-local.txt`，不更動正式伺服器 requirements。
- 查看位置：`python -B local_workspace.py status`。
- 保存歷史：先停止本機資料寫入程序，再雙擊 `save-history.cmd`。輸出只到 AppData 的 `history/<專案>/.portable-history`，每塊最多 32 MiB，附 SHA-256；不是排程，也不會於每次啟動更新。

原始碼以 OneDrive 為準，下次啟動才同步至本機，沒有 watcher。直接修改本機程式會觸發覆蓋衝突，必須先將修改帶回原始碼。原始碼刪除只移除先前同步且未修改的程式副本，並留本機備份；資料 seed 不隨來源刪除或更新覆蓋。

不可從 OneDrive 直接跑 `python research.py`、pytest、建立 venv 或安裝本地套件；請使用 local-run。外部程式自行硬寫 OneDrive 路徑不在啟動工具可防止的範圍內。

## 換機與歷史

先安裝 Python 3.12+、Git，取得 OneDrive 原始碼，並將舊機的 `history` 複製到新機同樣的 AppData 根目錄，再雙擊初始化。程式環境會重建，資料只補缺檔、不覆蓋較新的本機資料。**只有 OneDrive 不足以還原本機歷史**；缺少 Git bundle 時會明確停止，不能用空 Git 假裝歷史已恢復。

Git 操作仍在 OneDrive 原始碼目錄執行，metadata 實際在 AppData。bundle 保存 refs 可達的 commit 歷史，不包含 reflog／未提交 index；需要完整 metadata 時另外搬移 `git`。換機時不要直接沿用舊 venv，應重建。私鑰、券商憑證與私人機器設定由既有保險庫另行轉移。

封存包含研究產物、資料 seed、SDK 與 Git bundle；不包含可重建 venv、快取、tmp、log、機器秘密。log 與原始暫存仍保留在本機。驗證完成的搬移中間備份、還原測試副本與重複壓縮檔可刪除；正式資料、最新歷史封存及核對清冊保留。

本次只調整兩個 Windows 專案；正式 ARM/X64 交易排程、E-STOP、策略與資料庫均未遷移或重啟。本機歷史不能代替伺服器權威 DB 的災難備份。

## OneDrive 重建前

依你的安排自行處理 OneDrive；此工具不重啟、清空或重設 OneDrive。備份原始碼時也請另外保存 AppData 的 `TradingWorkspace`，其中有 OneDrive 不含的正式本機歷史、Git 與私密設定。Temp 不需帶到新電腦。

STOCK 的 `start-stock.cmd` 透過既有 SSH 權限開啟 ARM 網站。`start-stock.ps1 -Local` 僅供開發，須先設定 `STOCK_GLOBAL_MARKET_DB`。
