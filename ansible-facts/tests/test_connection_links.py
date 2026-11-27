"""filter_plugins/connection_links.py 的測試：假項目的 links 展開成 ufw 規則（不連主機、不 import ansible）

跑法見 ../README.md「測試」。
"""

import importlib.util
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('connection_links', HERE.parent / 'filter_plugins' / 'connection_links.py')
links = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(links)

DOC = yaml.safe_load((HERE / 'project' / 'ansible' / 'vars' / 'project.yaml').read_text(encoding='utf-8'))
GROUPS = {'web': ['web-1'], 'app': ['app-1', 'app-2'], 'store': ['store-1', 'store-2'], 'store_primary': ['store-1']}
HOSTVARS = {'web-1': {'ansible_host': '192.0.2.11'}, 'app-1': {'ansible_host': '192.0.2.21'},
            'app-2': {'ansible_host': '192.0.2.22'}, 'store-1': {'ansible_host': '192.0.2.31'},
            'store-2': {'ansible_host': '192.0.2.32'}}
PORTS = {'web_port': '80', 'store_port': '7000', 'app_port': '8080'}


class FilterTest(unittest.TestCase):
    def filters(self):
        return links.FilterModule().filters()

    def test_port_names(self):
        self.assertEqual(self.filters()['connection_port_names'](DOC), ['web_port', 'store_port', 'app_port'])

    def test_store_primary_rules(self):
        got = self.filters()['ufw_link_rules'](DOC, 'store-1', GROUPS, HOSTVARS, 'install-store.yml', PORTS)
        # app 兩台連主；複寫只放另一台，不放自己
        self.assertEqual([r['cmd'] for r in got], [
            "ufw allow from 192.0.2.21 to any port 7000 proto tcp comment 'store 192.0.2.21'",
            "ufw allow from 192.0.2.22 to any port 7000 proto tcp comment 'store 192.0.2.22'",
            "ufw allow from 192.0.2.32 to any port 7000 proto tcp comment 'store 192.0.2.32'",
        ])

    def test_any_source(self):
        got = self.filters()['ufw_link_rules'](DOC, 'web-1', GROUPS, HOSTVARS, 'install-web.yml', PORTS)
        self.assertEqual([r['cmd'] for r in got], ["ufw allow 80/tcp comment 'web any'"])

    def test_other_writer_gets_nothing(self):
        self.assertEqual(self.filters()['ufw_link_rules'](DOC, 'store-1', GROUPS, HOSTVARS, 'install-web.yml', PORTS), [])

    def test_missing_group_fails(self):
        with self.assertRaises(links.LinkError):
            links.ufw_rules(DOC, 'store-1', {'app': ['app-1']}, {}, 'install-store.yml', PORTS)


if __name__ == '__main__':
    unittest.main()
