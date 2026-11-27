# ansible-facts：用 ansible 部署的項目輸出部署事實

驗收文件的軟體表、連線表與指令輸出舉證，由一份與部署工具無關的 `部署事實.json` 組出來（格式由驗收文件 repo 的 `schemas/部署事實.schema.json` 定）。本目錄是 ansible 的轉接器：讀項目的 inventory、版本鎖定處、驗證紀錄與 `project.yaml`，寫出 `部署事實.json`。

本檔是轉接器的規格：項目的 ansible 要照什麼結構寫，轉接器才讀得出來。

| 檔 | 做什麼 | 誰用 |
|---|---|---|
| `callback_plugins/verify_records.py` | 把驗證步驟寫成紀錄（V3、V5、V6） | 每次執行 playbook |
| `filter_plugins/connection_links.py` | `links` 展開成 ufw 規則、欄位檢查、埠代入 | 負責放行的 playbook（N4）；轉接器 |
| `ansible_facts.py` | 轉接器：讀本檔規定的位置，寫出 `部署事實.json` | 項目產驗收文件前跑 |
| `tests/` | 上面三支的測試，假項目在 `tests/project/` | 本 repo |

整條路：項目照本檔寫 ansible → 跑 playbook，callback 留下驗證紀錄 → 轉接器讀 ansible 與紀錄，寫 `部署事實.json` → 驗收文件 repo 讀它組表。

轉接器只交事實。表格列怎麼排、版本怎麼接、用語正規化、句子怎麼寫，都是驗收文件那邊的事，不在這裡。

每條規則的「填哪個欄位」指 `部署事實.json` 的欄位路徑。「違反時」標明會不會有錯誤訊息；**標「無錯誤訊息」的，轉接器照樣寫出 json，錯只有讀文件的人看得到**。

`generated_at` 寫轉接器執行的時間。

## 執行環境

轉接器在使用者的 Mac 上跑（項目的 pre-commit 會呼叫），不連跳板與任何主機。它要 PyYAML 與 `ansible-inventory`，用跳板同版的 ansible-core 帶的 Python 跑，兩樣都有，不另裝：

```sh
# 在項目根目錄
mise x uv@0.13.9 -- uvx --from ansible-core==2.22.0 python ansible/vendor/infra-kit/ansible-facts/ansible_facts.py \
    --ansible-dir ansible -o 部署事實.json
```

- ansible-core 的版本要與執行 playbook 的跳板相同（目前 2.22.0）：inventory 由 `ansible-inventory --list` 解析，版本不同時群組與變數的疊法可能不同
- ansible-core 2.22 要 Python 3.13 以上，uvx 會自己挑合適的版本。Mac 的系統 python3（3.9）裝不了 ansible-core 2.22、也沒有 PyYAML，不能直接跑
- `ansible-inventory` 用同一個 Python 環境的那支；環境變數 `ANSIBLE_INVENTORY_CMD`（空白分隔）可換
- `--results` 換驗證紀錄的目錄，預設 `<ansible-dir>/logs/results`；給了而不是目錄時結束碼 2（多半是路徑打錯）。紀錄不進版控，另開的 worktree 沒有紀錄：每一項都寫 `null` 與「沒有正式執行的紀錄」，轉接器在 stderr 提醒，結束碼照樣是 0

| 結束碼 | 意思 |
|---|---|
| 0 | 寫出 `部署事實.json`。挑不到結果的寫在 `problem` 與 `problems`，由讀的一方決定怎麼處理 |
| 1 | 違反有錯誤訊息的規則，每項一行「錯：<代號> <哪裡>：<說明>」，不寫 json |
| 2 | 參數或環境錯（`--ansible-dir` 或 `--results` 不是目錄、找不到 `ansible-inventory`） |

## 測試

```sh
# 在本 repo 根目錄
mise x uv@0.13.9 -- uvx --from ansible-core==2.22.0 --with jsonschema==4.26.0 \
    python -m unittest discover -s ansible-facts/tests -t ansible-facts/tests
```

