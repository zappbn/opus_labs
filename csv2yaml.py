#!/usr/bin/env python3
"""
devices.csv + links.csv -> host_vars/*.yml + inventory.yml

Источник правды — две плоские таблицы:
  devices.csv: hostname,kind,site,asn,loopback,mgmt_inband
  links.csv:   device_a,iface_a,device_b,iface_b,network

links.csv опционален — если нет, P2P не генерится (только loopback + mgmt).

OOB-адрес = 192.168.100.<numeric_suffix>, см. group_vars/all.yml.
TARGET_SITES — фильтр; пустой = все сайты.
"""
import csv
import ipaddress
import re
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parent
DEVICES_CSV = LAB_ROOT / "devices.csv"
LINKS_CSV = LAB_ROOT / "links.csv"
HOST_VARS_DIR = LAB_ROOT / "host_vars"
INVENTORY_FILE = LAB_ROOT / "inventory.yml"

MGMT_SUBNET_PREFIX = "192.168.100"
TARGET_SITES = set()

HOSTNAME_ID_RE = re.compile(r'(\d+)$')


def parse_devices():
    rows = []
    with open(DEVICES_CSV, encoding="utf-8") as f:
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


def parse_links():
    """hostname -> [{name, ip, mask, peer, network}, ...]"""
    by_host = {}
    if not LINKS_CSV.exists():
        return by_host

    with open(LINKS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            net = ipaddress.ip_network(row["network"], strict=False)
            hosts = list(net.hosts())  # для /31 даст оба адреса (RFC 3021)
            ip_a, ip_b = str(hosts[0]), str(hosts[1])
            mask = str(net.netmask)
            dev_a, dev_b = row["device_a"].strip(), row["device_b"].strip()

            by_host.setdefault(dev_a, []).append({
                "name": row["iface_a"].strip(),
                "ip": ip_a, "mask": mask,
                "peer": dev_b, "network": row["network"].strip(),
            })
            by_host.setdefault(dev_b, []).append({
                "name": row["iface_b"].strip(),
                "ip": ip_b, "mask": mask,
                "peer": dev_a, "network": row["network"].strip(),
            })
    return by_host


def write_host_vars(rows, links_by_host):
    HOST_VARS_DIR.mkdir(parents=True, exist_ok=True)
    for r in rows:
        loopback_line = f"loopback_ip: {r['loopback']}\n" if r["loopback"] else ""
        p2p = sorted(links_by_host.get(r["hostname"], []), key=lambda x: x["name"])

        p2p_yml = ""
        if p2p:
            p2p_yml = "p2p_interfaces:\n"
            for iface in p2p:
                p2p_yml += (
                    f"  - name: {iface['name']}\n"
                    f"    ip: {iface['ip']}\n"
                    f"    mask: {iface['mask']}\n"
                    f"    peer: {iface['peer']}\n"
                    f"    network: {iface['network']}\n"
                )

        yml = (
            f"# Generated from devices.csv + links.csv — не править руками\n"
            f"kind: {r['kind']}\n"
            f"site: {r['site']}\n"
            f"asn: {r['asn']}\n"
            f"{loopback_line}"
            f"mgmt_inband_ip: {r['mgmt_inband']}\n"
            f"ansible_host: {r['mgmt_oob']}\n"
            f"{p2p_yml}"
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
    rows = parse_devices()
    links = parse_links()
    write_host_vars(rows, links)
    write_inventory(rows)

    for r in rows:
        n_ifaces = len(links.get(r["hostname"], []))
        suffix = f"  +{n_ifaces} P2P" if n_ifaces else ""
        print(f"  {r['hostname']:<6} kind={r['kind']:<7} site={r['site']:<12} "
              f"OOB={r['mgmt_oob']:<16}{suffix}")
    print(f"\nУстройств: {len(rows)}  |  Линков: {sum(len(v) for v in links.values())//2}")


if __name__ == "__main__":
    main()
