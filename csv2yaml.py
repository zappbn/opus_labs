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
SWITCH_PORTS_CSV = LAB_ROOT / "switch_ports.csv"
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


def parse_switch_ports():
    """device -> {ports: [...], port_channels: [...]}.

    Группирует LAG-членов в Port-channel'ы. Возвращает структуру готовую для
    подстановки в host_vars свитчей.
    """
    by_device = {}
    if not SWITCH_PORTS_CSV.exists():
        return by_device

    with open(SWITCH_PORTS_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # 1) Собираем LAG-членов отдельно, чтоб понять состав каждого Port-channel
    lag_members = {}   # (device, cg_id) -> [row, ...]
    other_rows = {}    # device -> [row, ...]
    for row in rows:
        device = row["device"].strip()
        cg = row.get("channel_group", "").strip()
        if cg:
            lag_members.setdefault((device, int(cg)), []).append(row)
        else:
            other_rows.setdefault(device, []).append(row)

    # 2) Соберём Port-channel-определения (одно на пару device+channel_group)
    port_channels_by_device = {}
    for (device, cg_id), members in lag_members.items():
        # Берём настройки trunk/mode из первого члена (они должны совпадать)
        first = members[0]
        pc = {
            "id": cg_id,
            "mode": first["mode"].strip(),
            "allowed_vlans": first.get("allowed_vlans", "").strip().replace(":", ","),
            "description": f"LAG {cg_id} (members: {', '.join(m['iface'] for m in members)})",
        }
        port_channels_by_device.setdefault(device, []).append(pc)

    # 3) Соберём ports на устройство (физические интерфейсы)
    for device in set(list(other_rows.keys()) + [d for d, _ in lag_members.keys()]):
        ports = []
        # Сначала "обычные" порты
        for row in other_rows.get(device, []):
            port = {
                "name": row["iface"].strip(),
                "mode": row["mode"].strip(),
                "description": row.get("description", "").strip(),
            }
            if port["mode"] == "access":
                port["vlan"] = row["vlan"].strip()
            elif port["mode"] == "trunk":
                port["allowed_vlans"] = row.get("allowed_vlans", "").strip().replace(":", ",")
            ports.append(port)
        # Потом LAG-члены (для них в физическом интерфейсе только channel-group)
        for (dev, cg_id), members in sorted(lag_members.items()):
            if dev != device:
                continue
            for m in members:
                # IOL/IOU L2 требует чтоб mode/encap/allowed_vlans совпадали
                # между физическим членом и Port-channel'ом - иначе LACP отказывается bundle.
                ports.append({
                    "name": m["iface"].strip(),
                    "channel_group": cg_id,
                    "allowed_vlans": m.get("allowed_vlans", "").strip().replace(":", ","),
                    "description": m.get("description", "").strip(),
                })
        # Сортируем по имени для детерминированного diff
        ports.sort(key=lambda x: x["name"])
        by_device[device] = {
            "ports": ports,
            "port_channels": sorted(port_channels_by_device.get(device, []),
                                    key=lambda x: x["id"]),
        }

    return by_device


def write_host_vars(rows, links_by_host, switch_data):
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

        # Switch-only секции (если устройство свитч и для него есть данные)
        sw_yml = ""
        if r["kind"] == "switch" and r["hostname"] in switch_data:
            sd = switch_data[r["hostname"]]
            if sd["port_channels"]:
                sw_yml += "port_channels:\n"
                for pc in sd["port_channels"]:
                    sw_yml += f"  - id: {pc['id']}\n"
                    sw_yml += f"    mode: {pc['mode']}\n"
                    if pc.get("allowed_vlans"):
                        sw_yml += f"    allowed_vlans: \"{pc['allowed_vlans']}\"\n"
                    sw_yml += f"    description: \"{pc['description']}\"\n"
            if sd["ports"]:
                sw_yml += "switch_ports:\n"
                for p in sd["ports"]:
                    sw_yml += f"  - name: {p['name']}\n"
                    if "channel_group" in p:
                        sw_yml += f"    channel_group: {p['channel_group']}\n"
                        if p.get("allowed_vlans"):
                            sw_yml += f"    allowed_vlans: \"{p['allowed_vlans']}\"\n"
                    else:
                        sw_yml += f"    mode: {p['mode']}\n"
                        if p["mode"] == "access" and p.get("vlan"):
                            sw_yml += f"    vlan: {p['vlan']}\n"
                        if p["mode"] == "trunk" and p.get("allowed_vlans"):
                            sw_yml += f"    allowed_vlans: \"{p['allowed_vlans']}\"\n"
                    sw_yml += f"    description: \"{p['description']}\"\n"

        yml = (
            f"# Generated from devices.csv + links.csv + switch_ports.csv — не править руками\n"
            f"kind: {r['kind']}\n"
            f"site: {r['site']}\n"
            f"asn: {r['asn']}\n"
            f"{loopback_line}"
            f"mgmt_inband_ip: {r['mgmt_inband']}\n"
            f"ansible_host: {r['mgmt_oob']}\n"
            f"{p2p_yml}"
            f"{sw_yml}"
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
    switch_data = parse_switch_ports()
    write_host_vars(rows, links, switch_data)
    write_inventory(rows)

    for r in rows:
        n_p2p = len(links.get(r["hostname"], []))
        sd = switch_data.get(r["hostname"], {})
        n_pc = len(sd.get("port_channels", []))
        n_ports = len(sd.get("ports", []))
        extras = []
        if n_p2p: extras.append(f"+{n_p2p} P2P")
        if n_pc:  extras.append(f"+{n_pc} LAG")
        if n_ports: extras.append(f"+{n_ports} ports")
        suffix = "  " + "  ".join(extras) if extras else ""
        print(f"  {r['hostname']:<6} kind={r['kind']:<7} site={r['site']:<12} "
              f"OOB={r['mgmt_oob']:<16}{suffix}")
    print(f"\nУстройств: {len(rows)}  |  Линков: {sum(len(v) for v in links.values())//2}")


if __name__ == "__main__":
    main()
