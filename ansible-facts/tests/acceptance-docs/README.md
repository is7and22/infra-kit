# acceptance-docs 的複本

`check_facts.py` 與 `schemas/部署事實.schema.json` 從驗收文件 repo（acceptance-docs）原樣複製，來源 commit `15f37b2`。轉接器的測試用它們驗輸出合乎 `部署事實.json` 的格式，不用另外 clone 那個 repo。

格式改了（那邊的 `format_version` 加一，或 F 規則改了）時，從那邊同一路徑重新複製兩個檔，更新上面的 commit。
