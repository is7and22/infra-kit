# nginx：官方 ngx_otel_module 片段

給能連外安裝的環境用。入口 nginx 為每個請求開一個 span，並把 `traceparent` 帶給上游，應用的 span 接在它下面。

## 映像

用含模組的 nginx 官方映像 `-otel` 變體：

```
nginx:1.30.6-alpine-otel
```

Docker Hub 的官方 nginx 映像有 `<版本>-otel`、`<版本>-alpine-otel` 等變體，模組在 `modules/ngx_otel_module.so`。CI 在這個映像裡跑 `test/run.sh`：`nginx -t`、`http.conf` 重複 include 要失敗、傳播實測。

## 兩份片段

| 片段 | 放哪 | 誰 include | 內容 |
|---|---|---|---|
| `http.conf` | `http { }` 裡 | 這台 nginx 的擁有者，**每台只 include 一次** | `otel_exporter`（Collector 位址） |
| `server.conf` | 要追蹤的 `server { }` 裡 | 每個要追蹤的 server 各一次 | `otel_trace on`、`otel_trace_context propagate` |

拆開的原因：`otel_exporter` 一台 nginx 只能有一個，第二次出現 `nginx -t` 會失敗（`"otel_exporter" directive is duplicate`）；`otel_service_name` 也只能放 http 層。追蹤開關放 server 層，沒 include `server.conf` 的 server 不起 span，請求的 `traceparent` 原封不動轉給上游。

`http.conf` 的 Collector 位址是範例值 `otel-collector:4317`（Collector 的 gRPC，compose 服務名照 `collector/compose.example.yaml`）。位址不同時不要 include `http.conf`，在 http 層自己寫同樣的區塊：

```nginx
otel_exporter {
    endpoint <主機>:4317;
}
```

## 接法

先在 `nginx.conf` 頂端（main 層，任何 `events`、`http` 區塊之外）加：

```nginx
load_module modules/ngx_otel_module.so;
```

`load_module` 只能放 main 層，兩份片段都放不進去。

### 一個項目一台 nginx

兩份都 include：

```nginx
http {
    include /etc/nginx/otel/http.conf;
    otel_service_name <項目>-nginx;

    server {
        include /etc/nginx/otel/server.conf;
        ...
    }
}
```

compose 把片段掛進去：

```yaml
volumes:
  - ./vendor/infra-kit/otel/nginx/http.conf:/etc/nginx/otel/http.conf:ro
  - ./vendor/infra-kit/otel/nginx/server.conf:/etc/nginx/otel/server.conf:ro
```

### 多個項目共用一台 nginx（例如本機開發）

http 層由 nginx 的擁有者寫一次，服務名用中性的名字，不用任何一個項目名：

```nginx
http {
    include /etc/nginx/otel/http.conf;
    otel_service_name dev-nginx;
    include /etc/nginx/conf.d/*.conf;
}
```

各項目只在自己的 server include `server.conf`，接著用 `otel_span_attr` 標項目名：

```nginx
server {
    include /etc/nginx/otel/server.conf;
    otel_span_attr project <項目>;
    ...
}
```

項目的設定檔裡不要 include `http.conf`、不要寫 `otel_service_name`：前者 `duplicate`，後者在 server 層 `directive is not allowed here`。共用 nginx 只有一個服務名，所有項目的 nginx span 都是 `dev-nginx`，要分項目查 span 屬性 `project`。沒接的項目不 include `server.conf`，不受影響。

## 行為（在 `nginx:1.30.6-alpine-otel` 實測）

- 有 include `server.conf` 的 server，請求帶 `traceparent`：上游收到同一個 trace id、新的 span id，旗標 `01`
- 同上，沒帶：nginx 新起一個 trace，上游收到旗標 `01` 的 `traceparent`
- 沒 include `server.conf` 的 server：不起 span，上游收到的 `traceparent` 與請求帶的一樣

## 要注意的

- `otel_trace on` 不要放 http 層：所有 server 都會起 span，沒接的項目的 span 也進 OpenObserve，不會有錯誤訊息。只搬這一行而 `otel_trace_context propagate` 留在 server 層時，沒接的 server 轉給上游的 `traceparent` 也不變，從上游看不出來；`test/run.sh` 用檢查 `http.conf` 擋這件事
- 改設定後一定跑 `nginx -t` 再 reload。漏了 `load_module` 時 `nginx -t` 會失敗（`unknown directive "otel_exporter"`）；若沒跑 `nginx -t` 直接 reload，舊設定繼續跑，看起來正常，但沒有任何 span，也不會有錯誤訊息
- `otel_exporter` 的主機名在 `nginx -t` 時不解析，Collector 沒起來 nginx 照樣能起，span 會送不出去
- access log 想帶 trace id 可用變數 `$otel_trace_id`