- 要的 Python 與相依：Python 3.13 以上，裝 `ansible-core==2.22.0`（帶 PyYAML 與 `ansible-inventory`、`ansible-playbook`）與 `jsonschema==4.26.0`。上面的 uvx 指令一次備齊；用系統 python3 直接跑會 `ModuleNotFoundError: yaml`。CI 用 setup-python 3.13 加 `pip install ansible-core==2.22.0 jsonschema==4.26.0`
- `test_ansible_facts.py`：假項目照規則寫的輸出、紀錄挑不到結果的情形，與每條有錯誤訊息的規則一份會紅的變體（`CASES`；另一條測試對照本檔的規則表，漏寫就紅）
- 輸出用 `tests/acceptance-docs/` 的 `check_facts.py` 與 schema 驗。那是驗收文件 repo 的複本，來源與更新方式見那個目錄的 README
- `test_verify_records.py`：對 localhost 真的跑 `ansible-playbook`，`tests/callback/ansible.cfg` 照下一節的寫法啟用 callback
- 假項目只用保留位址（192.0.2.0/24、198.51.100.0/24）與假主機名

## 項目怎麼引用

git submodule 掛在 `ansible/vendor/infra-kit`，指到 tag，不跟 main。掛在 `ansible/` 底下：執行 ansible 的機器可能只看得到 `ansible/` 這個目錄。

`ansible.cfg`：submodule 的路徑接在原本的值後面（路徑用 `:` 接，`callbacks_enabled` 用 `,` 接），不要蓋掉項目自己的 plugin。只有這兩樣時：

```ini
[defaults]
callback_plugins = ./vendor/infra-kit/ansible-facts/callback_plugins
callbacks_enabled = verify_records
filter_plugins = ./vendor/infra-kit/ansible-facts/filter_plugins
```

### 放行的 playbook 怎麼用 filter

負責放行的 playbook（N4）在 play 的 `vars` 寫 `ufw_writer: <它自己的檔名>`，算規則的任務照下面寫。`project.yaml` 用 lookup 讀（L3），不整份載入：

```yaml
- name: 算這台的 ufw 放行規則
  ansible.builtin.set_fact:
    ufw_link_rules: >-
      {{ links_doc | ufw_link_rules(inventory_hostname, groups, hostvars, ufw_writer,
         dict(link_ports | zip(query('vars', *link_ports)))) }}
  vars:
    links_doc: "{{ lookup('ansible.builtin.file', playbook_dir ~ '/vars/project.yaml') | from_yaml }}"
    link_ports: "{{ links_doc | connection_port_names }}"
```

| filter | 參數 | 回傳 |
|---|---|---|
| `connection_port_names` | `project.yaml` 讀進來的 dict | `links` 用到的埠變數名清單，照出現順序 |
| `ufw_link_rules` | 同上的 dict、這台的主機名、`groups`、`hostvars`、`ufw_writer`、`{埠變數名: 值}` | 這台要放行的規則清單，每項 `{link, from_host, from_ip, port, protocol, comment, cmd}`；`cmd` 是完整的 `ufw allow …` 指令。`links` 欄位錯、群組不存在、埠變數沒定義時失敗 |

放行就是逐項跑 `cmd`。`ufw` 只加規則、不刪，指令與 comment 跟主機上現有的一字不差，重跑才不會疊出新規則。

| 代號 | 規定 | 違反時 |
|---|---|---|
| S1 | callback 與 filter 只從 submodule 取，`ansible/callback_plugins/`、`ansible/filter_plugins/` 不放這兩支的副本 | 留著副本時 ansible 可能載到舊的那份，防火牆放行與 `部署事實.json` 的 `links` 照不同規則展開，無錯誤訊息 |
| S2 | 轉接器用同一個 submodule 的版本跑，不另裝一份 | 同上 |

## 一、位置

