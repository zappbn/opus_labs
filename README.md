# LAB01 — автоматизация Cisco в EVE-NG через Ansible

Лабораторка. Цель: править адресацию в табличке → Ansible катит на устройства.

> Главное правило: **никаких лишних абстракций**. Если что-то можно сделать одним
> инструментом — делаем одним. Если файл повторяет работу другого — выкидываем.

---

## 1. Что в итоге сделано

- 4 роутера SPB (R16, R17, R18, R32) на IOL в EVE-NG
- Доступны по SSH из EVE-хоста через OOB-сеть `192.168.100.0/24`
- Ansible 6.x на EVE-хосте, ходит на роутеры, читает `host_vars/` и пушит конфиги из одного Jinja-шаблона

---

## 2. Структура

```
otus_labs/
├── devices.csv                      ← ИСТОЧНИК ПРАВДЫ. Плоская таблица.
├── LAB01 - Адресация - Sheet1.csv   ← старая большая таблица — справка (шаблоны адресации, P2P, VLAN'ы)
├── net.png                          ← схема топологии
├── README.md                        ← этот файл
│
├── csv2yaml.py                      ← devices.csv → host_vars/*.yml (DictReader, ~30 строк)
├── render.py                        ← локальный рендерер конфигов (Windows, Python+Jinja2)
│
├── ansible.cfg                      ← конфиг Ansible (inventory, callback, таймауты)
├── inventory.yml                    ← какие хосты в каких группах
├── group_vars/all.yml               ← общие переменные (mgmt_iface, креды, ssh-args)
├── host_vars/R16.yml ... R32.yml    ← сгенерены csv2yaml.py — НЕ ПРАВИТЬ РУКАМИ
│
├── templates/device.j2              ← один Jinja-шаблон, обслуживает и роутеры, и свитчи
├── playbooks/
│   └── apply.yml                    ← пушит через ios_config по SSH (для уже доступных)
└── rendered/                        ← создаётся render.py; для первичной заливки
```

### Где что запускается

| Действие | Где | Чем |
|---|---|---|
| Правка `devices.csv` | Windows (или EVE через WinSCP — один файл) | блокнот / Sheets |
| `csv2yaml.py` | Windows | `python csv2yaml.py` |
| Рендер `.cfg` для console paste | **Windows** | `python render.py R12` |
| Открыть `rendered/*.cfg`, скопировать | Windows | блокнот |
| Paste в PuTTY-консоль | Windows | PuTTY |
| `apply.yml` | EVE | `ansible-playbook playbooks/apply.yml` (нужен SSH к устройствам — это есть только у EVE-хоста) |

EVE-shell нужен **только для `apply.yml`** — всё, что вокруг bootstrap'а нового устройства, делается на Винде.

**Файлов мало. Каждый делает одно.** Никаких `bootstrap.j2` отдельно от `device.j2`,
никаких двух выходных `.cfg` на устройство, никакого Python-генератора, повторяющего Ansible.

### Формат `devices.csv`

```csv
hostname,kind,site,asn,loopback,mgmt_inband
R12,router,Moscow,65001,10.1.255.12/32,10.1.250.12/24
R17,router,SPB,65002,10.2.255.17/32,10.2.250.17/24
SW9,switch,SPB,65002,,10.2.250.109/24
SW10,switch,SPB,65002,,10.2.250.110/24
```

Одна большая плоская таблица. **Никаких заголовков секций, никаких пустых строк.** Парсер
тривиальный: `csv.DictReader`. Добавишь колонку — добавится переменная в host_vars,
никаких регулярок переписывать.

**Колонки:**
- `kind` — `router` или `switch`. Шаблон ветвится по этому полю.
- `loopback` — только для `router`. У switch — пусто.
- `mgmt_inband` — in-band mgmt-адрес внутри сайта (для справки/будущих in-band задач). Текущий bootstrap его не использует.

**OOB-адрес** (`192.168.100.X`) вычисляется из числового суффикса имени:
`R12` → `.12`, `SW9` → `.9`, `SW10` → `.10`. Отдельной колонкой не хранится.

---

## 3. Ключевые решения

