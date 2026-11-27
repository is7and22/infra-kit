# verify_records：把名稱以「驗證：」開頭的步驟寫成結構化紀錄，給 ansible_facts.py（轉接器）讀
#
# 啟用：ansible.cfg 的 callbacks_enabled = verify_records
# 輸出：<playbook 所在目錄>/logs/results/<playbook 檔名>-<開始時間>.jsonl，一筆一行
#       環境變數 VERIFY_RECORDS_DIR 可改輸出目錄（測試用）
#
# 每筆只有這些欄位，不記步驟本身的輸出或參數（有些驗證步驟的參數帶密碼，no_log 的也一樣）：
#   {"type": "step", "time", "playbook", "play", "host", "step", "result", "check", "service", "method", "version", "command", "output"}
#   result 是 ok／failed／skipped／unreachable；check 是有沒有帶 --check
#   service、method、version、command、output 是驗證步驟的 vars 裡 report_service、report_method、report_version、
#   report_command、report_output 代入變數後的文字，沒寫就沒有這欄；
#   report_version 給測試報告書的軟體表：從主機或容器讀到的版本（前面步驟 register 的值），不要寫鎖定的變數
#   report_command 是給人看的指令原文，report_output 是前面步驟 register 的 stdout（驗收文件的舉證）。
#   output 超過 OUTPUT_MAX_LINES 行時只記前面那些行，末行註明截斷。只給不含密碼的指令（docker ps、版本查詢）
#   report_hosts 是 vars 裡 report_hosts 代入後的主機名清單（這步適用哪些主機），沒寫就沒有這欄
#   代入失敗（含 report_hosts 代入後不是字串清單）時寫 "report_error": true，不寫錯誤內容（錯誤訊息可能帶變數值）
# playbook 結束時另寫一行總結，轉接器用它判斷這次執行是否完整（中途中斷就沒有這一行）：
#   {"type": "summary", "time", "playbook", "check", "hosts": {"<主機>": {"ok", "failed", "skipped", "unreachable"}}}
#
# ansible-core 2.22 交給 callback 的 task.vars 是沒代入的原文，所以這裡用 play 的 variable manager 自己代入
# 代入的限制（寫 report_* 時注意）：拿不到同一步自己 register 的值；loop 步驟只記一筆，用 item 會變 report_error；
# run_once 的步驟只記第一台。report_* 會照樣寫出，no_log 的步驟也一樣，不要把密碼變數放進輸出文字
#
# report_hosts（不是每台都跑的報告步驟才寫）：寫主機名清單，用既有變數組，when 引用它，適用範圍只寫一處：
#     vars:
#       report_hosts: "{{ [pg_standby] }}"
#     when: inventory_hostname in report_hosts and not ansible_check_mode
# 轉接器讀它判斷：主機不在清單裡而 skipped 是不適用、不算缺；在清單裡卻 skipped，或不在清單裡卻跑了，都點名

import json
import os
from datetime import datetime

from ansible import context
from ansible.plugins.callback import CallbackBase
from ansible.template import Templar

DOCUMENTATION = """
    name: verify_records
    type: aggregate
    short_description: 把「驗證：」步驟寫成 jsonl 紀錄
    description:
      - 只記名稱以「驗證：」開頭的步驟的主機、結果、時間與 report_* 代入後的文字，不記步驟本身的輸出與參數
    requirements:
      - ansible.cfg 的 callbacks_enabled 加 verify_records
"""

PREFIX = '驗證：'
REPORT_KEYS = (('report_service', 'service'), ('report_method', 'method'), ('report_version', 'version'),
               ('report_command', 'command'), ('report_output', 'output'))
OUTPUT_MAX_LINES = 200


def _truncate(text):
    lines = text.splitlines()
    if len(lines) <= OUTPUT_MAX_LINES:
        return text
    return '\n'.join(lines[:OUTPUT_MAX_LINES] + ['…（截斷：共 %d 行，只記前 %d 行）' % (len(lines), OUTPUT_MAX_LINES)])


def _now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'aggregate'
    CALLBACK_NAME = 'verify_records'
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._path = None
        self._playbook = None
        self._play = None
        self._check = False

    def v2_playbook_on_start(self, playbook):
        pb_file = os.path.abspath(playbook._file_name)
        self._playbook = os.path.basename(pb_file)
        self._check = bool(context.CLIARGS.get('check', False))
        out_dir = os.environ.get('VERIFY_RECORDS_DIR') or os.path.join(os.path.dirname(pb_file), 'logs', 'results')
        os.makedirs(out_dir, exist_ok=True)
        stem = os.path.splitext(self._playbook)[0]
        self._path = os.path.join(out_dir, '%s-%s.jsonl' % (stem, datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')))

    def v2_playbook_on_play_start(self, play):
        self._play = play

    def _write(self, record):
        if not self._path:
            return
        with open(self._path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

    def _report_text(self, task, host):
        task_vars = task.vars or {}
        raw = {dst: task_vars[src] for src, dst in REPORT_KEYS if src in task_vars}
        if not raw and 'report_hosts' not in task_vars:
            return {}
        try:
            variables = self._play.get_variable_manager().get_vars(play=self._play, host=host, task=task)
            templar = Templar(loader=self._play._loader, variables=variables)
            out = {k: str(templar.template(v)) for k, v in raw.items()}
            if 'output' in out:
                out['output'] = _truncate(out['output'])
            if 'report_hosts' in task_vars:
                hosts = templar.template(task_vars['report_hosts'])
                if not isinstance(hosts, (list, tuple)) or not all(isinstance(h, str) for h in hosts):
                    return {'report_error': True}
                out['report_hosts'] = [str(h) for h in hosts]
            return out
        except Exception:
            return {'report_error': True}

    def _record(self, result, status):
        task = result.task
        # 用步驟本身的名稱：task.get_name() 對 role 內的步驟回「<role> : 驗證：…」，前綴比對不到；
        # 沒有 role 的步驟兩者相同
        name = task.name or ''
        if not name.startswith(PREFIX):
            return
        record = {
            'type': 'step',
            'time': _now(),
            'playbook': self._playbook,
            'play': self._play.get_name() if self._play else '',
            'host': result.host.get_name(),
            'step': name,
            'result': status,
            'check': self._check,
        }
        record.update(self._report_text(task, result.host))
        self._write(record)

    def v2_runner_on_ok(self, result):
        self._record(result, 'ok')

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._record(result, 'failed')

    def v2_runner_on_skipped(self, result):
        self._record(result, 'skipped')

    def v2_runner_on_unreachable(self, result):
        self._record(result, 'unreachable')

    def v2_playbook_on_stats(self, stats):
        hosts = {}
        for h in sorted(stats.processed.keys()):
            s = stats.summarize(h)
            hosts[h] = {'ok': s['ok'], 'failed': s['failures'], 'skipped': s['skipped'], 'unreachable': s['unreachable']}
        self._write({'type': 'summary', 'time': _now(), 'playbook': self._playbook, 'check': self._check, 'hosts': hosts})