| 代號 | 規定 | 填哪個欄位 | 違反時 |
|---|---|---|---|
| L1 | ansible 放在 `<項目根>/ansible/`，`ansible.cfg` 與 playbook 都在這一層；設定裡的相對路徑都相對 `ansible/`。playbook 放子目錄時，紀錄只記檔名、寫在那個子目錄的 `logs/`，對不到 `project.yaml` 寫的路徑，每一項都是「沒有正式執行的紀錄」，無錯誤訊息 | 全部 | 找不到檔案，轉接器停，有錯誤訊息 |
| L2 | 項目資料檔是 `ansible/vars/project.yaml`，檔名每個項目都一樣，最上層是 mapping | `components`、`links`、`evidence`、`groups[].label` | 有錯誤訊息 |
| L3 | playbook 只用 `lookup('ansible.builtin.file', …) \| from_yaml` 讀 `project.yaml` 再取要的節，不用 `vars_files` 或 `include_vars` 整份載入 | 不填；守 ansible 執行 | 整份載入時各節變成 play 變數，蓋掉同名變數，無錯誤訊息 |

## 二、inventory

| 代號 | 規定 | 填哪個欄位 | 違反時 |
|---|---|---|---|
| I1 | `project.yaml` 的 `inventory` 列出要帶的 inventory 來源，每項是相對 `ansible/` 的目錄，順序就是疊的順序 | `hosts`（順序）、`groups` | 沒有 `inventory` 鍵：轉接器停，有錯誤訊息。少列一份：引用它群組的元件與連線有錯誤訊息 |
| I2 | 每份來源是一個目錄，裡面有 `hosts.yml`，YAML 格式（`all:` → `children:` → 群組 → `hosts:`）。不用 INI，不用動態 inventory plugin。轉接器輸出所有有成員的群組 | `hosts`、`groups` | 讀不到，有錯誤訊息 |
| I3 | 主機在 `ansible_host` 寫字面的 IPv4，不寫運算式。轉接器輸出所有寫了 `ansible_host` 的主機，不只進表的：驗證結果的文字可能提到不進表的主機 | `hosts[]` | 寫運算式：schema 檢查擋下，有錯誤訊息。沒有 `ansible_host` 的主機（例：本機連線的 `localhost`）不輸出，也不算群組成員。被 `components` 或 `links` 引用的群組因此變空時有錯誤訊息；沒被引用的空群組不輸出；只少幾台時，那幾台不進表，無錯誤訊息 |
| I4 | `hosts` 的順序照 inventory：來源照 I1 的順序，來源內照群組與主機在檔裡的順序 | `hosts` 的順序 | 不是違反，是規則。改了順序，表的列順序跟著變 |
| I5 | 一群主機裡有特定角色的（例：資料庫的主庫、從庫、快取所在的那台），每個角色用一個 inventory 群組宣告，playbook 用 `groups['<角色群組>']` 取主機，不在 playbook 用運算式從別的群組推算 | `groups`、`components[].instances`、`links[].to` | 轉接器只讀群組，不解析 playbook 的運算式。用運算式推算的角色，`components` 與 `links` 沒有寫法能指到它；角色的參數用 `-e` 蓋過時，**表上主從標反，無錯誤訊息** |
| I6 | 埠變數（`links` 的 `port`、`verify` 的 `service` 裡的 `{變數}`）寫在某一份來源的 `group_vars/all.yml`，整個 inventory 只定義一處 | `links[].ports`、`links[].checks[].service` | 找不到：有錯誤訊息。兩份來源都定義：ansible 用後疊的那份，轉接器可能讀到另一份，文件與防火牆的埠不同，無錯誤訊息（轉接器要擋） |
| I7 | `ansible/` 底下受版控的檔，I1 的來源目錄以外，不出現 inventory 主機的 IP | 不填；守「IP 只寫在 inventory」 | 有錯誤訊息，點名檔案與行號。沒擋的話 IP 改了之後那份檔還是舊的，無錯誤訊息 |

## 三、project.yaml：group_labels

