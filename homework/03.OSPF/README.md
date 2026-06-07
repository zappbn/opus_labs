# Домашнее задание - OSPF

## Постановка задачи

Цель: настроить OSPF в офисе Москва, разделить сеть на зоны и настроить фильтрацию между зонами.

Требования:
1. R14, R15 — в area 0 (backbone)
2. R12, R13 — в area 10, дополнительно к маршрутам должны получать default route
3. R19 — в area 101, получает **только** default route
4. R20 — в area 102, получает **все маршруты, кроме маршрутов сетей area 101**
5. Настройка для IPv6 повторяет логику IPv4 (в данной работе IPv6 пропущен — топологии IPv6 в проекте пока нет)
6. План работы зафиксирован в документации

## Топология Moscow (AS65001)

```
              area 0
              ┌──── Lo0 R14 ────┐
              │   (vlink)       │
              │ ┌─ area 0 ──┐   │
              │ │           │   │
        area 10│ │           │   │area 10
R12 ──Eth0/2──┼─┘           └───┼────Eth0/1── R12 (нет, это R12-R15)
              │                 │
              R14               R15
             /│ \               / │\
       area 10│  area 101  area 10│ area 102
       Eth0/0 │  Eth0/3    Eth0/0,1 Eth0/3
        ↓     │   ↓           ↓     ↓
       R12   R13  R19        R12,R13 R20
```

Физических линков **четыре** между R12/R13 и R14/R15:

| Линк | Подсеть | Area |
|---|---|---|
| R12 Eth0/2 ↔ R14 Eth0/0 | 10.1.254.0/31 | 10 |
| R12 Eth0/3 ↔ R15 Eth0/1 | 10.1.254.2/31 | 10 |
| R13 Eth0/3 ↔ R14 Eth0/1 | 10.1.254.4/31 | 10 |
| R13 Eth0/2 ↔ R15 Eth0/0 | 10.1.254.6/31 | 10 |
| R14 Eth0/3 ↔ R19 Eth0/0 | 10.1.254.8/31 | 101 |
| R15 Eth0/3 ↔ R20 Eth0/0 | 10.1.254.10/31 | 102 |

Между R14 и R15 **прямого физического линка нет**, поэтому area 0 строится через virtual-link с transit-area 10.

Inter-AS линки (R14 Eth0/2 ↔ R22, R15 Eth0/2 ↔ R21) в OSPF не включаются — внешний трафик через OSPF не маршрутизируется.

## Дизайн зон

| Area | Тип | Роутеры | Что внутри |
|---|---|---|---|
| 0 | backbone (regular) | R14, R15 | Loopback'и R14/R15 + virtual-link |
| 10 | regular | R12, R13 (internal); R14, R15 (ABR) | Loopback'и R12/R13 + 4 P2P-линка |
| 101 | totally stubby | R19 (internal); R14 (ABR) | Loopback R19 + P2P R14↔R19 |
| 102 | regular | R20 (internal); R15 (ABR) | Loopback R20 + P2P R15↔R20 |

### Почему area 10 — regular, а не stub

Virtual-link не может проходить через stub area (RFC 2328). Transit area для vlink обязана быть regular. Раз area 10 у нас transit — она regular.

Следствие: default route в area 10 не инжектится автоматически (это была плюшка stub). Default отдаётся через `default-information originate always` на R14 и R15 → LSA-5 type-5. Для R12/R13 эффект тот же: маршрут 0.0.0.0/0 в таблице.

### Почему area 101 — totally stubby

По заданию R19 видит **только default**. Totally stubby (`area 101 stub no-summary` на ABR R14, `area 101 stub` на R19) блокирует:
- LSA-3 (inter-area summary) — `no-summary` на ABR
- LSA-5 (external) — свойство любого stub-варианта

ABR R14 автоматически инжектит default route как LSA-3. R19 получает только default + свои intra-area (P2P до R14 и Lo0).

### Почему area 102 — regular + filter-list

R20 должен видеть **всё кроме area 101**. Stub-вариант не подходит — там нельзя выборочно резать конкретные префиксы. Поэтому area 102 — regular, а на ABR R15 ставится `area 102 filter-list prefix DENY-AREA101 out`. Эта команда означает: «при генерации LSA-3 в area 102 не отправлять префиксы из prefix-list».

```
ip prefix-list DENY-AREA101 seq 5  deny 10.1.254.8/31     ! P2P R14↔R19
ip prefix-list DENY-AREA101 seq 10 deny 10.1.255.19/32    ! Loopback R19
ip prefix-list DENY-AREA101 seq 100 permit 0.0.0.0/0 le 32
```

Default route к R20 при этом проходит — потому что он не LSA-3, а LSA-5 от `originate always`.

## Virtual-link

R14 ↔ R15 поднимают виртуальное OSPF-соседство через area 10 как transit:

```
! на R14
router ospf 1
 area 10 virtual-link 10.1.255.15

! на R15
router ospf 1
 area 10 virtual-link 10.1.255.14
```

