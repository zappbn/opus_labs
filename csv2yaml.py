#!/usr/bin/env python3
"""
devices.csv -> host_vars/*.yml  +  inventory.yml

Источник правды — плоская таблица devices.csv:
    hostname,kind,site,asn,loopback,mgmt_inband

kind: router | switch
loopback: может быть пустым (для switch)

OOB-адрес вычисляется из числового суффикса имени:
    R12 -> 192.168.100.12,  SW2 -> 192.168.100.2,  SW10 -> 192.168.100.10

Инвентори группирует устройства по сайту И по типу — каждый хост попадает
в две группы (`spb` + `routers`, и т.п.). Для --limit удобно.

TARGET_SITES — фильтр; пустой set = все сайты.
"""
import csv
import re
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parent
CSV_FILE = LAB_ROOT / "devices.csv"
HOST_VARS_DIR = LAB_ROOT / "host_vars"
INVENTORY_FILE = LAB_ROOT / "inventory.yml"

MGMT_SUBNET_PREFIX = "192.168.100"      # OOB; должно совпадать с group_vars/all.yml
TARGET_SITES = set()                    # пустой = все сайты

HOSTNAME_ID_RE = re.compile(r'(\d+)$')


def parse_csv():
    """Читает devices.csv, возвращает список dict'ов с уже-разрешёнными полями."""
    rows = []
    with open(CSV_FILE, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if TARGET_SITES and row["site"] not in TARGET_SITES:
                continue
            hostname = row["hostname"].strip()
            m = HOSTNAME_ID_RE.search(hostname)
            if not m:
                print(f"  ! {hostname}: нет числового суффикса, пропускаю")
                continue
            rows.append({
                "hostname": hostname,
                "kind": row["kind"].strip(),
                "site": row["site"].strip(),
                "asn": row["asn"].strip(),
                "loopback": row.get("loopback", "").strip().split("/")[0],
                "mgmt_inband": row["mgmt_inband"].strip().split("/")[0],
                "mgmt_oob": f"{MGMT_SUBNET_PREFIX}.{int(m.group(1))}",
            })
    return rows


def write_host_vars(rows):
    HOST_VARS_DIR.mkdir(parents=True, exist_ok=True)
    for r in rows:
        loopback_line = f"loopback_ip: {r['loopback']}\n" if r["loopback"] else ""
        yml = (
            f"# Generated from devices.csv — не править руками\n"
            f"kind: {r['kind']}\n"
            f"site: {r['site']}\n"
            f"asn: {r['asn']}\n"
            f"{loopback_line}"
            f"mgmt_inband_ip: {r['mgmt_inband']}\n"
            f"ansible_host: {r['mgmt_oob']}\n"
        )
        (HOST_VARS_DIR / f"{r['hostname']}.yml").write_text(yml, encoding="utf-8")


def write_inventory(rows):
    by_site = {}
    by_kind = {"router": [], "switch": []}
    for r in rows:
        by_site.setdefault(r["site"].lower(), []).append(r["hostname"])
        by_kind.setdefault(r["kind"], []).append(r["hostname"])

    out = ["# Generated from devices.csv — не править руками", "all:", "  children:"]
    for site in sorted(by_site):
        out.append(f"    {site}:")
        out.append(f"      hosts:")
        for h in sorted(by_site[site]):
            out.append(f"        {h}:")
    KIND_TO_GROUP = {"router": "routers", "switch": "switches"}
    for kind, group_name in KIND_TO_GROUP.items():
        if not by_kind.get(kind):
            continue
        out.append(f"    {group_name}:")
        out.append(f"      hosts:")
        for h in sorted(by_kind[kind]):
            out.append(f"        {h}:")

    INVENTORY_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def main():
    rows = parse_csv()
    write_host_vars(rows)
    write_inventory(rows)

    for r in rows:
        print(f"  {r['hostname']:<6} kind={r['kind']:<7} site={r['site']:<12} "
              f"OOB={r['mgmt_oob']}")
    sites = sorted(TARGET_SITES) if TARGET_SITES else "all"
    print(f"\nSites: {sites}  | устройств: {len(rows)}")
    print(f"Записано:  host_vars/*.yml ({len(rows)})  +  inventory.yml")


if __name__ == "__main__":
    main()
