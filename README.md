# LAB01 — Cisco network automation в EVE-NG

Учебная сетевая лаборатория OTUS. **Источник правды — две плоские CSV-таблицы**.
Из них автоматически рендерятся конфиги Cisco-устройств (IOL в EVE-NG) и применяются
через Ansible поверх SSH.

Подробная топология и адресация — [`TOPOLOGY.md`](TOPOLOGY.md).
Объяснение инструментов (telnet, expect, SSH, Ansible — зачем и когда) — [`WORKFLOW.md`](WORKFLOW.md).

---

## Состояние

| Компонент | Статус |
|---|---|
| 18 роутеров bootstrap'нуты (loopback + mgmt + SSH) | ✅ |
| OOB-сеть `192.168.100.0/24` между EVE-хостом и устройствами | ✅ |
| Ansible 6.x на EVE-хосте, видит все 18 роутеров | ✅ |
| Топология (23 P2P-линка) описана в `links.csv` | ✅ |
| Конфиги рендерятся из шаблона (`render.py` / `apply.yml`) | ✅ |
| **Apply P2P на устройства** | ⏳ следующий шаг |
| Bootstrap 7 свитчей (SW2..SW29) | ⏳ в очереди |
| Static routes для cross-site reachability | ⏳ после P2P |
| OSPF / BGP | 🚧 за рамками текущей фазы |

---

## Структура

```
otus_labs/
├── devices.csv                ← источник правды: устройства
├── links.csv                  ← источник правды: P2P-линки
│
├── csv2yaml.py                ← CSV → host_vars/*.yml + inventory.yml
├── render.py                  ← локальный рендер конфигов (Windows)
│
├── ansible.cfg
├── inventory.yml              ← сгенерирован
├── group_vars/all.yml         ← общие переменные (mgmt_iface, ssh-args, креды)
├── host_vars/                 ← сгенерированы (per-device)
│
├── templates/device.j2        ← один Jinja-шаблон для роутеров и свитчей
├── playbooks/apply.yml        ← пушит конфиг через cisco.ios.ios_config
│
├── README.md                  ← этот файл
├── TOPOLOGY.md                ← топология и адресный план
├── WORKFLOW.md                ← объяснение инструментов и порядка действий
│
├── LAB01 - Адресация - Sheet1.csv   ← исходная таблица (справка для людей)
└── net.png                          ← схема топологии
```

**Руками редактируются только `devices.csv` и `links.csv`.** Всё остальное (`host_vars/`,
`inventory.yml`, `rendered/`) генерируется из них.

---

## Pipeline

```
   devices.csv  +  links.csv
            │
            │  python csv2yaml.py
            ▼
host_vars/*.yml + inventory.yml
            │
            ├──► python render.py R##  ──►  rendered/R##.cfg  ──►  console paste (для нового устройства)
            │
            └──► ansible-playbook apply.yml  ──►  SSH → ios_config  ──►  running устройство
```

---

## Адресный план (краткая выжимка)

| Назначение | Формула | Пример |
|---|---|---|
| Лупбэк роутера | `10.{site}.255.{id}/32` | R12 → `10.1.255.12/32` |
| In-band mgmt | `10.{site}.250.{id}/24` | R12 → `10.1.250.12/24` |
| OOB mgmt | `192.168.100.{id}/24` | R12 → `192.168.100.12` |
| Intra-AS P2P | `10.{site}.254.x/31` | `10.1.254.0/31`, `10.1.254.2/31`, ... |
| Inter-AS P2P | `172.16.0.x/31` | `172.16.0.0/31`, `172.16.0.2/31`, ... |

Полная карта устройств, линков и интерфейсов — в [`TOPOLOGY.md`](TOPOLOGY.md).

---

## Окружение

**Windows (рабочее место):**
- Python 3.8+
- `pip install pyyaml jinja2`
- PuTTY (или SecureCRT) для консольной связи с устройствами в EVE-NG
- WinSCP для синхронизации файлов с EVE-хостом

