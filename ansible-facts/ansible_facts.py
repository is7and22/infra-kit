#!/usr/bin/env python3
"""ansible 的轉接器：讀項目的 ansible/（inventory、vars/project.yaml、版本鎖定處、logs/results），寫出 部署事實.json

規則見同目錄 README.md（L、I、G、C、N、V、E、S 各代號）。本檔只交事實：表格列怎麼排、版本怎麼接、用語，都是驗收文件那邊的事。

用法（在項目根目錄，用跳板同版 ansible-core 帶的 Python 跑，它有 PyYAML 與 ansible-inventory）：
    mise x uv@0.13.9 -- uvx --from ansible-core==2.22.0 python ansible/vendor/infra-kit/ansible-facts/ansible_facts.py \\
        --ansible-dir ansible -o 部署事實.json

結束碼：0 寫出 json；1 違反有錯誤訊息的規則，每項一行，不寫 json；2 參數或環境錯（找不到 ansible-inventory、目錄不存在）。
紀錄挑不到結果（V1、V2、V7）不算錯：照樣寫出，在 json 的 problem 與 problems 點名，由讀的一方決定怎麼處理。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import string
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
FORMAT_VERSION = 1
PROJECT = Path('vars/project.yaml')
RECORD = 'record'
# 後面可以接句點（句尾），不能接「.數字」
IP_RE = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)')
GROUPS_SKIP = ('all', 'ungrouped')

# S2：links 的讀法只在 filter_plugins/connection_links.py，ansible 的 filter 與本檔載同一份
_spec = importlib.util.spec_from_file_location('connection_links', HERE / 'filter_plugins' / 'connection_links.py')
links_lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(links_lib)


class Stop(Exception):
    """參數或環境錯，結束碼 2"""


class LockError(Exception):
    """鎖定處、play 的 hosts: 讀不到"""


class FormatError(LockError):
    """C9：鎖定的版本格式不認得"""


# ── 讀檔 ──

def _lines(path):
    return Path(path).read_text(encoding='utf-8').splitlines()


def _strip(v):
    v = v.split(' #')[0].strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
        v = v[1:-1]
    return v


def read_yaml(path):
    try:
        return yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise LockError('%s 不存在' % path)
    except yaml.YAMLError as e:
        raise LockError('%s 不是合法的 YAML：%s' % (path, e))


# ── 二、inventory（I1–I4、I6） ──

def inventory_cmd():
    """ansible-inventory：環境變數 ANSIBLE_INVENTORY_CMD（空白分隔）> 同一個 Python 環境的 > PATH 上的"""
    if os.environ.get('ANSIBLE_INVENTORY_CMD'):
        return shlex.split(os.environ['ANSIBLE_INVENTORY_CMD'])
    sibling = Path(sys.executable).parent / 'ansible-inventory'
    if sibling.is_file():
        return [str(sibling)]
    found = shutil.which('ansible-inventory')
    if found:
        return [found]
    raise Stop('找不到 ansible-inventory：用 ansible-core 帶的 Python 跑本檔（見 README「執行環境」），或設 ANSIBLE_INVENTORY_CMD')


def _ansible_inventory(sources, cwd):
    cmd = inventory_cmd()
    for src in sources:
        cmd += ['-i', str(src)]
    # cwd 設在項目根目錄：不讀 ansible/ansible.cfg，免得它的 log_path 把這次查詢寫進 logs/
    proc = subprocess.run(cmd + ['--list'], cwd=cwd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise LockError('ansible-inventory 失敗：\n' + proc.stderr)
    return json.loads(proc.stdout)


def _walk(data):
    """ansible-inventory --list 的輸出走一遍：({主機名: IP}（照走到的順序）, {群組: [主機名]})。只收有 ansible_host 的主機（I3）"""
    hostvars = data.get('_meta', {}).get('hostvars', {})
    hosts, groups = {}, {}

    def walk(group):
        members = []
        for h in data.get(group, {}).get('hosts', []):
            ip = hostvars.get(h, {}).get('ansible_host')
            if ip:
                members.append(h)
                hosts.setdefault(h, str(ip))
        for child in data.get(group, {}).get('children', []):
            members += [h for h in walk(child) if h not in members]
        groups[group] = members
        return members

    walk('all')
    return hosts, groups


def read_inventory(sources, cwd):
    """({主機名: IP}, {群組: [主機名]})。群組與 IP 照所有來源疊起來的結果；主機順序照來源順序、來源內照檔裡的順序（I4）"""
    hosts, groups = _walk(_ansible_inventory(sources, cwd))
    if len(sources) > 1:
        order = []
        for src in sources:
            order += [h for h in _walk(_ansible_inventory([src], cwd))[0] if h not in order]
        order += [h for h in hosts if h not in order]
        hosts = {h: hosts[h] for h in order if h in hosts}
    return hosts, groups


# ── 四、components 的鎖定處（C7–C9） ──

def read_var(path, key, parent=None):
    """讀 `key: 值`。給 parent 時只在 `parent:` 這個區塊（縮排比它深的連續行）裡找。要剛好一處"""
    lines = _lines(path)
    spans = [(0, len(lines))]
    if parent is not None:
        spans = []
        for i, line in enumerate(lines):
            m = re.match(r'^(\s*)%s:\s*$' % re.escape(parent), line)
            if m:
                indent = len(m.group(1))
                j = i + 1
                while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > indent):
                    j += 1
                spans.append((i + 1, j))
        if len(spans) != 1:
            raise LockError('%s：「%s:」區塊有 %d 處，要剛好一處' % (path, parent, len(spans)))
    found = []
    for a, b in spans:
        for line in lines[a:b]:
            m = re.match(r'^\s*%s:\s*(\S.*)$' % re.escape(key), line)
            if m:
                found.append(_strip(m.group(1)))
    if len(found) != 1:
        raise LockError('%s：%s「%s」有 %d 處，要剛好一處' % (path, '「%s」裡的' % parent if parent else '', key, len(found)))
    return found[0]


def read_list_item(path, list_key, name, field):
    """讀頂層 `list_key:` 底下 `- name: <name>` 那一項的 field"""
    lines = _lines(path)
    starts = [i for i, l in enumerate(lines) if re.match(r'^%s:\s*$' % re.escape(list_key), l)]
    if len(starts) != 1:
        raise LockError('%s：「%s:」有 %d 處，要剛好一處' % (path, list_key, len(starts)))
    # 每個「- 」開一項；只收這一項第一層的鍵（「- 」那行的鍵與縮排對齊它的鍵），巢狀的不收
    items, cur, key_indent = [], None, None
    for line in lines[starts[0] + 1:]:
        if line.strip() and not line.startswith((' ', '-')):
            break
        m = re.match(r'^(\s*)-\s+(.*)$', line)
        if m:
            cur, key_indent = {}, len(m.group(1)) + 2
            items.append(cur)
            line = ' ' * key_indent + m.group(2)
        m = re.match(r'^(\s*)([\w-]+):\s*(\S.*)$', line)
        if m and cur is not None and len(m.group(1)) == key_indent:
            cur.setdefault(m.group(2), _strip(m.group(3)))
    hits = [it for it in items if it.get('name') == name]
    if len(hits) != 1 or field not in hits[0]:
        raise LockError('%s：「%s」裡 name 是 %s 的項目有 %d 個（要剛好一個，且有 %s）' % (path, list_key, name, len(hits), field))
    return hits[0][field]


# +ds、+dfsg 是 Debian 重新打包，上游不變；+really 等其他後綴表示上游不是前面那串，認不得就失敗
DEB_RE = re.compile(r'^(?:\d+:)?(?P<up>\d+(?:\.\d+)*)(?:\+(?:ds|dfsg)\d*)?-[A-Za-z0-9.+~]+$')
PLAIN_RE = re.compile(r'^(?P<up>\d+(?:\.\d+)+)$')
IMAGE_RE = re.compile(r'^[\w.\-/:]+:v?(?P<up>\d+(?:\.\d+)*)$')
FORMATS = {
    'deb': (DEB_RE, '[epoch:]上游[+ds|+dfsg]-修訂'),
    'image': (IMAGE_RE, '名稱:v1.2.3 或 名稱:1.2.3'),
    'plain': (PLAIN_RE, '1.2.3'),
}


def upstream(fmt, value):
    """C9：鎖定的完整版本取上游版本。格式認不得就失敗，不猜"""
    pattern, shape = FORMATS[fmt]
    m = pattern.match(value)
    if not m:
        raise FormatError('版本 %r 的格式不認得（format %s 要像 %s）' % (value, fmt, shape))
    return m.group('up')


def read_lock(lock, ansible_dir):
    """一個鎖定處：{'full', 'version'}"""
    path = Path(ansible_dir) / lock['file']
    if not path.is_file():
        raise LockError('鎖定處 %s 的檔案 %s 不存在' % (lock['id'], lock['file']))
    if 'list' in lock:
        full = read_list_item(path, lock['list'], lock['item'], lock['var'])
    else:
        full = read_var(path, lock['var'], lock.get('parent'))
    return {'full': full, 'version': upstream(lock['format'], full)}


# ── playbook：play 的 hosts:、include 的檔、步驟名（C4、C5、C12、N4、N5） ──

GROUP_LIST_RE = re.compile(r'[\w-]+(?:[:,][\w-]+)*')


def play_hosts(path):
    """C5：playbook 裡 play 的 hosts:（import_playbook 不算），要剛好一個 play，值是群組名用 : 或 , 接。回傳群組名清單"""
    found = [_strip(m.group(1)) for m in (re.match(r'^  hosts:\s*(\S.*)$', l) for l in _lines(path)) if m]
    if len(found) != 1:
        raise LockError('%s：play 的 hosts: 有 %d 處，play: 只能指只有一個 play 的 playbook' % (path.name, len(found)))
    if not GROUP_LIST_RE.fullmatch(found[0]):
        raise LockError('%s 的 hosts: 是 %s，不是群組名的清單（含運算式、!、&）；主機由 -e 決定的 playbook 改寫 group:' % (
            path.name, found[0]))
    return re.split(r'[:,]', found[0])


INCLUDE_RE = re.compile(r'(?:include_tasks|import_tasks|import_playbook):\s*(\S+\.yml)')
ROLE_RE = re.compile(r'(?:import_role|include_role)\b')
ROLE_NAME_RE = re.compile(r'\bname:\s*([\w-]+)')


def files_of(playbook, ansible_dir):
    """playbook 與它 include／import 的檔（tasks、playbook、role 的 tasks），遞迴"""
    d = Path(ansible_dir)
    todo, seen = [d / playbook], []
    while todo:
        f = todo.pop()
        if f in seen or not f.is_file():
            continue
        seen.append(f)
        lines = f.read_text(encoding='utf-8').splitlines()
        for i, line in enumerate(lines):
            for ref in INCLUDE_RE.findall(line):
                ref = _strip(ref)
                todo += [d / ref, f.parent / ref]
            roles = []
            if ROLE_RE.search(line):
                m = ROLE_NAME_RE.search(' '.join(lines[i:i + 3]))
                roles += [m.group(1)] if m else []
            if re.match(r'^\s*roles:\s*$', line):
                for nxt in lines[i + 1:]:
                    m = re.match(r'^\s*-\s*(?:role:\s*)?([\w-]+)\s*$', nxt)
                    if not m:
                        break
                    roles.append(m.group(1))
            for r in roles:
                todo += sorted((d / 'roles' / r / 'tasks').glob('*.yml'))
    return seen


def step_names(playbook, ansible_dir):
    """playbook 與它 include／import 的檔裡，名稱以「驗證：」開頭的步驟名稱（原文，不代入變數）"""
    names = set()
    for f in files_of(playbook, ansible_dir):
        for line in f.read_text(encoding='utf-8').splitlines():
            m = re.match(r'^\s*-\s+name:\s*(\S.*)$', line)
            if m:
                name = _strip(m.group(1))
                if name.startswith('驗證：'):
                    names.add(name)
    return names


VAR_RE = re.compile(r'\{\{.*?\}\}')


def step_found(step, names, allow_vars):
    """紀錄上的步驟名在 playbook 找得到：原文一樣；allow_vars 時（C12）名稱中間含 {{ 變數 }} 的步驟，每個 {{ }} 換成任意文字後一樣"""
    if step in names:
        return True
    if not allow_vars:
        return False
    for n in names:
        parts = VAR_RE.split(n)
        # 變數在名稱開頭或結尾時不放寬：會變成前綴或後綴比對，打錯字也對得上
        if len(parts) > 1 and parts[0] and parts[-1] and re.fullmatch('.+'.join(re.escape(s) for s in parts), step):
            return True
    return False


def writes_ufw(playbook, ansible_dir):
    """N4：playbook（含它 include 的檔）有 `ufw_writer: <它自己的檔名>`"""
    pat = re.compile(r'^\s*ufw_writer:\s*["\']?%s["\']?\s*$' % re.escape(playbook), re.M)
    return any(pat.search(f.read_text(encoding='utf-8')) for f in files_of(playbook, ansible_dir))


# ── 六、驗證紀錄（V5–V8） ──

def _t(s):
    return datetime.fromisoformat(s)


def load_runs(results_dir):
    """每個 jsonl 是一次執行：[{'name', 'steps', 'summary', 'formal'}]。V7：正式執行是不是 --check、有總結行、總結沒有 unreachable"""
    runs = []
    for f in sorted(Path(results_dir).glob('*.jsonl')):
        steps, summary = [], None
        for n, line in enumerate(f.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                raise LockError('%s 第 %d 行不是 JSON' % (f, n))
            if rec.get('type') == 'summary':
                summary = rec
            elif rec.get('type') == 'step':
                steps.append(rec)
        formal = (
            summary is not None
            and not summary.get('check')
            and all(h.get('unreachable', 0) == 0 for h in summary.get('hosts', {}).values())
            and not any(s.get('check') for s in steps)
        )
        runs.append({'name': f.name, 'steps': steps, 'summary': summary, 'formal': formal})
    return runs


class Records:
    """latest_run：(playbook, 主機) -> 最近一次正式執行；newest：(playbook, 主機, 步驟) -> 任何執行裡最晚出現的時間"""

    def __init__(self, runs):
        self.latest_run = {}
        for run in runs:
            if not run['formal']:
                continue
            pb, t = run['summary']['playbook'], _t(run['summary']['time'])
            for host in run['summary'].get('hosts', {}):
                cur = self.latest_run.get((pb, host))
                if cur is None or t >= _t(cur['summary']['time']):
                    self.latest_run[(pb, host)] = run
        self.newest = {}
        for run in runs:
            for rec in run['steps']:
                key = (rec['playbook'], rec['host'], rec['step'])
                if key not in self.newest or _t(rec['time']) > self.newest[key]:
                    self.newest[key] = _t(rec['time'])

    def latest(self, pb, host, step):
        """一個（playbook、主機、步驟）在最近一次正式執行的紀錄：(rec, None) 或 (None, problem)"""
        run = self.latest_run.get((pb, host))
        if run is None:
            return None, '沒有正式執行的紀錄（只有 --check 或中斷、有 unreachable 的執行）'
        recs = [r for r in run['steps'] if r['host'] == host and r['step'] == step]
        run_time = run['summary']['time']
        if len(recs) > 1:
            # V2：role 內的步驟記的是不帶 role 前綴的名稱，同一支 playbook 有兩步同名就分不出是哪一步
            return None, '%s 那次有 %d 筆同名紀錄，分不出是哪一步，把步驟改成不同名稱' % (run_time, len(recs))
        if recs:
            return recs[0], None
        seen = self.newest.get((pb, host, step))
        if seen is not None and seen > _t(run_time):
            return None, '最近一次出現在預演或不完整的執行，還沒有正式執行'
        if run['summary']['hosts'][host].get('failed', 0) > 0:
            return None, '最近一次正式執行（%s）在這步之前就失敗，這步沒跑' % run_time
        return None, '最近一次正式執行（%s）沒有這一步' % run_time


RESULT = {'ok': 'pass', 'failed': 'fail'}


# ── 轉接器本體 ──

COMPONENT_KEYS = {'id', 'name', 'plan_name', 'type', 'hosts', 'version_from', 'verify', 'table', 'evidence'}
EVIDENCE_KEYS = ('id', 'title', 'hosts', 'playbook', 'step')
COMPONENT_EVIDENCE_KEYS = {'id', 'line'}
VERSION_FIELD = '{version}'
LOCK_KEYS = {'id', 'file', 'var', 'parent', 'list', 'item', 'format', 'label'}
TYPES = ('application', 'container', 'library', 'operating-system', 'platform', 'framework')
HOST_KINDS = ('group', 'play')


def _fields(text):
    return [f for _, f, _, _ in string.Formatter().parse(text) if f]


def _check_doc_rule(message):
    """connection_links.check_doc 的訊息歸到哪條規則。看「link <id>」之後的內文，id 裡的字不影響"""
    head, _, body = message.partition('：')
    if ' 的 verify 第 ' in head:
        return 'N5'
    if body.startswith(('不認得的欄位', '缺 ', 'id 重複', 'protocol')):
        return 'N1'
    if body.startswith('port '):
        return 'N3'
    if 'firewall' in body or 'ufw_comment' in body:
        return 'N4'
    if body.startswith('from 有 any'):
        return 'N2'
    return 'N1'


def _shape_errors(doc):
    """check_doc 不驗型別：links 的 from、to 與 verify 的值型別不對時先擋（否則後面會 traceback）。[(代號, 訊息)]"""
    links = doc.get('links') if isinstance(doc, dict) else None
    if not isinstance(links, list):
        return []  # check_doc 會報
    out = []
    for i, link in enumerate(links):
        if not isinstance(link, dict):
            out.append(('N1', 'project.yaml links 第 %d 項要是 mapping' % (i + 1)))
            continue
        where = 'project.yaml link %s' % (link.get('id') or '第 %d 條' % (i + 1))
        frm = link.get('from')
        if not (isinstance(frm, str) or (isinstance(frm, list) and all(isinstance(g, str) for g in frm))):
            out.append(('N2', '%s：from 要是群組名或群組名的清單，寫的是 %r' % (where, frm)))
        if link.get('to') is not None and not isinstance(link.get('to'), str):
            out.append(('N2', '%s：to 要是一個群組名或 self，寫的是 %r' % (where, link['to'])))
        verify = link.get('verify')
        if verify is not None and not (isinstance(verify, list) and all(isinstance(v, dict) for v in verify)):
            out.append(('N5', '%s：verify 要是 mapping 的清單' % where))
            continue
        for j, v in enumerate(verify or []):
            for k in ('service', 'hosts', 'playbook', 'step', 'note'):
                if v.get(k) is not None and not isinstance(v[k], str):
                    out.append(('N5', '%s 的 verify 第 %d 項：%s 要是字串' % (where, j + 1, k)))
            if isinstance(v.get('service'), str):
                try:
                    _fields(v['service'])
                except ValueError as e:
                    out.append(('N6', '%s 的 verify 第 %d 項：service「%s」的 {變數} 寫法不對（%s）' % (where, j + 1, v['service'], e)))
    return out


class Facts:
    """讀一個項目的 ansible/。errors 收有錯誤訊息的規則；有錯時不寫 json"""

    def __init__(self, ansible_dir, results_dir=None):
        self.d = Path(ansible_dir).resolve()
        if not self.d.is_dir():
            raise Stop('L1 %s 不是目錄（ansible 放在 <項目根>/ansible/）' % ansible_dir)
        if results_dir and not Path(results_dir).is_dir():
            # 明確給了卻不是目錄：多半是路徑打錯，不要當成「沒有紀錄」寫出全 null
            raise Stop('--results %s 不是目錄' % results_dir)
        self.results_dir = Path(results_dir) if results_dir else self.d / 'logs' / 'results'
        self.errors = []
        self.port_dup_reported = set()

    def err(self, rule, message):
        self.errors.append('%s %s' % (rule, message))

    # L2、I1、I2
    def load_project(self):
        path = self.d / PROJECT
        if not path.is_file():
            self.err('L2', '%s 不存在（項目資料檔固定是 ansible/vars/project.yaml）' % path)
            return False
        try:
            doc = read_yaml(path)
        except LockError as e:
            self.err('L2', str(e))
            return False
        if not isinstance(doc, dict):
            self.err('L2', '%s 的最上層要是 mapping' % path)
            return False
        self.doc = doc
        srcs = doc.get('inventory')
        if not isinstance(srcs, list) or not srcs or not all(isinstance(s, str) and s for s in srcs):
            self.err('I1', 'project.yaml 要有 inventory：相對 ansible/ 的 inventory 目錄清單，照疊的順序')
            return False
        self.sources = [self.d / s for s in srcs]
        ok = True
        for s, src in zip(srcs, self.sources):
            if not (src / 'hosts.yml').is_file():
                self.err('I2', 'inventory 來源 %s 要是目錄、裡面有 hosts.yml（YAML 格式）' % s)
                ok = False
        return ok

    def load_inventory(self):
        try:
            self.hosts, self.groups = read_inventory(self.sources, self.d.parent)
        except LockError as e:
            self.err('I2', str(e))
            return False
        return True

    def group_hosts(self, names, rule, where):
        """群組展開成主機；群組不在 inventory 或沒有主機時記錯、回 None"""
        out = []
        for g in names:
            if not self.groups.get(g):
                self.err(rule, '%s：inventory 沒有群組 %s 或它沒有主機（project.yaml 的 inventory 少列了來源，或群組改了名）' % (where, g))
                return None
            out += [h for h in self.groups[g] if h not in out]
        return out

    def hosts_spec(self, spec, rule, where):
        """C4、C5、N5：`group:<群組>[,<群組>]` 或 `play:<playbook>` 展開成主機；錯時記錯、回 None"""
        kind, _, val = str(spec or '').partition(':')
        if kind not in HOST_KINDS or not val:
            self.err(rule, '%s：hosts「%s」要寫成 group:<群組> 或 play:<playbook>' % (where, spec))
            return None
        if kind == 'group':
            return self.group_hosts(val.split(','), rule, where)
        path = self.d / val
        if not path.is_file():
            self.err(rule, '%s：hosts 的 playbook %s 不存在' % (where, val))
            return None
        try:
            names = play_hosts(path)
        except LockError as e:
            self.err('C5', '%s：%s' % (where, e))
            return None
        return self.group_hosts(names, rule, where)

    # I6
    def load_ports(self):
        """所有來源的 group_vars/all.yml 疊起來：{變數: 值}；defined：{變數: [來源]}"""
        self.ports, self.port_sources = {}, {}
        for src in self.sources:
            f = src / 'group_vars' / 'all.yml'
            if not f.is_file():
                continue
            try:
                data = read_yaml(f) or {}
            except LockError as e:
                self.err('I6', str(e))
                continue
            for k, v in data.items():
                self.ports[k] = v
                self.port_sources.setdefault(k, []).append(str(src.relative_to(self.d)))

    def port_value(self, name, where, rule='I6'):
        """I6：埠變數只定義一處；回傳字串或 None（記錯）。rule 是找不到時的代號（verify 的 service 是 N6）"""
        srcs = self.port_sources.get(name, [])
        if not srcs or self.ports.get(name) in (None, ''):
            self.err(rule, '%s：變數 %s 不在任何 inventory 來源的 group_vars/all.yml' % (where, name))
            return None
        if len(srcs) > 1:
            if name not in self.port_dup_reported:
                self.port_dup_reported.add(name)
                self.err('I6', '%s：變數 %s 在 %s 都有定義，只能定義一處（ansible 用後疊的那份，文件可能讀到另一份）' % (
                    where, name, '、'.join(srcs)))
            return None
        return str(self.ports[name])

    # ── C1–C13 ──
    def component_field_errors(self, raw):
        if not isinstance(raw, list) or not raw:
            self.err('C1', 'project.yaml 要有 components 清單')
            return
        ids, locks = set(), {}
        for i, c in enumerate(raw):
            if not isinstance(c, dict):
                self.err('C1', 'components 第 %d 筆不是 mapping' % (i + 1))
                continue
            cid = c.get('id') or '第 %d 筆' % (i + 1)
            where = 'component %s' % cid
            extra = set(c) - COMPONENT_KEYS
            if extra:
                self.err('C1', '%s：不認得的欄位 %s' % (where, '、'.join(sorted(extra))))
            for k in ('id', 'name', 'type', 'version_from'):
                if c.get(k) in (None, '', []):
                    self.err('C1', '%s：缺 %s' % (where, k))
            if cid in ids:
                self.err('C1', '%s：id 重複' % where)
            ids.add(cid)
            if c.get('type') and c['type'] not in TYPES:
                self.err('C2', '%s：type %r 只能是 %s' % (where, c['type'], '、'.join(TYPES)))
            table = c.get('table', True)
            if table not in (True, False):
                self.err('C3', '%s：table 只能寫 false' % where)
            if table is False:
                for k in ('hosts', 'verify', 'plan_name', 'evidence'):
                    if k in c:
                        self.err('C3', '%s：table: false 不進軟體表，不寫 %s' % (where, k))
                if c.get('version_from') == RECORD:
                    self.err('C3', '%s：table: false 要有鎖定處，不能是 record' % where)
            else:
                for k in ('hosts', 'verify'):
                    if not c.get(k):
                        self.err('C3', '%s：缺 %s' % (where, k))
                v = c.get('verify')
                if v and (not isinstance(v, dict) or set(v) != {'playbook', 'step'} or not all(v.values())):
                    self.err('C12', '%s：verify 要寫 {playbook, step}' % where)
            vf = c.get('version_from')
            if vf in (None, '', [], RECORD):
                continue
            for lock in vf if isinstance(vf, list) else [vf]:
                if not isinstance(lock, dict):
                    self.err('C6', '%s：version_from 要是 record、鎖定處或鎖定處的清單' % where)
                    continue
                lid = lock.get('id') or '?'
                extra = set(lock) - LOCK_KEYS
                if extra:
                    self.err('C7', '%s 的鎖定處 %s：不認得的欄位 %s' % (where, lid, '、'.join(sorted(extra))))
                for k in ('id', 'file', 'var', 'format'):
                    if not lock.get(k):
                        self.err('C7', '%s 的鎖定處 %s：缺 %s' % (where, lid, k))
                if lock.get('format') and lock['format'] not in FORMATS:
                    self.err('C9', '%s 的鎖定處 %s：format %r 只能是 %s' % (where, lid, lock['format'], '、'.join(FORMATS)))
                if ('list' in lock) != ('item' in lock) or ('list' in lock and 'parent' in lock):
                    self.err('C7', '%s 的鎖定處 %s：list 與 item 要一起寫，且不和 parent 同時寫' % (where, lid))
                same = {k: x for k, x in lock.items() if k != 'label'}
                if lock.get('id') in locks and locks[lock['id']] != same:
                    self.err('C7', '%s 的鎖定處 %s：同一個 id 有兩種寫法（label 以外要一字不差，用 YAML 錨點）' % (where, lid))
                locks.setdefault(lock.get('id'), same)

    def build_components(self):
        """components 的設定層（不含紀錄）：[(原始 dict, 輸出 dict, 主機清單或 None)]"""
        raw = self.doc.get('components')
        before = len(self.errors)
        self.component_field_errors(raw)
        if len(self.errors) > before:
            return []
        out, seen, lock_cache = [], {}, {}
        for c in raw:
            where = 'component %s' % c['id']
            item = {'id': c['id'], 'name': c['name']}
            if c.get('plan_name'):
                item['plan_name'] = c['plan_name']
            item['type'] = c['type']
            item['in_table'] = c.get('table', True) is not False
            vf = c['version_from']
            if vf != RECORD:
                versions = []
                for lock in vf if isinstance(vf, list) else [vf]:
                    if lock['id'] not in lock_cache:
                        try:
                            lock_cache[lock['id']] = read_lock(lock, self.d)
                        except LockError as e:
                            self.err('C9' if isinstance(e, FormatError) else 'C8', '%s：%s' % (where, e))
                            lock_cache[lock['id']] = None
                    got = lock_cache[lock['id']]
                    if got:
                        v = {'version': got['version']}
                        if lock.get('label'):
                            v['label'] = lock['label']
                        v['full'], v['lock'] = got['full'], lock['id']
                        versions.append(v)
                item['versions'] = versions
            hosts = None
            if item['in_table']:
                hosts = self.hosts_spec(c['hosts'], 'C4', where)
                pb, step = c['verify']['playbook'], c['verify']['step']
                if not (self.d / pb).is_file():
                    self.err('C12', '%s：verify 的 playbook %s 不存在' % (where, pb))
                elif not step_found(step, step_names(pb, self.d), allow_vars=True):
                    self.err('C12', '%s：%s（含它 include、import 的檔與 role）找不到步驟「%s」（改名了？project.yaml 照新名稱改）' % (
                        where, pb, step))
                key = (str(c['hosts']), pb, step)
                if key in seen:
                    self.err('C13', '%s：hosts 與 verify 都跟 component %s 一樣（角色寫重了？）' % (where, seen[key]))
                seen.setdefault(key, c['id'])
            out.append((c, item, hosts))
        return out

    # ── G1、N1–N7 ──
    def label(self, name):
        return (self.doc.get('group_labels') or {}).get(name)

    def build_links(self):
        doc = self.doc
        shape = _shape_errors(doc)
        for rule, e in shape:
            self.err(rule, e)
        if shape:
            return []
        errs = links_lib.check_doc(doc)
        for e in errs:
            self.err(_check_doc_rule(e), 'project.yaml ' + e)
        if errs:
            return []
        out, seen = [], set()
        for link in doc['links']:
            lid = link['id']
            where = 'link %s' % lid
            frm = links_lib.as_list(link['from'])
            ends = [g for g in frm if g != links_lib.ANY] + ([] if link['to'] == links_lib.SELF else [link['to']])
            for g in ends:
                self.group_hosts([g], 'N2', where)
                if not self.label(g):
                    self.err('G1', '%s：group_labels 沒有「%s」（網路需求與測試規劃的句子要寫它的中文名稱）' % (where, g))
            if frm == [links_lib.ANY] and not self.label(links_lib.ANY):
                self.err('G1', '%s：from 是 any，group_labels 要有 any（不限來源的中文名稱）' % where)
            ports = []
            for p in links_lib.as_list(link['port']):
                if isinstance(p, int):
                    ports.append(str(p))
                else:
                    v = self.port_value(p, where)
                    if v is not None:
                        ports.append(v)
            w = link.get('firewall')
            if w:
                if not (self.d / w).is_file():
                    self.err('N4', '%s：firewall 的 playbook %s 不存在' % (where, w))
                elif not writes_ufw(w, self.d):
                    self.err('N4', '%s：%s（含它 include 的檔）沒有 `ufw_writer: %s`，這條不會有人放行' % (where, w, w))
            checks = []
            for v in link.get('verify') or []:
                vwhere = '%s 的 verify「%s」' % (where, v['service'])
                service = v['service']
                for f in _fields(v['service']):
                    val = self.port_value(f, vwhere, 'N6')
                    service = service.replace('{%s}' % f, val if val is not None else '{%s}' % f)
                hosts = self.hosts_spec(v['hosts'], 'N5', vwhere)
                pb, step = v['playbook'], v['step']
                if not (self.d / pb).is_file():
                    self.err('N5', '%s：playbook %s 不存在' % (vwhere, pb))
                elif not step_found(step, step_names(pb, self.d), allow_vars=False):
                    self.err('N5', '%s：%s（含它 include 的檔）找不到步驟「%s」（名稱含 {{ }} 的步驟不能用；改名了就照新名稱改 project.yaml）' % (
                        vwhere, pb, step))
                key = (v['service'], str(v['hosts']))
                if key in seen:
                    self.err('N7', '%s：同一服務、同一主機範圍列了兩次' % vwhere)
                seen.add(key)
                checks.append((v, service, hosts))
            out.append((link, ports, checks))
        return out

    # ── E1–E3：evidence ──
    def build_evidence(self):
        """[(原始 dict, 主機清單)]；project.yaml 沒有 evidence 時回 None"""
        if 'evidence' not in self.doc:
            return None
        raw = self.doc['evidence']
        if not isinstance(raw, list):
            self.err('E1', 'project.yaml 的 evidence 要是清單')
            return []
        out, ids = [], set()
        for i, e in enumerate(raw):
            if not isinstance(e, dict):
                self.err('E1', 'evidence 第 %d 項不是 mapping' % (i + 1))
                continue
            where = 'evidence %s' % (e.get('id') or '第 %d 項' % (i + 1))
            bad = False
            extra = set(e) - set(EVIDENCE_KEYS)
            if extra:
                self.err('E1', '%s：不認得的欄位 %s' % (where, '、'.join(sorted(extra))))
                bad = True
            for k in EVIDENCE_KEYS:
                if not isinstance(e.get(k), str) or not e[k]:
                    self.err('E1', '%s：缺 %s（要是字串）' % (where, k))
                    bad = True
            if isinstance(e.get('id'), str):
                if e['id'] in ids:
                    self.err('E1', '%s：id 重複' % where)
                    bad = True
                ids.add(e['id'])
            if bad:
                continue
            hosts = self.hosts_spec(e['hosts'], 'E2', where)
            if not (self.d / e['playbook']).is_file():
                self.err('E3', '%s：playbook %s 不存在' % (where, e['playbook']))
            elif not step_found(e['step'], step_names(e['playbook'], self.d), allow_vars=True):
                self.err('E3', '%s：%s（含它 include、import 的檔與 role）找不到步驟「%s」（改名了？project.yaml 照新名稱改）' % (
                    where, e['playbook'], e['step']))
            out.append((e, hosts))
        return out

    def component_evidence_errors(self, comps, evidence):
        """E7：components[].evidence 寫 {id, line}，id 在 evidence 裡，line 是正規式"""
        ids = {e['id'] for e, _ in evidence or []}
        for c, item, _ in comps:
            if 'evidence' not in c:
                continue
            where = 'component %s' % c['id']
            ev = c['evidence']
            if not isinstance(ev, dict) or set(ev) != COMPONENT_EVIDENCE_KEYS or not all(
                    isinstance(v, str) and v for v in ev.values()):
                self.err('E7', '%s：evidence 要寫 {id, line}，兩個都是字串' % where)
                continue
            if ev['id'] not in ids:
                self.err('E7', '%s：evidence 的 id %s 不在 project.yaml 的 evidence 裡' % (where, ev['id']))
            try:
                re.compile(ev['line'].replace(VERSION_FIELD, re.escape('0.0.0')))
            except re.error as e:
                self.err('E7', '%s：evidence 的 line「%s」不是正規式（%s）' % (where, ev['line'], e))
            if VERSION_FIELD in ev['line'] and c['version_from'] != RECORD and len(item.get('versions', [])) > 1:
                self.err('E7', '%s：evidence 的 line 有 {version}，元件卻有 %d 個鎖定處，不知道代哪一個' % (
                    where, len(item['versions'])))

    # I7
    def check_ips(self):
        ips = set(self.hosts.values())
        skip = [str(s.relative_to(self.d)) + '/' for s in self.sources if self.d in s.parents]
        proc = subprocess.run(['git', 'ls-files', '-z'], cwd=self.d, capture_output=True)
        if proc.returncode != 0:
            self.err('I7', '%s 不在 git repo 裡，查不了受版控的檔：%s' % (self.d, proc.stderr.decode('utf-8', 'replace').strip()))
            return
        for rel in proc.stdout.decode('utf-8').split('\0'):
            if not rel or rel.startswith(tuple(skip)):
                continue
            f = self.d / rel
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                found = sorted(set(IP_RE.findall(line)) & ips)
                if found:
                    self.err('I7', 'ansible/%s:%d：出現 inventory 的 IP %s，改用主機名或執行時從 inventory 讀（IP 只寫在 inventory 來源）' % (
                        rel, n, '、'.join(found)))

    # ── 紀錄（V1–V8） ──
    def build(self):
        """回傳部署事實的 dict；有錯誤訊息的規則違反時回 None（看 self.errors）"""
        if not self.load_project() or not self.load_inventory():
            return None
        self.load_ports()
        comps = self.build_components()
        links = self.build_links()
        evidence = self.build_evidence()
        self.component_evidence_errors(comps, evidence)
        self.check_ips()
        if self.errors:
            return None
        try:
            runs = load_runs(self.results_dir) if self.results_dir.is_dir() else []
        except LockError as e:
            self.err('V6', str(e))
            return None
        if not runs:
            print('注意：%s 沒有任何紀錄，每一項驗證都是 null' % self.results_dir, file=sys.stderr)
        rec = Records(runs)

        components, listed = [], {}
        for c, item, hosts in comps:
            if item['in_table']:
                pb, step = c['verify']['playbook'], c['verify']['step']
                listed.setdefault((pb, step), set()).update(hosts)
                item['instances'] = [self.component_instance(rec, c, h, pb, step) for h in hosts]
            components.append(item)

        out_links = []
        for link, ports, checks in links:
            frm = links_lib.as_list(link['from'])
            o = {'id': link['id'],
                 'from': [{'any': True, 'label': self.label(links_lib.ANY)}] if frm == [links_lib.ANY] else [{'group': g} for g in frm],
                 'to': {'self': True} if link['to'] == links_lib.SELF else {'group': link['to']},
                 'ports': ports, 'protocol': link['protocol'], 'purpose': link['purpose'],
                 'firewalled': bool(link.get('firewall')), 'checks': []}
            for v, service, hosts in checks:
                c = {'service': service}
                if v.get('note'):
                    c['note'] = v['note']
                c['instances'] = [self.link_instance(rec, h, v['playbook'], v['step']) for h in hosts]
                o['checks'].append(c)
            out_links.append(o)

        problems = []
        # V8：正式裝過而 components 沒展開到的主機（例：用 -e 多裝了一台），不靜默漏列
        for (pb, host), run in sorted(rec.latest_run.items()):
            for (vpb, step), hosts in listed.items():
                if vpb == pb and host not in hosts and any(r['host'] == host and r['step'] == step for r in run['steps']):
                    problems.append({'message': '%s 在 %s 有「%s」的正式紀錄，project.yaml 的 components 卻沒展開到這台' % (host, pb, step)})

        out_evidence = None
        if evidence is not None:
            out_evidence = []
            for e, hosts in evidence:
                for h in hosts:
                    entry, problem = self.evidence_entry(rec, e, h)
                    if problem:
                        problems.append({'message': 'evidence %s 的 %s：%s，部署事實.json 沒有這筆' % (e['id'], h, problem)})
                    else:
                        out_evidence.append(entry)
            problems += self.compare_evidence(comps, out_evidence)

        groups = []
        for name, members in self.groups.items():
            if name in GROUPS_SKIP or not members:
                continue
            g = {'name': name}
            if self.label(name):
                g['label'] = self.label(name)
            g['hosts'] = members
            groups.append(g)
        doc = {
            'format_version': FORMAT_VERSION,
            'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'hosts': [{'name': h, 'ip': ip} for h, ip in self.hosts.items()],
            'groups': groups,
            'components': components,
            'links': out_links,
        }
        if out_evidence is not None:
            doc['evidence'] = out_evidence
        doc['problems'] = problems
        return doc

    @staticmethod
    def evidence_entry(rec, e, host):
        """E5：一個（evidence、主機）的那筆，採用同 V7。(entry, None) 或 (None, problem)"""
        r, problem = rec.latest(e['playbook'], host, e['step'])
        if problem is None:
            if r.get('report_error'):
                problem = 'report_* 代入失敗（%s）' % r['time']
            elif 'report_hosts' in r and host not in r['report_hosts']:
                problem = 'report_hosts %s 不含這台（%s）' % (r['report_hosts'], r['time'])
            elif r['result'] not in RESULT:
                problem = '最近一次正式執行是 %s（%s）' % (r['result'], r['time'])
            elif 'command' not in r or 'output' not in r:
                problem = '紀錄沒有 %s（%s），步驟要寫 report_command 與 report_output' % (
                    '、'.join(k for k in ('command', 'output') if k not in r), r['time'])
        if problem:
            return None, problem
        return {'id': e['id'], 'title': e['title'].replace('{host}', host), 'host': host,
                'command': r['command'], 'output': r['output'], 'time': r['time']}, None

    @staticmethod
    def compare_evidence(comps, entries):
        """E8：check 是 pass 的實例，同主機那筆 evidence 的 output 要有一行符合 line。回傳 problems"""
        by_key = {(x['id'], x['host']): x for x in entries}
        out = []
        for c, item, _ in comps:
            ev = c.get('evidence')
            if not ev:
                continue
            for inst in item.get('instances', []):
                if not inst['check'] or inst['check']['result'] != 'pass':
                    continue
                host = inst['host']
                where = 'component %s 的 %s（evidence %s，line「%s」）' % (c['id'], host, ev['id'], ev['line'])
                pattern = ev['line']
                if VERSION_FIELD in pattern:
                    versions = inst.get('versions') or item.get('versions') or []
                    if len(versions) != 1:
                        out.append({'message': '%s：沒有版本可代入 {version}，對照不了' % where})
                        continue
                    # 版本代入前跳脫：v2026.11.26.2 的 . 不跳脫會配到任何字
                    pattern = pattern.replace(VERSION_FIELD, re.escape(versions[0]['version']))
                entry = by_key.get((ev['id'], host))
                if entry is None:
                    out.append({'message': '%s：元件判 pass，evidence 沒有這台的那筆，對照不了' % where})
                elif not any(re.search(pattern, line) for line in entry['output'].splitlines()):
                    out.append({'message': '%s：元件判 pass，指令輸出卻沒有一行符合（%s）' % (where, entry['time'])})
        return out

    @staticmethod
    def component_instance(rec, c, host, pb, step):
        r, problem = rec.latest(pb, host, step)
        inst = {'host': host}
        if c['version_from'] == RECORD and r and r.get('version'):
            # C11：版本取部署時從主機或容器讀到的值；最近一次是 skipped 時也照記（計畫書要印版本）
            inst['versions'] = [{'version': r['version']}]
        if problem is None:
            if 'report_hosts' in r and host not in r['report_hosts']:
                # V7：步驟說它不適用這台，跟 project.yaml 的主機範圍不一致
                problem = 'report_hosts %s 不含這台，跟 project.yaml 的主機範圍不一致（%s）' % (r['report_hosts'], r['time'])
            elif r['result'] not in RESULT:
                problem = '最近一次正式執行是 %s（%s）' % (r['result'], r['time'])
        if problem:
            inst['check'], inst['problem'] = None, problem
            return inst
        inst['check'] = {'result': RESULT[r['result']], 'time': r['time']}
        if c['version_from'] == RECORD and 'versions' not in inst:
            inst['problem'] = ('紀錄的 report_version 代入失敗（%s）' if r.get('report_error') else '紀錄沒有 version（%s）') % r['time']
        elif r.get('report_error'):
            # V3：代入失敗，結果照記，problem 說明
            inst['problem'] = '紀錄的 report_* 代入失敗（%s）' % r['time']
        return inst

    @staticmethod
    def link_instance(rec, host, pb, step):
        r, problem = rec.latest(pb, host, step)
        if problem is None:
            if r.get('report_error'):
                problem = 'report_service／report_method／report_hosts 代入失敗（%s）' % r['time']
            elif not r.get('method'):
                problem = '紀錄沒有 report_method（%s），步驟拿掉了報告文字，或挑到同名的另一步' % r['time']
            elif 'report_hosts' in r and host not in r['report_hosts']:
                problem = 'report_hosts %s 不含這台，跟 project.yaml 的主機範圍不一致（%s）' % (r['report_hosts'], r['time'])
            elif r['result'] not in RESULT:
                problem = '最近一次正式執行是 %s（%s）' % (r['result'], r['time'])
        if problem:
            return {'host': host, 'check': None, 'problem': problem}
        check = {'result': RESULT[r['result']], 'time': r['time'], 'method': r['method']}
        if r.get('service'):
            check['service'] = r['service']
        return {'host': host, 'check': check}


def main(argv=None):
    ap = argparse.ArgumentParser(description='讀項目的 ansible/，寫出 部署事實.json')
    ap.add_argument('--ansible-dir', default='ansible', help='項目的 ansible/ 目錄（預設 ./ansible）')
    ap.add_argument('--results', help='驗證紀錄的目錄（預設 <ansible-dir>/logs/results）')
    ap.add_argument('-o', '--out', required=True, help='寫出的 部署事實.json')
    args = ap.parse_args(argv)
    try:
        facts = Facts(args.ansible_dir, args.results)
        doc = facts.build()
    except Stop as e:
        print(str(e), file=sys.stderr)
        return 2
    if doc is None:
        for e in facts.errors:
            print('錯：' + e, file=sys.stderr)
        print('%d 項不符合 ansible-facts 的規則（代號見 infra-kit ansible-facts/README.md），沒有寫 %s' % (len(facts.errors), args.out),
              file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    nulls = sum(1 for c in doc['components'] for i in c.get('instances', []) if i['check'] is None) + \
        sum(1 for l in doc['links'] for c in l['checks'] for i in c['instances'] if i['check'] is None)
    print('寫出 %s：%d 台主機、%d 個元件、%d 條連線；%d 項沒有結果、%d 則 problems' % (
        args.out, len(doc['hosts']), len(doc['components']), len(doc['links']), nulls, len(doc['problems'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
