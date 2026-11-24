# nginx：官方 ngx_otel_module 片段

給能連外安裝的環境用。入口 nginx 為每個請求開一個 span，並把 `traceparent` 帶給上游，應用的 span 接在它下面。

## 映像

用含模組的 nginx 官方映像 `-otel` 變體：

```
nginx:1.30.6-alpine-otel
```

Docker Hub 的官方 nginx 映像有 `<版本>-otel`、`<版本>-alpine-otel` 等變體，模組在 `modules/ngx_otel_module.so`。CI 在這個映像裡跑 `nginx -t`（`test/run.sh`）。

## 接法

1. `nginx.conf` 頂端（main 層，任何 `events`、`http` 區塊之外）加：

   ```nginx
   load_module modules/ngx_otel_module.so;
   ```

   `load_module` 只能放 main 層，本 repo 的片段在 http 層 include，放不進去。

2. `http { }` 裡 include 片段，接著寫服務名：

   ```nginx
   http {
       include /etc/nginx/otel/otel.conf;
       otel_service_name <項目>-nginx;
       ...
   }
   ```

3. compose 把片段掛進去：

   ```yaml
   volumes:
     - ./vendor/infra-kit/otel/nginx/otel.conf:/etc/nginx/otel/otel.conf:ro
   ```

片段內容：`otel_exporter` 指 `otel-collector:4317`（Collector 的 gRPC，compose 服務名照 `collector/compose.example.yaml`）、`otel_trace on`、`otel_trace_context propagate`。

## 行為（在 `nginx:1.30.6-alpine-otel` 實測）

- 請求帶 `traceparent`：上游收到同一個 trace id、新的 span id，旗標 `01`
- 沒帶：nginx 新起一個 trace，上游收到旗標 `01` 的 `traceparent`

## 要注意的

- 改設定後一定跑 `nginx -t` 再 reload。漏了 `load_module` 時 `nginx -t` 會失敗（`unknown directive "otel_exporter"`）；若沒跑 `nginx -t` 直接 reload，舊設定繼續跑，看起來正常，但沒有任何 span，也不會有錯誤訊息
- `otel_exporter` 的主機名在 `nginx -t` 時不解析，Collector 沒起來 nginx 照樣能起，span 會送不出去
- access log 想帶 trace id 可用變數 `$otel_trace_id`
