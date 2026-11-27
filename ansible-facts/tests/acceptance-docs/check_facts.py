"""檢查 部署事實.json：照 schemas/部署事實.schema.json 驗，再驗 schema 表達不了的規則（docs/部署事實格式.md F1–F12）。

用法：python check_facts.py <部署事實.json>
全符合時結束碼 0；不符合時每項一行「<欄位路徑>：<代號> <哪裡不對>；<怎麼改>」，結束碼 1。

schema 有錯時只報 schema 的錯，不跑 F 規則：F 規則假設結構是對的，結構錯了會報出一串連帶的錯。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent
SCHEMA = ROOT / "schemas" / "部署事實.schema.json"
FORMAT_VERSION = 1

# F9：服務欄裡的埠號；F10：結果文字裡的 IPv4（後面可接句點，不能接「.數字」）
PORT_RE = re.compile(r"(?<!\d)\d{2,5}(?!\d)")
IP_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)")
LOCAL_IP_PREFIXES = ("127.", "0.0.0.0")


class Problem(tuple):
    """(路徑, 代號, 說明)"""

    def __new__(cls, path: str, code: str, message: str):
        return super().__new__(cls, (path, code, message))

    def __str__(self) -> str:
        return "%s：%s %s" % self


def _path(parts) -> str:
    out = "$"
    for p in parts:
        out += "[%d]" % p if isinstance(p, int) else ".%s" % p
    return out


def schema_problems(doc) -> list[Problem]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    # ipv4 由標準庫驗；date-time 沒裝額外套件時不驗格式，靠 schema 的 pattern
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)
    out = []
    for e in sorted(validator.iter_errors(doc), key=lambda e: list(map(str, e.absolute_path))):
        out.append(Problem(_path(e.absolute_path), "schema", "%s；照 schemas/部署事實.schema.json 改" % e.message))
    return out


def _dups(items, key):
    """[(索引, 值)]：值第二次以後出現的那幾筆"""
    seen, out = set(), []
    for i, it in enumerate(items):
        v = key(it)
        if v in seen:
            out.append((i, v))
        seen.add(v)
    return out


def _check_instances(doc):
    """所有 instance：[(路徑, instance, 是不是連線表的)]"""
    for i, c in enumerate(doc["components"]):
        for j, inst in enumerate(c.get("instances") or []):
            yield "$.components[%d].instances[%d]" % (i, j), inst, False
    for i, link in enumerate(doc["links"]):
        for j, chk in enumerate(link["checks"]):
            for k, inst in enumerate(chk["instances"]):
                yield "$.links[%d].checks[%d].instances[%d]" % (i, j, k), inst, True


def rule_problems(doc) -> list[Problem]:
    out = []
    hosts = {h["name"] for h in doc["hosts"]}
    ips = {h["ip"] for h in doc["hosts"]}
    groups = {g["name"]: g for g in doc["groups"]}

    # F1：主機名、IP 不重複
    for i, v in _dups(doc["hosts"], lambda h: h["name"]):
        out.append(Problem("$.hosts[%d].name" % i, "F1", "主機 %s 列了兩次；同一台只列一筆" % v))
    for i, v in _dups(doc["hosts"], lambda h: h["ip"]):
        out.append(Problem("$.hosts[%d].ip" % i, "F1", "IP %s 有兩台主機；查部署設定哪一台寫錯" % v))

    # F2：群組名不重複；群組成員與 instance 的主機都在 hosts
    for i, v in _dups(doc["groups"], lambda g: g["name"]):
        out.append(Problem("$.groups[%d].name" % i, "F2", "群組 %s 列了兩次；合成一筆" % v))
    for i, g in enumerate(doc["groups"]):
        for j, h in enumerate(g["hosts"]):
            if h not in hosts:
                out.append(Problem("$.groups[%d].hosts[%d]" % (i, j), "F2", "主機 %s 不在 hosts；補進 hosts 或從群組拿掉" % h))
    for path, inst, _ in _check_instances(doc):
        if inst["host"] not in hosts:
            out.append(Problem(path + ".host", "F2", "主機 %s 不在 hosts；補進 hosts（含 IP）" % inst["host"]))

    # F3：連線端點的群組存在，而且有 label（本機服務的來源也要：測試規劃連線項目的句子用它）
    for i, link in enumerate(doc["links"]):
        ends = [("from[%d]" % j, e) for j, e in enumerate(link["from"])] + [("to", link["to"])]
        for where, e in ends:
            name = e.get("group")
            if name is None:
                continue
            path = "$.links[%d].%s.group" % (i, where)
            if name not in groups:
                out.append(Problem(path, "F3", "群組 %s 不在 groups；補進 groups" % name))
            elif not groups[name].get("label"):
                out.append(Problem(path, "F3", "群組 %s 沒有 label，網路需求與測試規劃的句子缺主詞；groups 裡給它中文名稱" % name))

    # F4：id 不重複
    for sec in ("components", "links"):
        for i, v in _dups(doc[sec], lambda x: x["id"]):
            out.append(Problem("$.%s[%d].id" % (sec, i), "F4", "id %s 重複；改成唯一的名稱" % v))

    for i, c in enumerate(doc["components"]):
        path = "$.components[%d]" % i
        insts = c.get("instances") or []
        if c["in_table"]:
            # F5：進表的元件有 instance；沒有版本的那台要寫原因
            if not insts:
                out.append(Problem(path + ".instances", "F5", "進軟體表的元件沒有 instances；列出裝在哪幾台，或 in_table 改 false"))
            for j, inst in enumerate(insts):
                if not inst.get("versions") and not c.get("versions") and not inst.get("problem"):
                    out.append(Problem("%s.instances[%d].problem" % (path, j), "F5",
                                       "這台沒有版本，元件也沒有 versions，卻沒寫 problem；補上版本，或寫為什麼沒有"))
        else:
            # F6：不進表的元件只給版本
            if "instances" in c:
                out.append(Problem(path + ".instances", "F6", "in_table 是 false 的元件不寫 instances"))
            if not c.get("versions"):
                out.append(Problem(path + ".versions", "F6", "in_table 是 false 的元件要有 versions（給版本清單比對）"))
        # F12：同一元件主機不重複
        for j, v in _dups(insts, lambda x: x["host"]):
            out.append(Problem("%s.instances[%d].host" % (path, j), "F12", "主機 %s 列了兩次；同一台只列一筆" % v))

    # F7：沒有結果的要說原因；有結果、也有版本的不寫原因（元件的 instance 沒有版本時 F5 管）
    component_versions = {}
    for i, c in enumerate(doc["components"]):
        for j, inst in enumerate(c.get("instances") or []):
            component_versions["$.components[%d].instances[%d]" % (i, j)] = bool(inst.get("versions") or c.get("versions"))
    for path, inst, _ in _check_instances(doc):
        has_version = component_versions.get(path, True)
        if inst["check"] is None and not inst.get("problem"):
            out.append(Problem(path + ".problem", "F7", "check 是 null 卻沒寫 problem；寫為什麼沒有結果"))
        elif inst["check"] is not None and has_version and "problem" in inst:
            out.append(Problem(path + ".problem", "F7", "有結果也有版本卻寫了 problem；拿掉 problem，或結果不能用時 check 改 null"))

    seen = set()
    for i, link in enumerate(doc["links"]):
        for j, chk in enumerate(link["checks"]):
            for k, inst in enumerate(chk["instances"]):
                path = "$.links[%d].checks[%d].instances[%d]" % (i, j, k)
                # F11：同一台的同一個服務只一列（整張連線表）
                key = (inst["host"], chk["service"])
                if key in seen:
                    out.append(Problem(path + ".host", "F11", "%s 的「%s」在連線表出現兩次；每台每個服務只挑一項驗證" % key))
                seen.add(key)
                rec = inst["check"]
                if rec is None:
                    continue
                # F8：連線表的結果要有方法
                if not rec.get("method"):
                    out.append(Problem(path + ".check.method", "F8", "連線表的結果沒有 method，方法欄會空白；補上怎麼驗的"))
                text = "%s %s" % (rec.get("service", ""), rec.get("method", ""))
                # F9：服務欄的埠號要出現在結果文字裡
                for port in PORT_RE.findall(chk["service"]):
                    if not re.search(r"(?<!\d)%s(?!\d)" % port, text):
                        out.append(Problem(path + ".check", "F9",
                                           "服務「%s」的 %s 不在結果文字裡；埠改了就重跑驗證，或改服務欄" % (chk["service"], port)))
                # F10：結果文字裡的 IP 要是 hosts 的 IP
                for ip in sorted(set(IP_RE.findall(text)) - ips):
                    if not ip.startswith(LOCAL_IP_PREFIXES):
                        out.append(Problem(path + ".check", "F10", "結果文字的 IP %s 不在 hosts；IP 改了就重跑驗證" % ip))
    return out


def check(doc) -> list[Problem]:
    if not isinstance(doc, dict) or doc.get("format_version") != FORMAT_VERSION:
        got = doc.get("format_version") if isinstance(doc, dict) else None
        return [Problem("$.format_version", "schema", "格式版本是 %r，本 repo 只認 %d；照本 repo 的格式重新產生" % (got, FORMAT_VERSION))]
    problems = schema_problems(doc)
    return problems or rule_problems(doc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="檢查 部署事實.json（格式見 docs/部署事實格式.md）")
    ap.add_argument("facts", type=Path)
    args = ap.parse_args(argv)
    try:
        doc = json.loads(args.facts.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("%s 不存在" % args.facts, file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print("%s 不是 JSON：第 %d 行第 %d 欄 %s" % (args.facts, e.lineno, e.colno, e.msg), file=sys.stderr)
        return 1
    problems = check(doc)
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        print("%s：%d 項不符，格式見 docs/部署事實格式.md" % (args.facts, len(problems)), file=sys.stderr)
        return 1
    print("%s：符合部署事實格式（%d 台主機、%d 個元件、%d 條連線）" % (
        args.facts, len(doc["hosts"]), len(doc["components"]), len(doc["links"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