| Вопрос | Что выбрали | Почему |
|---|---|---|
| Где запускать Ansible? | На EVE-хосте | WSL отключён ради VMware Player; EVE-хост уже на всех `pnetX`-бриджах, видит лабу нативно. |
| Mgmt-сеть для Ansible | OOB `192.168.100.0/24` (R{ID} → .{ID}) | In-band per-site VLAN'ы требуют, чтобы L3-связность лабы уже работала — а мы её и собираемся настраивать. Курица/яйцо. |
| Mgmt-интерфейс | `Ethernet1/3` на всех | IOL: интерфейсы группами по 4. Добавили вторую portgroup → mgmt всегда на последнем порту, не "съезжает" при добавлении лабораторных линков. |
| Стыковка mgmt с EVE-хостом | Cloud1 (`pnet1`) | `Cloud0` = домашняя сеть (грязно). `Cloud1` = внутренний бридж EVE; EVE-хост его видит, наружу не торчит. |
| Промежуточный bridge-свитч | Отказались | EVE-NG не даёт соединить Network↔Network (bridge↔cloud). Cloud сам = L2-сегмент, втыкаем роутеры прямо в него. |
| Креды на vty | `admin/cisco`, privilege 15 через user, `login local` | Минимум, который циска принимает. Консоль (`line con 0`) не трогаем — там доступ открыт как было. |
| Telnet vs SSH | SSH (telnet оставлен fallback) | Современный Ansible (`ansible.netcommon.network_cli`) умеет только SSH. |
| Версия Ansible | 6.x (core 2.13) через pip | Apt'овская 2.9 — EOL. Core 2.14+ требует Python 3.9+, у EVE 3.8. 6.x — последний совместимый. |

---

## 4. Как пользоваться

### Когда правишь CSV (на Windows)

```cmd
python csv2yaml.py                                  # обновить host_vars
```

Если поменялась адресация на уже-настроенном устройстве — дальше `apply.yml` донесёт.

### Когда хочешь увидеть, что Ansible собирается катить (на EVE)

```bash
ansible-playbook playbooks/apply.yml --check --diff
```

Покажет diff между running-config и желаемым. Ничего не применяет.

### Когда хочешь применить (на EVE)

```bash
ansible-playbook playbooks/apply.yml --limit R17     # на одного для проверки
ansible-playbook playbooks/apply.yml                 # на всю группу
```

### Когда заводишь НОВОЕ устройство (bootstrap, разово)

Подходит и для роутеров, и для свитчей — разница только в шаблоне (он ветвится по `kind`).

Делается **один раз** на устройство. Дальше — только Ansible.

**1. В таблице (Windows):**
- Добавь строку в `devices.csv` (укажи `kind: router` или `switch`, у switch колонка `loopback` пустая)
- Если новый сайт — открой `csv2yaml.py`, добавь сайт в `TARGET_SITES`
- `python csv2yaml.py` → появится `host_vars/<hostname>.yml`

**2. В EVE-NG GUI:**
- Stop ноды
- Edit Node → **Ethernet portgroups = 2** (чтобы появился `Ethernet1/3`)
- Подсоедини `Ethernet1/3` к `mgmt-cloud` (Cloud1)
- Start ноды

**3. Сгенерь конфиг для console paste (Windows):**
```cmd
python render.py R12
```
Появится `rendered/R12.cfg`.

**4. PuTTY-консоль ноды (Windows):**
- Ответь `no` на "initial configuration dialog?"
- `enable` → `configure terminal`
- **Правый клик мыши** — паста содержимого `rendered/R12.cfg`
- `crypto key generate rsa modulus 2048` (на IOL/IOU проходит и в config-mode)
- `end` → `wr`

**5. Добавь в inventory (Windows):**
- Открой `inventory.yml`, впиши хост в нужную группу

**6. Проверь с EVE-хоста:**
```bash
ssh -oKexAlgorithms=+diffie-hellman-group14-sha1 \
    -oHostKeyAlgorithms=+ssh-rsa \
    admin@192.168.100.##
```
Если влетел в `R##` / `SW##` — bootstrap завершён.

**7. Дальше Ansible (EVE):**
```bash
ansible-playbook playbooks/apply.yml --limit R## --check --diff   # plan
ansible-playbook playbooks/apply.yml --limit R##                  # apply
```

> Bootstrap нельзя автоматизировать полностью — у нового устройства нет IP, SSH и юзера, ходить туда некуда кроме консоли. **Это неизбежная стоимость одного раза.** Дальше Ansible.

---

## 5. Что слетит после ребута EVE

