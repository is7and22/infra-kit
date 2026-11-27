# ansible-facts：驗證步驟記下指令輸出，部署事實加 evidence 並與元件結果對照

主題歸：download-test-sensitive-py（協調 session 名稱，回報與提問用 SendMessage 傳給它）
角色：backend
改的 repo：`infra-kit`（單子裡的檔案路徑都相對這個 repo）

## 一句話

驗收文件要用主機上標準指令（例 `docker ps`）的輸出當舉證。callback 讓驗證步驟可記下「執行的指令」與「輸出」；轉接器依 `project.yaml` 新的 `evidence` 一節把它們寫進 `部署事實.json`，並對照元件的 Pass：元件判 Pass 而指令輸出裡找不到它，寫進 `problems`。

## 已經做完

- `ansible-facts/callback_plugins/verify_records.py`：`REPORT_KEYS` 有 `report_service`、`report_method`、`report_version`（README 規則 V3、V6）
- 轉接器 `ansible-facts/ansible_facts.py` 依 V7 只採正式執行、每個（playbook、主機）取最近一次
- v0.2.0 是現行 tag

## 這一步要改

1. callback：V3 加兩個鍵，紀錄加兩個欄位
   - `report_command`（字串，顯示用的指令原文）→ 紀錄 `command`
   - `report_output`（字串，指令的 stdout）→ 紀錄 `output`
   - 輸出超過 200 行時截斷並在末行註明截斷（寫進 V3）
2. `project.yaml` 加 `evidence` 一節，README 新增規則（編號自訂，接在現有表後）：
   - 清單，每項 `{id, title, hosts, playbook, step}`，全部必填；`id` 不重複
   - `hosts` 寫法同 C4、C5；`step` 規則同 C12（要找得到、只出現一次）
   - `title` 可含 `{host}`，代入主機名
   - 那一步的紀錄要有 `command` 與 `output`，沒有就寫 `problems`
3. 元件對照：`components[]` 可加 `evidence: {id, line}`
   - `line` 是正規式，可含 `{version}`，代入該實例的版本（正規式跳脫後代入）
   - 對每個 `check.result` 是 `pass` 的實例：同主機那筆 `evidence[id]` 的 `output` 要有一行符合 `line`；找不到那筆或沒有符合的行，寫 `problems`，點名元件、主機、`id`、`line`
   - `check` 是 `null` 或 `fail` 的實例不對照
4. `部署事實.json` 加 `evidence`，格式如下（與 acceptance-docs 的範本單相同，兩邊照這段寫）：
   ```
   "evidence": [
     {"id": "docker_ps", "title": "web01 容器狀態檢查", "host": "web01",
      "command": "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'",
      "output": "NAMES   IMAGE   STATUS\n...", "time": "2026-11-27T10:00:00+08:00"}
   ]
   ```
   - 順序：`evidence` 清單順序，同一項內依 `hosts` 順序
   - 採用規則同 V7（正式執行、最近一次、skipped 不採）；沒採到的主機不輸出那筆並寫 `problems`
   - `project.yaml` 沒有 `evidence` 時不輸出這個鍵
5. 測試：`tests/` 的假項目加 `evidence` 與元件對照；`CASES` 每條新規則一份會紅的變體；`test_verify_records.py` 真跑一步帶 `report_command`、`report_output`
6. `tests/acceptance-docs/` 的 `check_facts.py` 與 schema 是 acceptance-docs 的複本：等 acceptance-docs 的範本單（同名主題 `admin-manual`）併 main 後，照該目錄 README 更新複本。它還沒併就先做 1 到 5，最後一步再更新

實際路徑：主機執行標準指令 → 驗證步驟帶 `report_command`、`report_output` → callback 寫 `logs/results/*.jsonl` → 轉接器依 `evidence` 寫 `部署事實.json`，元件對照不符寫 `problems` → 驗收文件產生指令
寫入端：`evidence` 只由轉接器寫；紀錄的 `command`、`output` 只由 callback 寫
驗收分派：執行者全做並自驗；打 tag 由協調決定
手冊：不適用（共用元件，沒有使用者畫面）

authorized: 「寫單派出去」（2026-11-27，使用者對協調 session，回答「要現在寫單並派出去嗎？」）
範圍：`ansible-facts/` 的 callback、轉接器、README 規則、測試；更新 `tests/acceptance-docs/` 複本。

## 驗收

1. 測試與 `pre-commit run --all-files` 綠
2. 元件對照會紅：假紀錄裡元件 pass、版本 `1.2.3`，`docker_ps` 的輸出那一行寫成 `1.2.4` 或 `Exited`，`problems` 點名該元件與主機；改回 `1.2.3` 且 `Up` 時沒有這筆 problem
3. `--check` 的執行帶了 `output`，轉接器不採（`evidence` 沒有那筆、寫 `problems`）
4. `report_output` 300 行：紀錄 200 行加截斷註記
5. 舊項目（`project.yaml` 沒有 `evidence`）的輸出與改動前逐字相同（用現有假項目比對）

## 最容易漏的地方

- `{version}` 代入前要跳脫：版本 `v2026.11.26.2` 的 `.` 沒跳脫時會配到 `v2026x11...`，對照該紅而綠，無錯誤訊息
- 同一台多次執行：`evidence` 的那步與元件的那步可能在不同次執行；各自取最近一次正式執行，不要求同一次
- V4：`report_output` 照樣寫進紀錄，`no_log` 也擋不住。README 寫明只給不含密碼的指令（`docker ps`、版本查詢）

## 不要做

- 不打 tag、不改任何項目 repo（案子 repo 另一張單引用新 tag）
- 不改 `evidence` 的格式；發現行不通就停下來回報（acceptance-docs 照同一格式寫）
- 不在 README 或測試放客戶的主機名、IP

## 進度

- 17:36 infra-kit-03：步 1–5 做完（commit 90eba8e）：callback 記 `command`、`output`（超過 200 行截斷）；轉接器依 `evidence` 寫 `部署事實.json`、元件對照不符寫 `problems`；README 加 E1–E8、改 V3、V4、V6、C1、C3，原「七、八」改成「八、九」。驗收 2–5 有測試；驗收 5 另用 origin/main 的轉接器與新轉接器跑同一份舊假項目，除 `generated_at` 外逐字相同。突變：拿掉 `{version}` 的 `re.escape`，`test_compare_red_and_green` 的 `v1x2x3` 那個子案例變紅，已還原。剩下：步 6 等 acceptance-docs 的 admin-manual 併 main，現在 `test_exit_ok_and_passes_check_facts` 因 schema 複本不認得 `evidence` 而紅
- 17:41 infra-kit-03：diff 審查：唯讀子代理載 `zh-deslop`（這台沒有 `python-expert`，Python 照一般標準審）。已修：skipped 的步驟 callback 不代入 `report_command`、`report_output`（原本引用被跳過的 register 會變 `report_error`、蓋掉 `report_hosts`，callback 測試加這情形）；E8 鎖定處代入上游版本補紅綠測試；E5 每種不採的情形各一個子案例；problems 改用內容篩、不靠位置；E8 補「符合的行被截掉也寫 problems」。不修：驗收 5 不存 golden json（手動與審查各比過一次逐字相同，golden 會擋到以後正當的改動）；V7 採用判斷三份（元件、連線、evidence）不在這張單抽共用；V3 一格六個鍵先不拆表。測試 43 條只紅 `test_exit_ok_and_passes_check_facts`（等步 6）；pre-commit 綠