| 代號 | 規定 | 填哪個欄位 | 違反時 |
|---|---|---|---|
| G1 | `group_labels` 給群組的中文名稱，鍵是群組名，另有 `any` 給不限來源。`links` 兩端用到的群組都要有（本機服務的來源也要） | `groups[].label`、`links[].from[].label`（`any`） | 缺名稱：有錯誤訊息 |

## 四、project.yaml：components

| 代號 | 規定 | 填哪個欄位 | 違反時 |
|---|---|---|---|
| C1 | 欄位只有 `id`、`name`、`plan_name`、`type`、`hosts`、`version_from`、`verify`、`table`、`evidence`（E7）。`id`、`name`、`type`、`version_from` 必填，`id` 不重複 | `components[]` 的 `id`、`name`、`plan_name`、`type` | 有錯誤訊息 |
| C2 | `type` 是 `application`、`container`、`library`、`operating-system`、`platform`、`framework` 之一 | `components[].type` | 有錯誤訊息 |
| C3 | `table: false` 的元件不寫 `hosts`、`verify`、`plan_name`、`evidence`，`version_from` 要是鎖定處。其他元件 `hosts`、`verify` 必填 | `components[].in_table`、`versions` | 有錯誤訊息 |
| C4 | `hosts` 寫成 `group:<群組>`（多組用逗號接）或 `play:<playbook>`。`play:` 取那支 playbook 的 play 的 `hosts:` | `components[].instances[].host` | 寫法不認得、群組不在 inventory 或沒有主機：有錯誤訊息 |
| C5 | `play:` 指到的 playbook 只有一個 play（`import_playbook` 不算），play 寫成 `- name: …` 開頭、下一層兩格縮排的 `  hosts: …`（`- hosts:` 開頭的寫法認不得），`hosts:` 是群組名用 `:` 或 `,` 接，不含運算式、`!`、`&`。主機由 `-e` 參數決定的 playbook 不能用 `play:`，改寫 `group:` | 同上 | 有錯誤訊息 |
| C6 | `version_from` 是 `record`、一個鎖定處或鎖定處的清單 | `components[].versions`（鎖定處）、`instances[].versions`（`record`） | 有錯誤訊息 |
| C7 | 鎖定處的欄位只有 `id`、`file`、`var`、`parent`、`list`、`item`、`format`、`label`；`id`、`file`、`var`、`format` 必填；`list` 與 `item` 一起寫，且不和 `parent` 同時寫；同一個 `id` 出現兩次時 `label` 以外要一字不差（用 YAML 錨點） | `versions[].lock`、`label` | 有錯誤訊息 |
| C8 | 鎖定處在 `file`（相對 `ansible/`）裡剛好一處，寫成單行的 `var: 值`。有 `parent` 時只在 `parent:` 區塊內找；有 `list`、`item` 時在頂層 `list:` 清單裡 `name: <item>` 那一項找 `var` | `versions[].full` | 找不到或不只一處：有錯誤訊息 |
| C9 | `format` 是 `deb`（`[epoch:]上游[+ds\|+dfsg]-修訂`）、`image`（`名稱:v1.2.3` 或 `名稱:1.2.3`）、`plain`（`1.2.3`）之一 | `versions[].version`（上游版本） | 值的格式不認得：有錯誤訊息 |
| C10 | 版本只寫在鎖定處，`project.yaml` 不寫版本號 | 不填 | 寫在 `project.yaml` 的版本號轉接器不讀，跟鎖定處不同時無錯誤訊息 |
| C11 | `version_from: record` 的元件，它的驗證步驟要有 `report_version`（V3） | `instances[].versions` | 那台沒有版本：不寫 `versions`，`problem` 寫原因 |
| C12 | `verify` 寫 `{playbook, step}`。`step` 是紀錄上的名稱，要在那支 playbook 或它 include、import 的檔（含 role 的 tasks）找得到。步驟名含 `{{ 變數 }}` 時，`step` 寫代入後的名稱；這種步驟名在 `{{ }}` 前後都要有固定文字 | `instances[].check` | 找不到：有錯誤訊息。`{{ }}` 在名稱開頭或結尾的步驟不放寬，要寫原文（含 `{{ }}` 的名稱紀錄上對不到，等於找不到） |
| C13 | 沒有兩筆元件的 `hosts` 與 `verify` 都一樣 | 同上 | 有錯誤訊息（通常是角色寫重了） |
| C14 | `components` 的順序就是輸出的順序 | `components` 的順序 | 不是違反，是規則 |