**EVE-NG host (Ubuntu, где живёт Ansible):**
- Ansible 6.x (через pip; apt'овский 2.9 — EOL): `pip3 install "ansible>=6.0,<7.0"`
- `paramiko<3.0`: `pip3 install "paramiko<3.0"` (3.x отрезает `ssh-rsa`, нужный для старых IOL)
- Коллекции `cisco.ios` и `ansible.netcommon` (приходят с Ansible 6.x)

---

## Использование

### Регенерация из источников правды

```cmd
python csv2yaml.py        # после правки devices.csv или links.csv
```

### Bootstrap нового устройства (когда SSH ещё нет)

```cmd
python render.py R12      # → rendered/R12.cfg
```

Откройте PuTTY-консоль R12 в EVE-NG → `enable` → `conf t` → правый клик (paste rendered/R12.cfg) →
`crypto key generate rsa modulus 2048` → `end` → `wr`. После этого устройство доступно по SSH.

### Plan / Apply через Ansible

```bash
# на EVE-хосте
cd /root/otus_labs

ansible-playbook playbooks/apply.yml --check --diff              # plan
ansible-playbook playbooks/apply.yml --limit R17 --check --diff  # plan на одного
ansible-playbook playbooks/apply.yml --limit spb                 # apply на сайт
ansible-playbook playbooks/apply.yml                             # apply на всё
```

Inventory сгенерирован с группами по сайту (`moscow`, `spb`, `triada`, ...) и по типу
(`routers`, `switches`), любая комбинация работает для `--limit`.

---

## Архитектурные решения

| Вопрос | Решение | Почему |
|---|---|---|
| Где живёт Ansible? | На EVE-хосте | WSL отключён под VMware Player; EVE-хост уже в L2 со всеми `pnetX`-бриджами лабы. |
| Mgmt-сеть для автоматизации | OOB `192.168.100.0/24` (`R{id}` → `.{id}`) | In-band per-site VLAN'ы требуют рабочей L3-связности лабы — а мы её и собираемся настраивать. Курица-яйцо. |
| Mgmt-интерфейс | `Ethernet1/3` на всех роутерах | IOL: интерфейсы группами по 4. Вторая portgroup → mgmt всегда на крайнем порту, не "съезжает" при добавлении лабораторных линков. |
| Соединение mgmt с EVE-хостом | `Cloud1` (`pnet1`) | `Cloud0` = домашняя сеть (грязно). `Cloud1` = внутренний бридж EVE; EVE-хост его видит, наружу не торчит. |
| Telnet vs SSH | SSH (telnet — fallback) | Современный Ansible (`ansible.netcommon.network_cli`) работает только по SSH. |
| Версия Ansible | 6.x (core 2.13), через pip | apt'овская 2.9 — EOL. core 2.14+ требует Python 3.9+, у EVE 3.8. 6.x — последний совместимый. |
| `paramiko<3.0` | Обязательно | 3.x по умолчанию отрезает `ssh-rsa`, который только и поддерживает IOL 15.4. |
| SSH-опции для IOL | `KexAlgorithms=+diffie-hellman-group14-sha1`, `HostKeyAlgorithms=+ssh-rsa` | IOL 15.4 знает только устаревший KEX/HostKey. Опции в `ansible_ssh_common_args` глобально. |

---

## Лаба vs прод

- Креды в `group_vars/all.yml` — открытым текстом. В проде — `ansible-vault` или внешний secret store.
- `host_key_checking = False` — приемлемо для лабы. В проде — проверять fingerprints.
- Устаревшие KEX/HostKey-опции нужны только для IOL 15.4; на современном оборудовании не используются.
- `transport input ssh telnet` — telnet оставлен как fallback для отладки. В проде telnet выключают.

---

## Дальнейшие шаги

См. [Out of scope в PR](https://github.com/zappbn/opus_labs/pulls) и план в `TOPOLOGY.md`.

1. Apply P2P-конфигов на все 18 роутеров с проверкой ping между лупбэками.
2. Static routes для cross-site reachability.
3. Bootstrap 7 свитчей по той же процедуре.
4. Перевод `ios_config` → declarative-модули (`ios_user`, `ios_interfaces`, `ios_l3_interfaces`) для настоящей идемпотентности.
5. OSPF / BGP, когда курс дойдёт.
