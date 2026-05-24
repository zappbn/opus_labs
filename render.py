#!/usr/bin/env python3
"""
Локальный рендерер конфигов — для первичной заливки нового устройства через консоль.

Читает: group_vars/all.yml + host_vars/<host>.yml + templates/device.j2
Пишет:  rendered/<host>.cfg

Использование:
    python render.py            # все устройства из host_vars/
    python render.py R12        # только R12
    python render.py R12 R13    # только эти

Не требует Ansible. Не лезет в сеть. Безопасно гонять на Винде.

Зависимости:
    pip install pyyaml jinja2
"""
import sys
from pathlib import Path

try:
    import yaml
    from jinja2 import Environment
except ImportError:
    sys.exit("Нужны библиотеки: pip install pyyaml jinja2")

# trim_blocks/lstrip_blocks убирают лишние \n от {% if %}-блоков
JINJA = Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)

LAB = Path(__file__).resolve().parent
GROUP_VARS = LAB / "group_vars" / "all.yml"
TEMPLATE = LAB / "templates" / "device.j2"
HOST_VARS_DIR = LAB / "host_vars"
OUT_DIR = LAB / "rendered"


def main():
    OUT_DIR.mkdir(exist_ok=True)
    group_vars = yaml.safe_load(GROUP_VARS.read_text(encoding="utf-8")) or {}
    template = JINJA.from_string(TEMPLATE.read_text(encoding="utf-8"))
    targets = set(sys.argv[1:])  # пусто = всё

    rendered = 0
    for yml_file in sorted(HOST_VARS_DIR.glob("*.yml")):
        hostname = yml_file.stem
        if targets and hostname not in targets:
            continue
        host_vars = yaml.safe_load(yml_file.read_text(encoding="utf-8")) or {}
        vars = {**group_vars, **host_vars, "inventory_hostname": hostname}
        out_path = OUT_DIR / f"{hostname}.cfg"
        out_path.write_text(template.render(**vars), encoding="utf-8")
        print(f"  {hostname} -> {out_path}")
        rendered += 1

    if targets and rendered < len(targets):
        missed = targets - {p.stem for p in HOST_VARS_DIR.glob('*.yml')}
        if missed:
            print(f"\n! не нашёл host_vars для: {sorted(missed)}")
    print(f"\nГотово, конфигов: {rendered}")


if __name__ == "__main__":
    main()
