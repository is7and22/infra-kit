#!/usr/bin/env python3
"""串接檢查：到 OpenObserve 查一個 trace id，確認期望的服務都留下 span 與 log。

只用標準函式庫。可以 import 成函式（check_trace），也可以當命令列跑：

    OPENOBSERVE_OTLP_ENDPOINT=https://o2.example.org/api/<組織> \\
    OPENOBSERVE_AUTH="Basic <base64(帳號:密碼)>" \\
    python3 check_trace.py --trace-id <32 位 hex> \\
        --span-service <項目>-nginx --span-service <項目>-api \\
        --log-service <項目>-api --timeout 60

結束碼：0 全部到齊；1 逾時仍有缺（列出缺哪個服務的 span 或 log）；2 參數或連線錯誤。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_IDENT = re.compile(r"^[A-Za-z0-9_]+$")


class CheckError(Exception):
    """參數錯、認證錯、OpenObserve 回非 2xx：重試也不會好，不必等到逾時。"""


@dataclass
class Result:
    missing_spans: list[str] = field(default_factory=list)
    missing_logs: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_spans and not self.missing_logs

    def describe(self) -> str:
        lines = []
        if self.missing_spans:
            lines.append("缺 span 的服務：" + ", ".join(self.missing_spans))
        if self.missing_logs:
            lines.append("缺 log 的服務：" + ", ".join(self.missing_logs))
        return "\n".join(lines) or "全部到齊"


def _missing(expected: list[str], found: set[str]) -> list[str]:
    # 每一個期望的服務都要出現；少一個就算缺
    return [s for s in expected if s not in found]


def _services(api: str, auth: str, search_type: str, stream: str, trace_id: str,
              service_field: str, trace_id_field: str, lookback: int) -> set[str]:
    sql = (f'SELECT {service_field}, COUNT(*) AS n FROM "{stream}" '
           f"WHERE {trace_id_field} = '{trace_id}' GROUP BY {service_field}")
    now_us = int(time.time() * 1_000_000)
    body = {"query": {"sql": sql,
                      "start_time": now_us - lookback * 1_000_000,
                      "end_time": now_us + 60 * 1_000_000,
                      "from": 0, "size": 1000}}
    req = urllib.request.Request(
        f"{api.rstrip('/')}/_search?type={search_type}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": auth},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 404 and "stream" in detail.lower():
            # stream 還沒有任何資料：當作還沒到，繼續等。回應格式待 faninsights 驗證
            return set()
        raise CheckError(f"OpenObserve 查 {search_type} 回 {e.code}：{detail}") from e
    except urllib.error.URLError as e:
        raise CheckError(f"連不到 OpenObserve（{api}）：{e.reason}") from e
    except (OSError, ValueError) as e:  # 讀回應逾時、連線重設、回應不是 JSON
        raise CheckError(f"查 OpenObserve 失敗（{api}）：{e!r}") from e
    return {str(h[service_field]) for h in payload.get("hits", []) if h.get(service_field)}


def check_trace(api: str, auth: str, trace_id: str, span_services: list[str],
                log_services: list[str], timeout: float = 60, interval: float = 2,
                trace_stream: str = "default", log_stream: str = "default",
                service_field: str = "service_name", trace_id_field: str = "trace_id",
                lookback: int = 3600) -> Result:
    """輪詢到全部到齊或逾時。回傳 Result；參數或連線錯誤丟 CheckError。

    api：OpenObserve 的 /api/<組織> 位址，與 Collector 的 OPENOBSERVE_OTLP_ENDPOINT 同一個值。
    auth：完整的 Authorization 標頭值（例 "Basic ..."）。
    """
    trace_id = trace_id.lower()
    if not _TRACE_ID.match(trace_id):
        raise CheckError(f"trace id 要是 32 位 hex：{trace_id!r}")
    for name in (trace_stream, log_stream, service_field, trace_id_field):
        if not _IDENT.match(name):
            raise CheckError(f"stream 或欄位名只能有英數與底線：{name!r}")
    if not auth:
        raise CheckError("沒有認證：設定 OPENOBSERVE_AUTH")
    if not span_services and not log_services:
        raise CheckError("至少要期望一個服務的 span 或 log")

    deadline = time.monotonic() + timeout
    while True:
        result = Result()
        if span_services:
            found = _services(api, auth, "traces", trace_stream, trace_id,
                              service_field, trace_id_field, lookback)
            result.missing_spans = _missing(span_services, found)
        if log_services:
            found = _services(api, auth, "logs", log_stream, trace_id,
                              service_field, trace_id_field, lookback)
            result.missing_logs = _missing(log_services, found)
        if result.ok or time.monotonic() >= deadline:
            return result
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))


def basic_auth(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--url", default=os.environ.get("OPENOBSERVE_OTLP_ENDPOINT", ""),
                   help="OpenObserve 的 /api/<組織> 位址；預設讀 OPENOBSERVE_OTLP_ENDPOINT")
    p.add_argument("--trace-id", required=True)
    p.add_argument("--span-service", action="append", default=[], metavar="SERVICE",
                   help="期望出現 span 的 service.name，可重複")
    p.add_argument("--log-service", action="append", default=[], metavar="SERVICE",
                   help="期望出現帶該 trace id 的 log 的 service.name，可重複")
    p.add_argument("--timeout", type=float, default=60, help="等待上限秒數")
    p.add_argument("--interval", type=float, default=2)
    p.add_argument("--trace-stream", default=os.environ.get("OPENOBSERVE_STREAM", "default"))
    p.add_argument("--log-stream", default=os.environ.get("OPENOBSERVE_STREAM", "default"))
    p.add_argument("--lookback", type=int, default=3600, help="往回查幾秒")
    a = p.parse_args(argv)
    if not a.url:
        print("沒有 OpenObserve 位址：給 --url 或設定 OPENOBSERVE_OTLP_ENDPOINT", file=sys.stderr)
        return 2
    try:
        r = check_trace(a.url, os.environ.get("OPENOBSERVE_AUTH", ""), a.trace_id,
                        a.span_service, a.log_service, timeout=a.timeout,
                        interval=a.interval, trace_stream=a.trace_stream,
                        log_stream=a.log_stream, lookback=a.lookback)
    except CheckError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2
    if r.ok:
        print(f"trace {a.trace_id}：全部到齊")
        return 0
    print(f"trace {a.trace_id}：逾時 {a.timeout:g} 秒仍有缺\n{r.describe()}", file=sys.stderr)
    if set(r.missing_spans) == set(a.span_service) and set(r.missing_logs) == set(a.log_service):
        print("所有服務都缺：先查位址、組織名與 stream 名", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
