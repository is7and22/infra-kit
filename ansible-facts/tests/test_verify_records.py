"""verify_records callback 的測試：對 localhost 真的跑 ansible-playbook

跑法見 ../README.md「測試」。ansible-playbook 用同一個 Python 環境的（ansible-core 帶的），
可用環境變數 ANSIBLE_PLAYBOOK_CMD 換（空白分隔）
"""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CALLBACK_DIR = HERE / 'callback'
PLAYBOOK = CALLBACK_DIR / 'verify-records-test.yml'
FAKE_SECRET = 'FAKE-SECRET-7f3a91c2'


def run_playbook(out_dir, *extra):
    if os.environ.get('ANSIBLE_PLAYBOOK_CMD'):
        cmd = shlex.split(os.environ['ANSIBLE_PLAYBOOK_CMD'])
    else:
        cmd = [str(Path(sys.executable).parent / 'ansible-playbook')]
    env = dict(os.environ)
    # callback/ansible.cfg 照 README「項目怎麼引用」的寫法啟用本目錄的 callback，只把 log 與紀錄目錄換掉
    env.update({
        'ANSIBLE_CONFIG': str(CALLBACK_DIR / 'ansible.cfg'),
        'ANSIBLE_LOG_PATH': str(Path(out_dir) / 'ansible.log'),
        'VERIFY_RECORDS_DIR': str(Path(out_dir) / 'results'),
    })
    proc = subprocess.run(
        cmd + ['-i', 'localhost,', '-c', 'local', str(PLAYBOOK), *extra],
        env=env, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise AssertionError('ansible-playbook 失敗：\n' + proc.stdout + proc.stderr)
    files = sorted((Path(out_dir) / 'results').glob('*.jsonl'))
    if len(files) != 1:
        raise AssertionError('應該剛好一個紀錄檔：%s' % files)
    lines = [json.loads(x) for x in files[0].read_text(encoding='utf-8').splitlines()]
    return files[0], lines


class VerifyRecordsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.check_tmp = tempfile.TemporaryDirectory()
        cls.file, cls.lines = run_playbook(cls.tmp.name)
        cls.check_file, cls.check_lines = run_playbook(cls.check_tmp.name, '--check')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        cls.check_tmp.cleanup()

    def steps(self, lines):
        return [x for x in lines if x['type'] == 'step']

    def test_verify_steps_with_results(self):
        got = [(x['step'], x['result']) for x in self.steps(self.lines)]
        self.assertEqual(got, [
            ('驗證：成功', 'ok'),
            ('驗證：失敗', 'failed'),
            ('驗證：跳過', 'skipped'),
            ('驗證：no_log 帶密碼', 'ok'),
            ('驗證：適用主機（no_log）', 'ok'),
            ('驗證：不適用主機', 'skipped'),
            ('驗證：指令輸出', 'ok'),
            ('驗證：長輸出', 'ok'),
            ('驗證：role 內的步驟', 'ok'),
            ('驗證：role 內的迴圈', 'ok'),
        ])

    def test_role_step_recorded_without_role_prefix(self):
        # ansible 的 task.get_name() 對 role 內的步驟回「verify_role : 驗證：…」，
        # 紀錄要寫步驟本身的名稱，轉接器才對得上 project.yaml components 的驗證步驟
        role_steps = [x for x in self.steps(self.lines) if x['play'] == 'verify_records 測試（role）']
        self.assertEqual([x['step'] for x in role_steps], ['驗證：role 內的步驟', '驗證：role 內的迴圈'])

    def test_step_without_prefix_not_recorded(self):
        self.assertNotIn('不是驗證的步驟', self.file.read_text(encoding='utf-8'))

    def test_only_listed_fields(self):
        allowed = {'type', 'time', 'playbook', 'play', 'host', 'step', 'result', 'check', 'service', 'method',
                   'version', 'report_hosts', 'command', 'output'}
        for x in self.steps(self.lines):
            self.assertLessEqual(set(x), allowed, x)
            self.assertEqual(x['host'], 'localhost')
            self.assertEqual(x['playbook'], 'verify-records-test.yml')
            self.assertFalse(x['check'])

    def test_fake_secret_not_in_any_record(self):
        for d in (self.tmp.name, self.check_tmp.name):
            for f in (Path(d) / 'results').rglob('*'):
                if f.is_file():
                    self.assertNotIn(FAKE_SECRET, f.read_text(encoding='utf-8'), f)

    def test_report_text_is_templated(self):
        first = self.steps(self.lines)[0]
        self.assertEqual(first['service'], 'Zot / 5000')
        self.assertEqual(first['method'], '測試方式文字')
        self.assertEqual(first['version'], '5000.0.1')
        self.assertNotIn('service', self.steps(self.lines)[1])
        self.assertNotIn('version', self.steps(self.lines)[1])

    def test_command_and_output(self):
        by_step = {x['step']: x for x in self.steps(self.lines)}
        rec = by_step['驗證：指令輸出']
        self.assertEqual(rec['command'], r"docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'")
        self.assertEqual(rec['output'], 'NAMES\tIMAGE\tSTATUS\napp\texample/app:v1.2.3\tUp 2 hours')
        self.assertNotIn('report_error', rec)
        self.assertNotIn('output', by_step['驗證：成功'])

    def test_long_output_truncated(self):
        # 300 行只記前 200 行，末行註明截斷
        out = {x['step']: x for x in self.steps(self.lines)}['驗證：長輸出']['output'].split('\n')
        self.assertEqual(len(out), 201)
        self.assertEqual(out[:200], [str(i) for i in range(1, 201)])
        self.assertEqual(out[200], '…（截斷：共 300 行，只記前 200 行）')

    def test_report_hosts_templated_for_ok_and_skipped(self):
        by_step = {x['step']: x for x in self.steps(self.lines)}
        ran, skipped = by_step['驗證：適用主機（no_log）'], by_step['驗證：不適用主機']
        self.assertEqual((ran['result'], ran['report_hosts']), ('ok', ['localhost']))
        self.assertEqual((skipped['result'], skipped['report_hosts']), ('skipped', ['db99']))
        self.assertNotIn('report_error', ran)
        self.assertNotIn('report_hosts', by_step['驗證：成功'])

    def test_summary_line(self):
        summary = self.lines[-1]
        self.assertEqual(summary['type'], 'summary')
        self.assertFalse(summary['check'])
        self.assertEqual(summary['hosts']['localhost']['unreachable'], 0)
        self.assertEqual(summary['hosts']['localhost']['failed'], 0)  # ignore_errors 不算 failures

    def test_check_mode_flagged(self):
        steps = self.steps(self.check_lines)
        self.assertTrue(steps)
        self.assertTrue(all(x['check'] for x in steps))
        self.assertTrue(self.check_lines[-1]['check'])


if __name__ == '__main__':
    unittest.main()
