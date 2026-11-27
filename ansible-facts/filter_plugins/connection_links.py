"""vars/project.yaml 的 links 節的讀法：ufw 規則與部署事實都從這裡展開，playbook（filter）與 ../ansible_facts.py（import 本檔）共用

不 import ansible：轉接器直接載入本檔。
from、to 只寫 inventory 的群組名（資料庫的主庫、從庫這類角色也各是一個群組），主機照 inventory 展開。
"""

ANY = 'any'
SELF = 'self'
LINK_KEYS = {'id', 'from', 'to', 'port', 'protocol', 'purpose', 'firewall', 'ufw_comment', 'verify'}
VERIFY_KEYS = {'service', 'hosts', 'playbook', 'step', 'note'}
PROTOCOLS = ('tcp', 'udp')


class LinkError(Exception):
    pass


def as_list(v):
    return list(v) if isinstance(v, (list, tuple)) else [v]


def expand(name, groups):
    """群組名展開成主機。群組不在 inventory 或是空的就失敗：少帶 -i 或群組改名時不靜默少放行"""
    hosts = groups.get(name)
    if not hosts:
        raise LinkError('inventory 沒有群組 %s 或它沒有主機（少帶了 -i inventory/<環境>，或群組改了名）' % name)
    return list(hosts)


def port_names(doc):
    """links 用到的埠變數名（數字不算），照出現順序"""
    out = []
    for link in doc.get('links') or []:
        for p in as_list(link.get('port')):
            if isinstance(p, str) and p not in out:
                out.append(p)
    return out


def check_doc(doc):
    """欄位層的錯（不看 inventory 與變數值）。回傳錯誤訊息 list"""
    errors, ids = [], set()
    if not isinstance(doc, dict) or not isinstance(doc.get('links'), list):
        return ['project.yaml 要有 links 清單']
    for i, link in enumerate(doc['links']):
        lid = link.get('id') or '第 %d 條' % (i + 1)
        extra = set(link) - LINK_KEYS
        if extra:
            errors.append('link %s：不認得的欄位 %s' % (lid, '、'.join(sorted(extra))))
        for k in ('id', 'from', 'to', 'port', 'protocol', 'purpose'):
            if link.get(k) in (None, '', []):
                errors.append('link %s：缺 %s' % (lid, k))
        if lid in ids:
            errors.append('link %s：id 重複' % lid)
        if link.get('protocol') and link['protocol'] not in PROTOCOLS:
            errors.append('link %s：protocol %r 只能是 %s' % (lid, link['protocol'], '、'.join(PROTOCOLS)))
        ids.add(lid)
        for p in as_list(link.get('port')):
            if not (isinstance(p, int) or (isinstance(p, str) and p.isidentifier())):
                errors.append('link %s：port %r 要是變數名或數字' % (lid, p))
            elif isinstance(p, int) and link.get('to') != SELF:
                errors.append('link %s：port 寫了數字 %s，只有 self 可以；跨主機的埠要用 group_vars 的變數名' % (lid, p))
        if link.get('firewall'):
            if link.get('to') == SELF:
                errors.append('link %s：to 是 self（本機服務）不能開 firewall' % lid)
            if not link.get('ufw_comment'):
                errors.append('link %s：有 firewall 就要寫 ufw_comment（和主機上現有的規則相同）' % lid)
        elif link.get('ufw_comment'):
            errors.append('link %s：沒寫 firewall 卻有 ufw_comment' % lid)
        if ANY in as_list(link.get('from')) and len(as_list(link.get('from'))) > 1:
            errors.append('link %s：from 有 any 就不能再列群組' % lid)
        for j, v in enumerate(link.get('verify') or []):
            where = 'link %s 的 verify 第 %d 項「%s」' % (lid, j + 1, v.get('service', ''))
            extra = set(v) - VERIFY_KEYS
            if extra:
                errors.append('%s：不認得的欄位 %s' % (where, '、'.join(sorted(extra))))
            for k in ('service', 'hosts', 'playbook', 'step'):
                if not v.get(k):
                    errors.append('%s：缺 %s' % (where, k))
    return errors


def verifies(doc):
    """各 link 的 verify 攤平，照 links 順序、同一條照 verify 順序：[(link id, verify 項)]"""
    return [(link.get('id'), v) for link in doc.get('links') or [] for v in link.get('verify') or []]


def _resolve_port(p, ports, lid):
    if isinstance(p, int):
        return str(p)
    if p not in ports or ports[p] in (None, ''):
        raise LinkError('link %s：變數 %s 沒有定義（埠號寫在某一份 inventory 來源的 group_vars/all.yml）' % (lid, p))
    return str(ports[p])


def ufw_rules(doc, host, groups, ips, writer, ports):
    """這台主機在 writer 這支 playbook 要放行的 ufw 規則，照 links 順序、來源主機、埠展開

    doc：project.yaml 讀進來的 dict；groups：{群組: [主機]}；ips：{主機: IP}；ports：{變數名: 值}
    回傳 [{link, from_host, from_ip, port, comment, cmd}]。來源是本機自己的不放（db 互連只放另一台）
    """
    errors = check_doc(doc)
    if errors:
        raise LinkError('；'.join(errors))
    rules = []
    for link in doc['links']:
        if link.get('firewall') != writer or host not in expand(link['to'], groups):
            continue
        if as_list(link['from']) == [ANY]:
            sources = [(ANY, ANY)]
        else:
            sources = []
            for g in as_list(link['from']):
                for h in expand(g, groups):
                    if h != host and h not in [s[0] for s in sources]:
                        if not ips.get(h):
                            raise LinkError('link %s：%s 沒有 ansible_host' % (link['id'], h))
                        sources.append((h, str(ips[h])))
        for h, ip in sources:
            for p in as_list(link['port']):
                port = _resolve_port(p, ports, link['id'])
                comment = link['ufw_comment'].format(ip=ip, host=h)
                proto = link['protocol']
                if ip == ANY:
                    cmd = "ufw allow %s/%s comment '%s'" % (port, proto, comment)
                else:
                    cmd = "ufw allow from %s to any port %s proto %s comment '%s'" % (ip, port, proto, comment)
                rules.append({'link': link['id'], 'from_host': h, 'from_ip': ip, 'port': port, 'protocol': proto,
                              'comment': comment, 'cmd': cmd})
    return rules


def _host_ips(hostvars, hosts):
    out = {}
    for h in hosts:
        try:
            ip = hostvars[h].get('ansible_host')
        except (KeyError, AttributeError):
            ip = None
        if ip:
            out[h] = str(ip)
    return out


def ufw_link_rules(doc, host, groups, hostvars, writer, ports):
    """filter：{{ doc | ufw_link_rules(inventory_hostname, groups, hostvars, '<放行的 playbook>', ports) }}"""
    hosts = [h for g in groups.values() for h in g]
    return ufw_rules(doc, host, groups, _host_ips(hostvars, hosts), writer, ports)


def connection_port_names(doc):
    """filter：project.yaml 用到的埠變數名，給 query('vars', *names) 取值"""
    return port_names(doc)


class FilterModule:
    def filters(self):
        return {'ufw_link_rules': ufw_link_rules, 'connection_port_names': connection_port_names}
