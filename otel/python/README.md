# Python：零程式注入

應用程式碼不寫任何 OpenTelemetry 初始化。啟動指令前加 `opentelemetry-instrument`，設定全部走環境變數（清單見 `otel/README.md` 的「應用端環境變數」）。

標「待 faninsights 驗證」的是還沒在實際項目跑過的，照做後請回報結果。

## 套件

加進項目的 requirements，釘版本：

```
opentelemetry-distro==0.66b2
opentelemetry-exporter-otlp-proto-http==1.45.2
```

再在裝好項目依賴的環境裡跑：

```sh
opentelemetry-bootstrap -a requirements
```

它依已安裝的套件（Django、Flask、FastAPI、requests、psycopg、redis、celery 等）列出對應的 `opentelemetry-instrumentation-*`。把輸出加進 requirements 並釘成同一版（`0.66b2`）。之後加了新的函式庫就重跑一次。

`opentelemetry-distro` 與各 instrumentation 是 `0.X` 版號，`opentelemetry-sdk`、exporter 是 `1.Y`，兩組要成對升級（0.66b2 對 1.45.2）。

## 啟動

原指令前加 `opentelemetry-instrument`：

```sh
# gunicorn
opentelemetry-instrument gunicorn <模組>.wsgi:application --workers 4 --bind 0.0.0.0:8000

# uvicorn
opentelemetry-instrument uvicorn <模組>.main:app --host 0.0.0.0 --port 8000

# celery worker
opentelemetry-instrument celery -A <模組> worker --loglevel=INFO
```

compose 裡 web 與 worker 共用映像時，各自設 `OTEL_SERVICE_NAME`（`<項目>-api`、`<項目>-worker`）。

## 已知要注意的

### gunicorn 多 worker

- 不必寫 `post_fork` hook：SDK 的批次處理器（`BatchSpanProcessor`、`BatchLogRecordProcessor`）在 fork 後自己重建背景執行緒
- 不開 `preload_app`：開了之後 app 在 master 匯入，instrumentation 的狀態跟著 fork，行為與上面不同，待 faninsights 驗證前不要用
- 指標關掉（`OTEL_METRICS_EXPORTER=none`）：本版不收指標，Collector 也沒有 metrics pipeline

### 框架已內建 OTel 中介層

某一層已經由框架或函式庫內建的 OTel 中介層開 span 時，自動注入的 instrumentation 會再開一個。結果是同一條 trace 裡出現重複的巢狀 span，或另起一棵樹、trace 斷成兩條，看內建中介層怎麼取 context（待 faninsights 驗證）。用環境變數關掉重複的那一層：

```
OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=fastapi
```

值是 instrumentation 的 entry point 名（多半等於套件名去掉 `opentelemetry-instrumentation-`），多個用逗號分隔。

### 沒有 instrumentation 的 HTTP 客戶端

`opentelemetry-bootstrap` 涵蓋 requests、httpx、urllib、urllib3、aiohttp 等。用到它不涵蓋的客戶端呼叫下游時，下游收不到 `traceparent`，下游的 span 會另起一條 trace，不會有錯誤訊息。要在程式裡自己帶：

```python
from opentelemetry import propagate

headers = {}
propagate.inject(headers)  # 加上 traceparent（與 tracestate）
client.post(url, headers=headers, ...)
```

### 待 faninsights 驗證

- uvicorn 帶 `--workers` 大於 1 或 `--reload` 時，子程序是否都有注入
- celery prefork pool 的子程序是否都送出 span，以及 task 的 span 是否接在發出 task 的請求下面
- log 是否帶上 trace id 進到 OpenObserve（`OTEL_LOGS_EXPORTER=otlp` 加 `OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true`），用 `check/check_trace.py --log-service` 驗
