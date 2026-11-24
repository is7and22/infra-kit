# otel：一個請求在 OpenObserve 串成一條 trace

應用不寫初始化程式。各語言用 OpenTelemetry 零程式注入的啟動器，以 `OTEL_*` 環境變數設定；每台主機（本機開發則每個 compose）一個 Collector，應用與 nginx 只送本機 Collector，由它轉送 OpenObserve。

| 目錄 | 內容 |
|---|---|
| `collector/` | Collector 設定範本與 compose 服務範例 |
| `nginx/` | 入口 nginx 的官方 `ngx_otel_module` 片段 |
| `python/` | Python 零程式注入的包法 |
| `check/` | `check_trace.py`：到 OpenObserve 查一個 trace id，確認期望的服務都有 span 與 log |

整條路：項目以 submodule 取本 repo 的 tag → compose 用 `collector/` 起 Collector、入口 nginx include `nginx/` 的片段 → 應用照 `python/` 啟動 → 項目的整合測試呼叫 `check/check_trace.py` 到 OpenObserve 查。

## 項目怎麼引用

git submodule 掛在 `vendor/infra-kit`，指到 tag，不跟 main：

```sh
git submodule add https://github.com/is7and22/infra-kit vendor/infra-kit
git -C vendor/infra-kit checkout v0.1.0
git add vendor/infra-kit && git commit -m "infra-kit v0.1.0"
```

升版：`git -C vendor/infra-kit fetch --tags && git -C vendor/infra-kit checkout <新 tag>`，再提交 submodule 指標。

## 約定（項目照填）

### 服務名

`OTEL_SERVICE_NAME` = `<項目>-<服務>`，例 `<項目>-api`、`<項目>-worker`、`<項目>-nginx`。同一個映像跑不同角色（web 與 worker）各取一個名字。

### 資源屬性

`OTEL_RESOURCE_ATTRIBUTES` 帶兩項：

- `service.version`：映像 tag
- `deployment.environment.name`：`dev`、`test`、`prod` 三選一

```
OTEL_RESOURCE_ATTRIBUTES=service.version=1.4.2,deployment.environment.name=prod
```

### 應用端環境變數

| 變數 | 值 |
|---|---|
| `OTEL_SERVICE_NAME` | `<項目>-<服務>` |
| `OTEL_RESOURCE_ATTRIBUTES` | 見上 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 本機 Collector 的 HTTP 埠，compose 裡是 `http://otel-collector:4318` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` |
| `OTEL_TRACES_EXPORTER` | `otlp`（預設值，可不寫） |
| `OTEL_METRICS_EXPORTER` | `none` |
| `OTEL_LOGS_EXPORTER` | `otlp` |
| `OTEL_PYTHON_LOG_CODE_ATTRIBUTES` | `true`（Python 才需要：log 帶上程式碼位置 `code.file.path`、`code.function.name`、`code.line.number`） |

Python 不要設 `OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED`。設成 `true` 時 log 改走 SDK 已標 deprecated 的 `LoggingHandler`，`opentelemetry-instrumentation-logging` 跳過自己的 handler；SDK 拿掉 `LoggingHandler` 的那一版起 log 不再送出，不會有錯誤訊息。不設時由 instrumentation-logging 的 handler 送 log，詳見 [`python/README.md`](python/README.md) 的「log」。

OpenObserve 的位址與認證只給 Collector，應用端不需要也不應該拿到。

### Collector 端環境變數

| 變數 | 值 |
|---|---|
| `OPENOBSERVE_OTLP_ENDPOINT` | `https://<OpenObserve 主機>/api/<組織>`，例 `https://o2.example.org/api/<組織>` |
| `OPENOBSERVE_AUTH` | 完整的 Authorization 標頭值，`Basic <base64(帳號:密碼)>` |
| `OPENOBSERVE_STREAM` | 選填，traces 與 logs 寫進哪個 stream，預設 `default`（traces 是否也照這個標頭分流，待 faninsights 驗證）。`check_trace.py` 的 `--trace-stream`、`--log-stream` 預設也讀它 |

值放項目的 `.env` 或部署機的祕密存放處，不進 git。

## 各元件

- Collector：[`collector/`](collector/)，設定檔 `config.yaml`，compose 範例 `compose.example.yaml`
- nginx：[`nginx/README.md`](nginx/README.md)
- Python：[`python/README.md`](python/README.md)
- 串接檢查：[`check/check_trace.py`](check/check_trace.py)，用法見檔頭

### Collector

映像 `otel/opentelemetry-collector-contrib:0.165.0`。收 OTLP gRPC（4317）與 HTTP（4318），經 `memory_limiter`、`batch`，用 `otlphttp` 送 OpenObserve；traces 與 logs 兩條 pipeline，不收指標。

設定檔裡的環境變數一律寫 `${env:NAME}`。寫成 `$NAME` 不會報錯，送出的是字面字串，OpenObserve 回 401，只有 Collector 的 log 看得到，應用端一切正常。`collector/tests/test_config.py` 擋這件事。

起來之後先看一次 Collector 的 log 沒有 401 或連線錯誤：`docker compose logs otel-collector`。

### 串接檢查

```sh
OPENOBSERVE_OTLP_ENDPOINT=https://o2.example.org/api/<組織> \
OPENOBSERVE_AUTH="Basic <base64(帳號:密碼)>" \
python3 vendor/infra-kit/otel/check/check_trace.py \
  --trace-id <32 位 hex> \
  --span-service <項目>-nginx --span-service <項目>-api \
  --log-service <項目>-api \
  --timeout 60
```

結束碼 0 全部到齊；1 逾時仍有缺，訊息列出缺哪個服務的 span 或 log；2 參數、認證或連線錯誤（不等到逾時）。在 Python 測試裡用 `check_trace.check_trace(...)`，回傳 `Result`（`ok`、`missing_spans`、`missing_logs`）。

查詢用 OpenObserve 的 `_search` API，以 SQL 依 `trace_id` 欄位過濾、依 `service_name` 欄位分組。

- 欄位名：`trace_id`、`service_name` 在 OpenObserve v1.1.2 已在實際項目實測成立。版本不同而對不上時，改 `check_trace()` 的 `service_field`、`trace_id_field` 參數。其他 resource 屬性不一定只是把點換成底線，例如 `service.instance.id` 展開成 `service_service_instance_id`，自己寫 SQL 前先在 OpenObserve 看實際欄位名
- stream 不存在：OpenObserve 回 400，body 是 `{"code":20002,"message":"Search stream not found: <stream>"}`，traces 與 logs 相同。回 400 或 404 且訊息含 `stream not found`（不分大小寫）時當作「還沒到」繼續等；其他 400、404 直接以結束碼 2 結束
- 逾時而所有服務都缺：先查位址與組織名

整合測試要自己產生 trace id 並帶進請求（`traceparent: 00-<trace id>-<16 位 hex>-01`），nginx 會沿用它。