`pnet1` теряет IP `192.168.100.1/24`. Пока — лечится одной командой:
```bash
ip addr add 192.168.100.1/24 dev pnet1
```
Когда захочешь персистентно — пропишем в `/etc/network/interfaces` (отложили).

---

## 6. Гайд по тому, как это всё было собрано (история проекта)

Хронология действий — для понимания, **почему** структура такая. Можно пропустить если не интересно.

1. **CSV → host_vars.** Изначально парсили большую `LAB01 - Адресация...csv` со свободной структурой (секции, заголовки) через regex. Хрупко: любая правка в Sheets ломала парсер.
2. **EVE топология.** В лабе добавили ноду `Cloud1`. У каждого роутера в *Edit Node* увеличили Ethernet portgroups до 2 (появился `Ethernet1/3`). Соединили каждый R с Cloud1 через `e1/3`.
3. **Первичная заливка.** HTML5-консоль EVE не даёт paste — переключились на PuTTY (запустили `C:\Program Files\EVE-NG\win10_64bit_putty.reg`). В PuTTY paste = правый клик. На каждом из 4 роутеров: `enable` → `conf t` → паста → `crypto key generate rsa modulus 2048` → `wr`.
4. **Mgmt-сеть EVE.** SSH на EVE-хост (`ssh root@<eve-ip>`, пароль `eve`). Дали `pnet1` IP: `ip addr add 192.168.100.1/24 dev pnet1`. Пингануло всех R16/17/18/32.
5. **SSH с EVE на роутеры.** Современный OpenSSH ругается на устаревший KEX/HostKey IOL'а. Лечится: `ssh -oKexAlgorithms=+diffie-hellman-group14-sha1 -oHostKeyAlgorithms=+ssh-rsa admin@192.168.100.17`. Эти опции вписаны в `ansible_ssh_common_args`.
6. **Ansible.** Apt'овская 2.9 — EOL → снос. Pip-овая 6.x (core 2.13) — подходит под Python 3.8. Дополнительно `paramiko<3.0` (3.x по умолчанию отрезает ssh-rsa). Удалили `/root/.ansible/collections` (там лежали слишком новые версии, требующие Ansible 2.15+).
7. **Первый плейбук.** Запустили `ios_command` → все 4 хоста ответили. End-to-end pipeline работает.
8. **Чистка №1.** Снесли промежуточные сущности: отдельный `bootstrap.j2`, два cfg-файла на роутер, отдельный python-рендер. Заменили на: один `device.j2` + Ansible `template`/`ios_config`. Один источник правды, одна точка применения.
9. **Чистка №2 (CSV).** Большая таблица оказалась хрупкой для парсинга. Сделали отдельный `devices.csv` — одна плоская таблица, фиксированные колонки, `csv.DictReader`. Парсер ужался с 80 строк до 25, не ломается от изменений в Sheets. Старая таблица оставлена для людей как справка.

---

## 7. Что важно помнить про лабу vs прод

- Креды в открытом виде в `group_vars/all.yml` — нормально для лабы, в проде использовать Vault или env.
- `host_key_checking = False` и `StrictHostKeyChecking=no` — нормально для лабы, в проде проверять fingerprints.
- IOL 15.4 поддерживает только устаревший KEX/HostKey — в проде на современном оборудовании этих опций SSH **не нужно**.

---

## 8. Что дальше

1. **Остальные сайты.** Bootstrap-процедура из секции 4 — для каждого нового роутера. Начинать лучше с Moscow (R12-R20).
2. **`ios_config` → declarative модули.** Текущий `apply.yml` показывает "вечный changed" на 7 косметических строках (`username secret`, `no shutdown` и т.п.) — это известная слабость `ios_config`. Перевести на `cisco.ios.ios_user` / `ios_interfaces` / `ios_l3_interfaces` — получим настоящую идемпотентность. (Задача #8 в трекере.)
3. **P2P-интерфейсы.** Добавить в `devices.csv` или в отдельный `links.csv` колонки `device_a, iface_a, device_b, iface_b, network`. Расширить `device.j2` или сделать отдельный шаблон.
4. **Sync файлов Windows ↔ EVE.** Сейчас правишь на Windows и копируешь через WinSCP. Если надоест — git push/pull. Или редактирование прямо на EVE через WinSCP (двойной клик по файлу).
5. **Персистентность `pnet1`** через `/etc/network/interfaces` на EVE — чтобы IP не слетал после ребута VM.
