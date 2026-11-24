#!/bin/sh
# 在含 ngx_otel_module 的官方映像裡驗片段：
#   1. 帶 load_module：nginx -t 要通過
#   2. 拿掉 load_module：nginx -t 要失敗（證明這行必要、片段確實用到模組）
set -eu
IMAGE="${NGINX_OTEL_IMAGE:-nginx:1.30.6-alpine-otel}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
grep -v '^load_module' "$HERE/nginx.conf" > "$TMP/nginx-no-module.conf"

run() {
    docker run --rm \
        -v "$HERE/../otel.conf:/etc/nginx/otel/otel.conf:ro" \
        -v "$1:/etc/nginx/nginx.conf:ro" \
        "$IMAGE" nginx -t
}

echo "== 帶 load_module =="
run "$HERE/nginx.conf"

echo "== 拿掉 load_module（預期失敗）=="
if run "$TMP/nginx-no-module.conf" > "$TMP/out" 2>&1; then
    cat "$TMP/out"
    echo "FAIL：拿掉 load_module 仍通過 nginx -t" >&2
    exit 1
fi
cat "$TMP/out"
if ! grep -q 'unknown directive "otel_' "$TMP/out"; then
    echo "FAIL：nginx -t 失敗，但原因不是缺模組" >&2
    exit 1
fi
echo "OK：拿掉 load_module 時 nginx -t 失敗，如預期"