После сходимости проверяется через `show ip ospf virtual-links` — состояние должно быть `up`. Адрес внутри vlink будет один из IP в transit-area (выбирается OSPF'ом из доступных путей через R12/R13).

## Router-ID

Везде явно зафиксирован на адресе Loopback0, чтобы не зависел от порядка up интерфейсов:

| Роутер | RID |
|---|---|
| R12 | 10.1.255.12 |
| R13 | 10.1.255.13 |
| R14 | 10.1.255.14 |
| R15 | 10.1.255.15 |
| R19 | 10.1.255.19 |
| R20 | 10.1.255.20 |

## Network type

На всех /31 P2P-интерфейсах принудительно поставлен `ip ospf network point-to-point`. По умолчанию IOS на Ethernet ставит broadcast (выборы DR/BDR), что на /31 излишне — на P2P это просто замедляет схождение. P2P-тип даёт более быстрое и предсказуемое соседство.

## Какие интерфейсы участвуют в OSPF

| Роутер | Интерфейс | Area | Назначение |
|---|---|---|---|
| R12 | Lo0 | 10 | router-id, loopback |
| R12 | Eth0/2 | 10 | к R14 |
| R12 | Eth0/3 | 10 | к R15 |
| R13 | Lo0 | 10 | router-id, loopback |
| R13 | Eth0/2 | 10 | к R15 |
| R13 | Eth0/3 | 10 | к R14 |
| R14 | Lo0 | 0 | router-id, loopback |
| R14 | Eth0/0 | 10 | к R12 |
| R14 | Eth0/1 | 10 | к R13 |
| R14 | Eth0/3 | 101 | к R19 |
| R15 | Lo0 | 0 | router-id, loopback |
| R15 | Eth0/0 | 10 | к R13 |
| R15 | Eth0/1 | 10 | к R12 |
| R15 | Eth0/3 | 102 | к R20 |
| R19 | Lo0 | 101 | router-id, loopback |
| R19 | Eth0/0 | 101 | к R14 |
| R20 | Lo0 | 102 | router-id, loopback |
| R20 | Eth0/0 | 102 | к R15 |

Не в OSPF: R14 Eth0/2 (к R22, inter-AS), R15 Eth0/2 (к R21, inter-AS), Eth1/3 (OOB-mgmt через Cloud1).

## Проверка работы

### 1. OSPF-соседства

```bash
ansible R12,R13,R14,R15,R19,R20 -m ios_command -a "commands='show ip ospf neighbor'"
```

Ожидаемые соседства:
- R12: R14, R15 (через Eth0/2, Eth0/3)
- R13: R14, R15 (через Eth0/2, Eth0/3)
- R14: R12, R13, R19 + виртуальный сосед R15 (10.1.255.15)
- R15: R12, R13, R20 + виртуальный сосед R14 (10.1.255.14)
- R19: R14
- R20: R15

Все в состоянии FULL.

### 2. Virtual-link

```bash
ansible R14,R15 -m ios_command -a "commands='show ip ospf virtual-links'"
```

Ожидается `Virtual Link OSPF_VL0 to router 10.1.255.15 is up` (на R14) и зеркально на R15.

### 3. Маршруты R12 (area 10)

```bash
ansible R12 -m ios_command -a "commands='show ip route ospf'"
```

Ожидается:
- `O` (intra-area) — loopback'и R13 и P2P-сети area 10
- `O IA` (inter-area) — loopback'и R14, R15, R19, R20 + P2P-сети area 0/101/102
- `O*E2` — default route 0.0.0.0/0

### 4. Маршруты R19 (area 101 totally stubby)

```bash
ansible R19 -m ios_command -a "commands='show ip route ospf'"
```

Ожидается **только**:
- `O*IA 0.0.0.0/0` — default через ABR (R14)

И больше никаких OSPF-маршрутов.

### 5. Маршруты R20 (area 102 + фильтр)

```bash
ansible R20 -m ios_command -a "commands='show ip route ospf'"
```

Ожидается:
- `O IA` — loopback'и R12, R13, R14, R15 + P2P-сети area 0/10
- `O*E2 0.0.0.0/0` — default
- **НЕТ** `10.1.254.8/31` (P2P R14↔R19)
- **НЕТ** `10.1.255.19/32` (Loopback R19)

### 6. Database на R15 (фильтр на исходе)

```bash
ansible R15 -m ios_command -a "commands='show ip ospf database summary'"
```

R15 в своей LSDB знает про `10.1.254.8/31` и `10.1.255.19/32` (из area 0, куда их положил R14). Но **в area 102 не транслирует** — фильтрует на исходе.

## Чего нет и почему

- **IPv6 (OSPFv3)**. В задании сказано «настройка для IPv6 повторяет логику IPv4». В нашем проекте IPv6-адресации пока нет вообще, отдельный планирование для лабы выходит за рамки. По логике дизайна это копия 1:1 (`ipv6 router ospf 1` вместо `router ospf 1`, `ipv6 ospf 1 area X` на интерфейсах, virtual-link через `area X virtual-link <RID>` так же).
- **Inter-AS линки не в OSPF**. R14 Eth0/2 (к R22) и R15 Eth0/2 (к R21) — это p2p к другим AS. Их нельзя включать в OSPF Moscow-домена, иначе утечёт LSA в чужие сети.
- **Static redistribution не делается**. У R14/R15 нет статических маршрутов, которые надо тянуть в OSPF. Default отдаётся через `originate always` — это самодостаточно.

## Файлы

- `ospf.yml` — плейбук применяет конфигурацию OSPF на R12, R13, R14, R15, R19, R20
- Зависимости в корне проекта:
  - `inventory.yml`, `group_vars/all.yml`, `ansible.cfg`