## 五、project.yaml：links

`links` 一條是一個連線關係。ufw 白名單與 `部署事實.json` 的 `links` 都從這裡展開。

| 代號 | 規定 | 填哪個欄位 | 違反時 |
|---|---|---|---|
| N1 | 欄位只有 `id`、`from`、`to`、`port`、`protocol`、`purpose`、`firewall`、`ufw_comment`、`verify`。`id`、`from`、`to`、`port`、`protocol`、`purpose` 必填，`id` 不重複，`protocol` 是 `tcp` 或 `udp` | `links[]` 的 `id`、`protocol`、`purpose` | 有錯誤訊息 |
| N2 | `from` 是群組名或群組名清單，或 `any`（不能再列群組）。`to` 是一個群組名，或 `self`（本機服務） | `links[].from`、`to` | 群組不在 inventory 或沒有主機：有錯誤訊息 |
| N3 | `port` 是 I6 的變數名或變數名清單；只有 `to: self` 可以寫數字 | `links[].ports`（代入後的值） | 有錯誤訊息 |
| N4 | `firewall` 寫負責放行的 playbook（相對 `ansible/`），它或它 include 的檔要有 `ufw_writer: <它自己的檔名>`。有 `firewall` 就要有 `ufw_comment`，沒有就不寫；`to: self` 不寫 `firewall`。`ufw_comment` 裡 `{ip}`、`{host}` 代來源主機的 IP 與名稱 | `links[].firewalled` | 有錯誤訊息。`ufw_comment` 跟主機上現有規則不同時，重跑會疊出新規則，無錯誤訊息 |
| N5 | `verify` 是清單，每項欄位只有 `service`、`hosts`、`playbook`、`step`、`note`，前四個必填。`hosts` 寫法同 C4、C5。`step` 要在那支 playbook 或它 include 的檔找得到原文一樣的步驟，名稱含 `{{ }}` 的步驟不能用 | `links[].checks[]` | 有錯誤訊息 |
| N6 | `verify` 的 `service` 裡 `{變數}` 代入 I6 的值 | `checks[].service` | 變數找不到：有錯誤訊息 |
| N7 | 同一個 `service` 與 `hosts` 只列一次 | `checks[]` | 有錯誤訊息 |
| N8 | `links` 的順序與每條 `verify` 的順序就是輸出的順序；ufw 照 `links` 的順序放行 | `links`、`checks` 的順序 | 不是違反，是規則 |

## 六、驗證步驟與紀錄

