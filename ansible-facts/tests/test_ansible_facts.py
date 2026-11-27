"""ansible_facts.py（轉接器）的測試：假項目 project/ansible/ 與 project/results/ 的紀錄

跑法見 ../README.md「測試」。輸出用 acceptance-docs/ 的 check_facts.py 與 schema 驗（驗收文件 repo 的複本，來源見 acceptance-docs/README.md）。
每條有錯誤訊息的規則在 CASES 至少一份會紅的變體：從假項目複製一份，改一處，轉接器要以 1 結束、點名那條代號、不寫 json。
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADAPTER = HERE.parent / 'ansible_facts.py'
PROJECT = HERE / 'project' / 'ansible'
RESULTS = HERE / 'project' / 'results'
CHECK_FACTS = HERE / 'acceptance-docs' / 'check_facts.py'


_TMP = tempfile.TemporaryDirectory(prefix='ansible-facts-test-')


def run(mutate=None, results=RESULTS):
    """複製假項目、套用 mutate(ansible 目錄)、git init 後跑轉接器。回傳 (結束碼, stderr, json 或 None, json 路徑)"""
    tmp = Path(tempfile.mkdtemp(dir=_TMP.name))
    d = tmp / 'ansible'
    shutil.copytree(PROJECT, d)
    if mutate:
        mutate(d)
    subprocess.run(['git', 'init', '-q'], cwd=d, check=True)
    subprocess.run(['git', 'add', '-A'], cwd=d, check=True)
    out = tmp / '部署事實.json'
    proc = subprocess.run([sys.executable, str(ADAPTER), '--ansible-dir', str(d), '--results', str(results), '-o', str(out)],
                          capture_output=True, text=True, timeout=300)
    doc = json.loads(out.read_text(encoding='utf-8')) if out.exists() else None
    return proc.returncode, proc.stderr, doc, out


def replace(path, old, new, count=1):
    def mutate(d):
        f = d / path
        text = f.read_text(encoding='utf-8')
        assert old in text, (path, old)
        f.write_text(text.replace(old, new, count), encoding='utf-8')
    return mutate


def write(path, text):
    def mutate(d):
        (d / path).parent.mkdir(parents=True, exist_ok=True)
        (d / path).write_text(text, encoding='utf-8')
    return mutate


def delete(path):
    return lambda d: (d / path).unlink()


P = 'vars/project.yaml'
DUP_WEB = '''  - id: web2
    name: 網頁二
    type: container
    hosts: play:install-web.yml
    version_from: {id: web, file: install-web.yml, var: web_image, format: image}
    verify: {playbook: install-web.yml, step: 驗證：映像 Id 與清單一致}

  - id: monitor-agent'''
DUP_VERIFY = '''        step: 驗證：從庫在複寫
      - service: 儲存 {store_port} 主 → 從
        hosts: group:store_primary
        playbook: install-store.yml
        step: 驗證：從庫在複寫'''

# (規則代號, 說明, mutate, stderr 要出現的字)
CASES = [
    ('L2', '沒有 project.yaml', delete(P), 'project.yaml 不存在'),
    ('I1', '沒有 inventory', replace(P, 'inventory:\n  - inventory/shared\n  - inventory/prod\n', ''), '要有 inventory'),
    ('I2', '來源不是有 hosts.yml 的目錄', replace(P, '  - inventory/prod\n', '  - inventory/prod\n  - inventory/nope\n'), 'inventory/nope'),
    ('I6', '埠變數找不到', replace(P, 'port: web_port', 'port: nope_port'), 'nope_port'),
    ('I6', '埠變數兩份來源都定義', write('inventory/prod/group_vars/all.yml', 'store_port: "7001"\n'), '都有定義'),
    ('I7', 'inventory 以外的檔出現 IP', replace('install-web.yml', 'web_image:', '# 192.0.2.11\n    web_image:'), 'install-web.yml:5'),
    ('G1', '連線端點的群組沒有中文名稱', replace(P, '  app: 應用主機\n', ''), '沒有「app」'),
    ('G1', '本機服務的來源沒有中文名稱', replace(P, '  web: 網頁主機\n', ''), '沒有「web」'),
    ('G1', 'from 是 any 而沒有 any 的名稱', replace(P, '  any: 使用者端\n', ''), 'group_labels 要有 any'),
    ('C1', '不認得的欄位', replace(P, '    plan_name: 儲存引擎（資料）\n', '    plan_name: 儲存引擎（資料）\n    extra: 1\n'), '不認得的欄位 extra'),
    ('C1', 'id 重複', replace(P, 'id: web\n', 'id: store\n'), 'id 重複'),
    ('C2', 'type 不認得', replace(P, 'type: library', 'type: lib'), "type 'lib'"),
    ('C3', 'table: false 寫了 hosts', replace(P, '    table: false\n', '    table: false\n    hosts: group:web\n'), '不寫 hosts'),
    ('C4', '群組不在 inventory', replace(P, 'hosts: group:store_primary\n    version_from:', 'hosts: group:nope\n    version_from:'), '沒有群組 nope'),
    ('C4', 'hosts 寫法不認得', replace(P, 'hosts: group:app\n    version_from: record', 'hosts: targets:deploy-app.yml\n    version_from: record'), 'targets:deploy-app.yml'),
    ('C5', 'play: 指到 hosts 是運算式的 playbook', replace(P, 'hosts: play:install-web.yml\n    version_from', 'hosts: play:install-base.yml\n    version_from'), '不是群組名的清單'),
    ('C6', 'version_from 不是 record 或鎖定處', replace(P, 'version_from: record', 'version_from: 3'), 'version_from 要是'),
    ('C7', '鎖定處缺 format', replace(P, 'var: web_image, format: image}', 'var: web_image}'), '缺 format'),
    ('C7', '同一個 id 兩種寫法', replace(P, 'id: base-helper, file: roles/base/defaults/main.yml', 'id: base, file: roles/base/defaults/main.yml'), '兩種寫法'),
    ('C8', '鎖定處找不到', replace(P, 'var: web_image', 'var: nope_image'), 'nope_image'),
    ('C8', '鎖定處不只一處', replace('install-web.yml', '    web_image: example/web:v1.0.3\n', '    web_image: example/web:v1.0.3\n    other:\n      web_image: example/web:v1.0.4\n'), '有 2 處'),
    ('C9', '版本格式不認得', replace('install-web.yml', 'example/web:v1.0.3', 'example/web:latest'), "'example/web:latest'"),
    ('C12', '步驟名找不到', replace(P, 'step: 驗證：代理在跑', 'step: 驗證：不存在'), '找不到步驟「驗證：不存在」'),
    ('C12', 'verify 的 playbook 不存在', replace(P, 'verify: {playbook: deploy-app.yml', 'verify: {playbook: nope.yml'), 'nope.yml 不存在'),
    ('C13', '兩筆元件 hosts 與 verify 一樣', replace(P, '  - id: monitor-agent', DUP_WEB), '跟 component web 一樣'),
    ('N1', 'protocol 不認得', replace(P, 'protocol: tcp', 'protocol: sctp'), "protocol 'sctp'"),
    ('N2', '連線的群組不在 inventory', replace(P, 'to: store_primary', 'to: nope'), '沒有群組 nope'),
    ('N3', '跨主機的埠寫數字', replace(P, 'port: web_port', 'port: 80'), '只有 self 可以'),
    ('N4', 'firewall 的 playbook 沒有 ufw_writer', replace(P, 'firewall: install-store.yml', 'firewall: install-base.yml'), '沒有 `ufw_writer: install-base.yml`'),
    ('N4', '有 firewall 沒有 ufw_comment', replace(P, '    ufw_comment: "web {host}"\n', ''), 'ufw_comment'),
    ('N5', 'verify 的步驟找不到', replace(P, 'step: 驗證：本機服務回 200', 'step: 驗證：不存在'), '找不到步驟「驗證：不存在」'),
    ('N5', 'verify 挑了名稱含 {{ }} 的步驟', replace(P, 'step: 驗證：連得到儲存', 'step: 驗證：應用 容器在跑'), '找不到步驟「驗證：應用 容器在跑」'),
    ('N5', 'verify 多了不認得的欄位', replace(P, '        note: 從應用容器連儲存\n', '        note: 從應用容器連儲存\n        extra: 1\n'), '不認得的欄位 extra'),
    ('N5', 'verify 缺欄位', replace(P, '        note: 從應用容器連儲存\n', '        note: 從應用容器連儲存\n        step: null\n'), '缺 step'),
    ('N1', 'id 含其他規則的字也歸 N1', replace(P, "id: users-web\n    from: any\n    to: web\n    port: web_port\n    protocol: tcp", "id: users-port\n    from: any\n    to: web\n    port: web_port\n    protocol: sctp"), "protocol 'sctp'"),
    ('N1', '缺 port', replace(P, '    port: app_port\n', ''), '缺 port'),
    ('N2', 'to 寫成清單', replace(P, 'to: store_primary', 'to: [store_primary]'), 'to 要是一個群組名'),
    ('N2', 'from 有 any 又列群組', replace(P, 'from: any', 'from: [any, app]'), 'from 有 any'),
    ('N3', 'port 不是變數名', replace(P, 'port: web_port', 'port: web-port'), "port 'web-port'"),
    ('N6', 'service 的大括號沒關', replace(P, 'service: 網頁 {app_port}', 'service: 網頁 {app_port'), '寫法不對'),
    ('C12', '步驟名結尾是變數時不放寬', replace(P, 'step: 驗證：代理在跑', 'step: 驗證：只聽 打錯字'), '找不到步驟「驗證：只聽 打錯字」'),
    ('C3', 'table: false 用 record', replace(P, "version_from: {id: base-helper, file: roles/base/defaults/main.yml, list: base_debs, item: base-helper, var: version, format: deb}", 'version_from: record'), 'C3'),
    ('N6', 'service 的變數找不到', replace(P, 'service: 網頁 {app_port}', 'service: 網頁 {nope_port}'), 'nope_port'),
    ('N7', '同一服務同一範圍列兩次', replace(P, '        step: 驗證：從庫在複寫', DUP_VERIFY), '列了兩次'),
]


class AdapterOkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rc, cls.err, cls.doc, cls.out = run()

    def test_exit_ok_and_passes_check_facts(self):
        self.assertEqual(self.rc, 0, self.err)
        proc = subprocess.run([sys.executable, str(CHECK_FACTS), str(self.out)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def comp(self, cid):
        return next(c for c in self.doc['components'] if c['id'] == cid)

    def inst(self, cid, host):
        return next(i for i in self.comp(cid)['instances'] if i['host'] == host)

    def test_hosts_order_and_no_ansible_host_skipped(self):
        # I3、I4：來源照 inventory 的順序；沒有 ansible_host 的 localhost 不輸出，local 群組因此是空的、不輸出
        self.assertEqual([h['name'] for h in self.doc['hosts']], ['mon-1', 'web-1', 'app-1', 'app-2', 'store-1', 'store-2'])
        self.assertNotIn('local', [g['name'] for g in self.doc['groups']])
        self.assertEqual(next(g for g in self.doc['groups'] if g['name'] == 'store_primary'),
                         {'name': 'store_primary', 'label': '儲存主機（主）', 'hosts': ['store-1']})

    def test_versions_from_locks(self):
        # C8、C9：deb 清單項目、parent 區塊、映像、plain 各一種
        self.assertEqual(self.comp('base')['versions'], [{'version': '29.1.3', 'full': '5:29.1.3-0ubuntu1', 'lock': 'base'}])
        self.assertEqual(self.comp('store')['versions'], [
            {'version': '18.6', 'full': '18.6-0ubuntu0.1', 'lock': 'store-engine'},
            {'version': '0.8.1', 'label': '外掛', 'full': '0.8.1-2', 'lock': 'store-ext'}])
        self.assertEqual(self.comp('web')['versions'][0]['version'], '1.0.3')
        self.assertEqual(self.comp('monitor-agent')['versions'][0]['version'], '0.165.0')
        helper = self.comp('base-helper')
        self.assertEqual((helper['in_table'], helper['versions'][0]['version']), (False, '2.40.3'))
        self.assertNotIn('instances', helper)

    def test_play_hosts_and_role_group(self):
        # C4：play: 讀 play 的 hosts:；group: 角色群組只展開到那台
        self.assertEqual([i['host'] for i in self.comp('web')['instances']], ['web-1'])
        self.assertEqual([i['host'] for i in self.comp('store')['instances']], ['store-1'])

    def test_record_version(self):
        # C11：版本取紀錄；紀錄沒有版本時保留結果、problem 寫原因
        self.assertEqual(self.inst('app', 'app-1')['versions'], [{'version': 'v1.2.3'}])
        app2 = self.inst('app', 'app-2')
        self.assertEqual(app2['check']['result'], 'pass')
        self.assertIn('紀錄沒有 version', app2['problem'])

    def test_failed_before_step_not_reused(self):
        # V7：主機在這步之前就失敗，不沿用、寫 null 與原因
        app2 = self.inst('base', 'app-2')
        self.assertIsNone(app2['check'])
        self.assertIn('這步之前就失敗', app2['problem'])

    def test_later_check_run_ignored(self):
        # V7：較晚的 --check 不算正式執行，結果照最近一次正式的
        self.assertEqual(self.inst('web', 'web-1')['check'], {'result': 'pass', 'time': '2026-11-20T10:00:00+08:00'})

    def test_links(self):
        links = {l['id']: l for l in self.doc['links']}
        self.assertEqual(links['users-web']['from'], [{'any': True, 'label': '使用者端'}])
        self.assertEqual((links['users-web']['ports'], links['users-web']['firewalled']), (['80'], True))
        self.assertEqual(links['web-local']['to'], {'self': True})
        self.assertFalse(links['web-local']['firewalled'])
        chk = links['app-store']['checks'][0]
        self.assertEqual((chk['service'], chk['note']), ('應用 → 儲存 7000', '從應用容器連儲存'))
        self.assertEqual([(i['host'], i['check']['result']) for i in chk['instances']], [('app-1', 'pass'), ('app-2', 'fail')])
        self.assertEqual(chk['instances'][0]['check']['method'], '連 192.0.2.31:7000 回應正常')
        self.assertEqual(chk['instances'][0]['check']['service'], '儲存 / 7000')

    def test_extra_host_in_problems(self):
        # V8：store-2 用 -e 多裝了基礎套件，components 沒展開到
        self.assertEqual(len(self.doc['problems']), 1)
        self.assertIn('store-2', self.doc['problems'][0]['message'])


class AdapterRecordProblemsTest(unittest.TestCase):
    """V1–V3、V7 不是錯：照樣寫出，null 與 problem"""

    def test_results_not_a_dir(self):
        rc, err, doc, _ = run(results=HERE / 'project' / 'nope')
        self.assertEqual((rc, doc), (2, None))
        self.assertIn('不是目錄', err)

    def test_bad_jsonl_is_error(self):
        tmp = Path(tempfile.mkdtemp(dir=_TMP.name))
        (tmp / 'x-1.jsonl').write_text('{"type": "step"\n', encoding='utf-8')
        rc, err, doc, _ = run(results=tmp)
        self.assertEqual((rc, doc), (1, None))
        self.assertIn('錯：V6 ', err)

    def test_no_records(self):
        rc, err, doc, _ = run(results=Path(tempfile.mkdtemp(dir=_TMP.name)))
        self.assertEqual(rc, 0, err)
        self.assertIn('沒有任何紀錄', err)
        insts = [i for c in doc['components'] for i in c.get('instances', [])]
        self.assertTrue(all(i['check'] is None and '沒有正式執行的紀錄' in i['problem'] for i in insts))

    def test_record_problems(self):
        tmp = Path(tempfile.mkdtemp(dir=_TMP.name))
        shutil.copytree(RESULTS, tmp, dirs_exist_ok=True)
        f = tmp / 'install-store-20261120-100000.jsonl'
        lines = [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines()]
        # 從庫那步拿掉 report_method、版本那步重複一筆
        del lines[1]['method']
        lines.insert(0, dict(lines[0]))
        f.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in lines), encoding='utf-8')
        rc, err, doc, _ = run(results=tmp)
        self.assertEqual(rc, 0, err)
        store = next(c for c in doc['components'] if c['id'] == 'store')['instances'][0]
        self.assertIsNone(store['check'])
        self.assertIn('2 筆同名紀錄', store['problem'])
        rep = next(l for l in doc['links'] if l['id'] == 'store-replication')['checks'][0]['instances'][0]
        self.assertIsNone(rep['check'])
        self.assertIn('沒有 report_method', rep['problem'])


class AdapterOtherRulesTest(unittest.TestCase):
    """CASES 以外、有錯誤訊息的規則"""

    def test_L1_no_ansible_dir(self):
        proc = subprocess.run([sys.executable, str(ADAPTER), '--ansible-dir', '/nonexistent/ansible', '-o', '/dev/null'],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('L1', proc.stderr)

    def test_I3_expression_blocked_by_check_facts(self):
        # ansible_host 寫運算式：轉接器照寫，check_facts.py 的 schema 擋下
        rc, err, doc, out = run(replace('inventory/prod/hosts.yml', 'ansible_host: 192.0.2.11', 'ansible_host: "{{ web_ip }}"'))
        self.assertEqual(rc, 0, err)
        proc = subprocess.run([sys.executable, str(CHECK_FACTS), str(out)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('$.hosts[1].ip', proc.stdout + proc.stderr)

    def test_I3_group_empty_without_ansible_host(self):
        rc, err, _, _ = run(replace('inventory/shared/hosts.yml', '          ansible_host: 198.51.100.10\n', ''))
        self.assertEqual(rc, 1)
        self.assertIn('沒有群組 monitor 或它沒有主機', err)


OTHER_RULES = {'L1', 'I3'}  # AdapterOtherRulesTest


class AdapterErrorTest(unittest.TestCase):
    def test_cases(self):
        for code, why, mutate, expect in CASES:
            with self.subTest(code=code, why=why):
                rc, err, doc, _ = run(mutate)
                self.assertEqual(rc, 1, err)
                self.assertIsNone(doc)
                lines = [x for x in err.splitlines() if x.startswith('錯：')]
                self.assertTrue(any(x.startswith('錯：%s ' % code) and expect in x for x in lines), err)

    def test_every_rule_with_error_message_has_a_case(self):
        """README 每條「違反時」有錯誤訊息的規則，CASES 至少一份"""
        readme = (HERE.parent / 'README.md').read_text(encoding='utf-8')
        rules = set()
        for line in readme.splitlines():
            cells = [c.strip() for c in line.strip('|').split('|')]
            if len(cells) >= 3 and cells[0][:1] in 'LIGCN' and cells[0][1:].isdigit() and '有錯誤訊息' in cells[-1]:
                rules.add(cells[0])
        self.assertTrue(rules)
        self.assertEqual(rules - {c[0] for c in CASES} - OTHER_RULES, set())


if __name__ == '__main__':
    unittest.main()
