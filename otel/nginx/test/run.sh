#!/bin/sh
# 在含 ngx_otel_module 的官方映像裡驗片段：
#   1. 兩個 server 各 include server.conf：nginx -t 要通過
#   2. 拿掉 load_module：nginx -t 要失敗（證明這行必要、片段確實用到模組）
#   3. http.conf include 兩次：nginx -t 要失敗（duplicate），證明一台 nginx 只能 include 一次
#   0. http.conf 不能有 otel_trace／otel_trace_context：只搬 otel_trace on 到 http 層時，
#      沒接的 server 也起 span，但不設 propagate 就不改寫 traceparent，第 4 條驗不出來
#   4. 傳播：帶 traceparent 打有 include server.conf 的 server，上游收到同一 trace id、新 span id；
#      打沒 include 的 server，上游收到的 traceparent 原封不動
set -eu
IMAGE="${NGINX_OTEL_IMAGE:-nginx:1.30.6-alpine-otel}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
NAME="otel-nginx-test-$$"
cleanup() {
    docker rm -f "$NAME" > /dev/null 2>&1 || true
    rm -rf "$TMP"
}
trap cleanup EXIT
if grep -Eq '^[[:space:]]*otel_trace([[:space:]]|_context)' "$HERE/../http.conf"; then
    echo "FAIL：http.conf 不能有 otel_trace／otel_trace_context，會讓所有 server 起 span" >&2
    exit 1
fi
grep -v '^load_module' "$HERE/nginx.conf" > "$TMP/nginx-no-module.conf"
sed 's|^\( *\)include /etc/nginx/otel/http.conf;|&\n\1include /etc/nginx/otel/http.conf;|' \
    "$HERE/nginx.conf" > "$TMP/nginx-dup.conf"
[ "$(grep -c 'otel/http.conf' "$TMP/nginx-dup.conf")" = 2 ] || { echo "FAIL：重複 include 的設定沒產生" >&2; exit 1; }

dock() {  # $1 設定檔，其餘是 docker run 的參數（含映像與指令）
    cfg="$1"
    shift
    docker run \
        -v "$HERE/../http.conf:/etc/nginx/otel/http.conf:ro" \
        -v "$HERE/../server.conf:/etc/nginx/otel/server.conf:ro" \
        -v "$cfg:/etc/nginx/nginx.conf:ro" \
        "$@"
}

run() {
    dock "$1" --rm "$IMAGE" nginx -t
}

expect_fail() {  # $1 說明 $2 設定檔 $3 預期訊息
    echo "== $1（預期失敗）=="
    if run "$2" > "$TMP/out" 2>&1; then
        cat "$TMP/out"
        echo "FAIL：$1 仍通過 nginx -t" >&2
        exit 1
    fi
    cat "$TMP/out"
    if ! grep -q "$3" "$TMP/out"; then
        echo "FAIL：nginx -t 失敗，但訊息不含 $3" >&2
        exit 1
    fi
    echo "OK：$1 時 nginx -t 失敗，如預期"
}

echo "== 兩個 server include server.conf =="
run "$HERE/nginx.conf"

expect_fail "拿掉 load_module" "$TMP/nginx-no-module.conf" 'unknown directive "otel_'
expect_fail "http.conf include 兩次" "$TMP/nginx-dup.conf" 'duplicate'

echo "== 傳播實測 =="
dock "$HERE/nginx.conf" -d --name "$NAME" "$IMAGE" > /dev/null
i=0
until docker exec "$NAME" wget -qO- http://127.0.0.1:9000/ > /dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -lt 30 ] || { docker logs "$NAME"; echo "FAIL：nginx 沒起來" >&2; exit 1; }
    sleep 1
done
TID=0af7651916cd43dd8448eb211c80319c
SID=b7ad6b7169203331
IN="00-$TID-$SID-01"
got() {
    docker exec "$NAME" wget -qO- --header "traceparent: $IN" "http://127.0.0.1:$1/"
}
for port in 8080 8081; do
    out="$(got "$port")"
    echo "送 $IN → :${port}，上游收到 $out"
    case "$out" in
        "00-$TID-$SID-"*) echo "FAIL：:${port} 有 include server.conf，span id 卻沒換（沒起 span）" >&2; exit 1 ;;
        "00-$TID-"[0-9a-f]*) ;;
        *) echo "FAIL：:${port} 上游收到的 trace id 不同或沒有 traceparent" >&2; exit 1 ;;
    esac
done
out="$(got 8082)"
echo "送 $IN → :8082，上游收到 $out"
if [ "$out" != "$IN" ]; then
    echo "FAIL：:8082 沒 include server.conf，traceparent 卻被改了（起了 span）" >&2
    exit 1
fi
echo "OK：有 include 的 server 起新 span，沒 include 的原封不動"