| 代號 | 規定 | 填哪個欄位 | 違反時 |
|---|---|---|---|
| V1 | 要進報告的檢查寫成名稱以「驗證：」開頭的步驟，只有這種步驟會留紀錄 | `check` | 沒有紀錄：`check` 是 `null`，`problem` 寫「沒有正式執行的紀錄」 |
| V2 | `components` 與 `links` 引用的步驟，在同一次執行的同一台主機只出現一次 | `check` | role 內的步驟名不帶 role 前綴，同名兩步分不出來：`check` 是 `null`，`problem` 寫原因 |
| V3 | 驗證步驟的 `vars` 可寫六個鍵。`report_service`；`report_method`，`links` 引用的步驟必寫；`report_version`，從主機或容器讀到的版本，不寫鎖定的變數；`report_hosts`，這步適用哪些主機的名稱清單，`when:` 引用同一個變數；`report_command`，給人看的指令原文（含 `{{ }}` 的寫成 `!unsafe >-` 區塊，`\t` 照原文留著）；`report_output`，前面步驟 register 的 `stdout`，超過 200 行時只記前 200 行、末行註明截斷 | `check.service`、`check.method`、`instances[].versions`、`evidence[].command`、`evidence[].output` | `links` 引用的步驟沒有 `report_method`：`problem`。代入失敗：紀錄寫 `report_error`，`problem`（連線的那筆 `check` 寫 `null`；元件的照記結果） |
| V4 | `report_*` 不放密碼變數。紀錄照樣寫出，`no_log` 也一樣。`report_output` 只給不含密碼的指令（`docker ps`、版本查詢），輸出會原文進驗收文件 | 不填 | 密碼寫進紀錄檔與驗收文件，無錯誤訊息 |
| V5 | `ansible.cfg` 啟用本目錄的 callback（見「項目怎麼引用」）。紀錄寫在 `ansible/logs/results/<playbook>-<時間>.jsonl`，不進版控 | `check` | 沒啟用：沒有紀錄，同 V1 |
| V6 | 紀錄格式由 callback 定，項目不改：每個驗證步驟一行 `{"type": "step", time, playbook, play, host, step, result, check, service?, method?, version?, command?, output?, report_hosts?, report_error?}`，執行結束一行 `{"type": "summary", time, playbook, check, hosts: {主機: {ok, failed, skipped, unreachable}}}` | — | jsonl 有一行不是 JSON：有錯誤訊息（結束碼 1）。其他不認得的行跳過 |
| V7 | 轉接器只採正式執行：不是 `--check`、有總結行、總結沒有 unreachable。每個（playbook、主機）取最近一次正式執行；那次沒有這一步、主機在這步之前就失敗、只出現在更晚的預演、結果是 skipped，都寫 `null` 與 `problem`，不沿用更早的結果。`report_hosts` 不含這台時同樣（元件與連線都是） | `check.result`（ok → `pass`、failed → `fail`）、`check.time` | 不是違反，是規則 |
| V8 | 有正式紀錄而 `components` 沒展開到的主機（例：用 `-e` 多裝了一台） | `problems[]` | 轉接器寫進 `problems` |

## 七、project.yaml：evidence

驗收文件用主機上標準指令（例：`docker ps`）的輸出當舉證。`evidence` 一項是一個指令，每台主機一筆；元件可以指一項，轉接器拿那台的輸出對照元件的結果。

| 代號 | 規定 | 填哪個欄位 | 違反時 |
|---|---|---|---|
| E1 | `evidence` 是清單，每項欄位只有 `id`、`title`、`hosts`、`playbook`、`step`，都是字串、都必填，`id` 不重複 | `evidence[]` 的 `id`、`title` | 有錯誤訊息 |
| E2 | `hosts` 寫法同 C4、C5 | `evidence[].host` | 有錯誤訊息 |
| E3 | `step` 規則同 C12：要在那支 playbook 或它 include、import 的檔（含 role 的 tasks）找得到；同一次執行的同一台只出現一次（V2） | `evidence[]` | 找不到：有錯誤訊息 |
| E4 | `title` 可含 `{host}`，代入主機名 | `evidence[].title` | 不是違反，是規則 |
| E5 | 那一步要帶 `report_command` 與 `report_output`（V3）。每台的取法同 V7：最近一次正式執行，skipped、`report_hosts` 不含這台、代入失敗都不採；`evidence` 那步與元件那步各取各的最近一次，不要求同一次 | `evidence[]` 的 `command`、`output`、`time` | 沒採到或紀錄沒有 `command`、`output` 的主機不輸出那筆，轉接器寫進 `problems`，點名 `id` 與主機 |
| E6 | 順序照 `evidence` 清單，同一項內照 `hosts` 展開的順序。`project.yaml` 沒有 `evidence` 時不輸出這個鍵 | `evidence` 的順序 | 不是違反，是規則 |
| E7 | `components[]` 可寫 `evidence: {id, line}`，兩個都是字串：`id` 是 `evidence` 裡的一項，`line` 是正規式，可含 `{version}`。含 `{version}` 的元件，`version_from` 是 `record` 或只有一個鎖定處 | 不填；給 E8 對照 | 有錯誤訊息 |
| E8 | 對照：`check.result` 是 `pass` 的實例，同主機那筆 `evidence[id]` 的 `output` 要有一行含符合 `line` 的片段（逐行 `re.search`）。`{version}` 代入該實例的版本（`record` 取紀錄的版本，鎖定處取上游版本），代入前先跳脫。`check` 是 `null` 或 `fail` 的不對照 | `problems[]` | 找不到那筆、沒有版本可代入、沒有一行符合（輸出超過 200 行、符合的那行被截掉也算）：轉接器寫進 `problems`，點名元件、主機、`id`、`line`。`line` 寫得太寬（例：只寫 `Up`）時對照永遠綠，無錯誤訊息 |

