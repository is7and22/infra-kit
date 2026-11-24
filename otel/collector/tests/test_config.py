"""Collector 範本的靜態檢查：位址與認證只能從環境變數來，且用 ${env:NAME} 語法。

寫死字串或寫成 $NAME，otelcol validate 都會過，送出時 OpenObserve 回 401，只在
Collector 的 log 看得到。所以另外用這支擋。

跑法（repo 根目錄）：python3 -m unittest discover -s otel/collector/tests
"""
import os
import re
import unittest

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config.yaml")


def _lines():
    with open(CONFIG, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            code = line.split("#", 1)[0].rstrip()
            if code.strip():
                yield n, code


def _value(key):
    for _, code in _lines():
        m = re.match(rf"^\s*{re.escape(key)}:\s*(.*)$", code)
        if m:
            return m.group(1)
    raise AssertionError(f"config.yaml 找不到 {key}")


class CollectorConfigTest(unittest.TestCase):
    def test_auth_header_only_from_env(self):
        self.assertEqual(_value("Authorization"), "${env:OPENOBSERVE_AUTH}")

    def test_endpoint_only_from_env(self):
        exporter_endpoints = [c for _, c in _lines() if "OPENOBSERVE_OTLP_ENDPOINT" in c]
        self.assertEqual(len(exporter_endpoints), 1)
        self.assertEqual(exporter_endpoints[0].strip(), "endpoint: ${env:OPENOBSERVE_OTLP_ENDPOINT}")

    def test_every_dollar_uses_env_syntax(self):
        bad = [(n, c) for n, c in _lines()
               if re.sub(r"\$\{env:[A-Z0-9_]+(:-[^}]*)?\}", "", c).count("$")]
        self.assertEqual(bad, [], "環境變數要寫成 ${env:NAME}")


if __name__ == "__main__":
    unittest.main()