## 八、參考項目改過的地方

參考項目原本自己維護產生腳本，與本規格的差異如下。改用本目錄時照「改成」那欄改：

| 規則 | 原本 | 改成 |
|---|---|---|
| I1 | inventory 來源與順序寫在項目的產生腳本裡，`project.yaml` 沒有 `inventory` | `project.yaml` 加 `inventory`，照現在腳本裡的順序列 |
| I5 | 資料庫的主庫、從庫、快取主機由資料庫 playbook 的運算式從資料庫群組推算（第一台、其餘那台），不是寫在 inventory | inventory 加三個角色群組；資料庫 playbook 改用 `groups['<角色群組>']`；`components` 與 `links` 的 `db:<角色>` 改成 `group:<角色群組>`，`group_labels` 的鍵跟著改。主從切換改 inventory 的群組成員 |
| C4、N5 | 一個元件與一項連線驗證的主機寫成 `targets:<playbook>`，由 playbook 前置斷言允許的 `-e target` 清單推算 | inventory 加一個群組宣告這個服務裝在哪些主機，playbook 的 `hosts:` 用它（或 `-e target` 與它取交集）；`hosts` 改寫 `group:` 或 `play:` |
| S1 | callback 與 filter 是項目自己的副本，放在 `ansible/callback_plugins/`、`ansible/filter_plugins/` | 刪掉副本，掛 submodule 到 `ansible/vendor/infra-kit`，`ansible.cfg` 改指那裡 |

取捨的依據：

- **I1**：不改的代價是來源清單寫死在轉接器裡，換一個 inventory 拆法不同的項目就要改轉接器。來源順序決定 `hosts` 順序，轉接器自己排的話表上列順序會變，無錯誤訊息。不用「`inventory/` 底下全部子目錄」：子目錄名的排序不一定是疊的順序
- **I5**：不改的代價是轉接器要寫死一支 playbook 的檔名、變數名與運算式原文，還要求資料庫群組剛好兩台，別的項目拓撲不同就不能用。運算式的參數可以用 `-e` 蓋過，切換後表上主從標反，無錯誤訊息
- **`targets:`**：讀前置斷言是解析 playbook 的條件判斷。不改的代價是轉接器要認一種斷言的字面寫法，斷言換個寫法就讀不到
- **S1**：不改的代價是每個項目一份副本。連線展開同時決定防火牆放行與文件內容，兩邊用到不同版本時文件與實際放行不一致，無錯誤訊息
- 其他規則照參考項目現行做法：它已照這些寫法在用，舉不出改的理由

## 九、不在本規格的

參考項目還有幾項檢查讀 ansible，屬於該項目自己的部署寫法，留在項目裡，轉接器不做：

- 安裝 Docker 的 playbook 預設 target 要涵蓋 Docker 元件的主機，且每個容器元件的主機都裝了 Docker
- 正式部署 playbook 每個服務的步驟名代入的服務名稱，要在對應群組的 play 裡剛好一處
- 項目的安裝版本清單每一列的版本等於鎖定處：版本從 `部署事實.json` 的 `versions[].full` 與 `lock` 讀，表格欄名與元件的對照是項目文件的寫法
