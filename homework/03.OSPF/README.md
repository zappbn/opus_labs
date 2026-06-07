# Домашнее задание - OSPF

## Постановка задачи

Цель: настроить OSPF в офисе Москва, разделить сеть на зоны и настроить фильтрацию между зонами.

Требования:
1. R14, R15 в area 0 (backbone)
2. R12, R13 в area 10, дополнительно к маршрутам должны получать default route
3. R19 в area 101, получает только default route
4. R20 в area 102, получает все маршруты, кроме сетей area 101
5. Настройка для IPv6 повторяет логику IPv4 (тут пропустил, IPv6-адресации в проекте пока нет)
6. План работы зафиксирован в документации

## Топология Moscow (AS65001)

Шесть роутеров, между R12/R13 и R14/R15 полная mesh из четырёх P2P-линков:

| Линк | Подсеть | Area |
|---|---|---|
| R12 Eth0/2 ↔ R14 Eth0/0 | 10.1.254.0/31 | 10 |
| R12 Eth0/3 ↔ R15 Eth0/1 | 10.1.254.2/31 | 10 |
| R13 Eth0/3 ↔ R14 Eth0/1 | 10.1.254.4/31 | 10 |
| R13 Eth0/2 ↔ R15 Eth0/0 | 10.1.254.6/31 | 10 |
| R14 Eth0/3 ↔ R19 Eth0/0 | 10.1.254.8/31 | 101 |
| R15 Eth0/3 ↔ R20 Eth0/0 | 10.1.254.10/31 | 102 |

Между R14 и R15 прямого физического линка нет. Area 0 связывается через virtual-link, где transit-area = 10.

Inter-AS линки (R14 Eth0/2 к R22, R15 Eth0/2 к R21) в OSPF не включаем, чтобы LSA не утекали в чужие AS.

## Дизайн зон

| Area | Тип | Роутеры | Что внутри |
|---|---|---|---|
| 0 | backbone (regular) | R14, R15 | Loopback'и R14/R15 + virtual-link |
| 10 | regular | R12, R13 (internal); R14, R15 (ABR) | Loopback'и R12/R13 + 4 P2P |
| 101 | totally stubby | R19 (internal); R14 (ABR) | Loopback R19 + P2P R14-R19 |
| 102 | regular | R20 (internal); R15 (ABR) | Loopback R20 + P2P R15-R20 |

### Почему area 10 regular, а не stub

Через area 10 идёт virtual-link, а transit area для vlink обязана быть regular (RFC 2328). Поэтому stub нельзя.

Минус такого решения: default route в area 10 автоматически не инжектится (это была плюшка stub area). Поэтому на R14 и R15 явно стоит `default-information originate always`. Для R12/R13 эффект тот же — `0.0.0.0/0` в таблице маршрутов, просто приходит как LSA-5 (external) а не LSA-3.

### Почему area 101 totally stubby

По заданию R19 видит только default. Под это идеально ложится totally stubby:
- `area 101 stub no-summary` на ABR R14 блокирует LSA-3 (summary inter-area)
- `area 101 stub` на R19 (для согласия hello-параметров) блокирует LSA-5 (external)
- ABR R14 сам инжектит default как LSA-3, это единственное что доходит до R19

R19 в итоге видит только default + свои локальные intra-area сети.

### Почему area 102 regular + filter-list

R20 должен видеть всё, кроме area 101. Stub-вариант не подходит — там нельзя выборочно резать конкретные префиксы. Поэтому area 102 regular, а на ABR R15 ставится prefix-list фильтр.

```
ip prefix-list DENY-AREA101 seq 5  deny 10.1.254.8/31     ! P2P R14-R19
ip prefix-list DENY-AREA101 seq 10 deny 10.1.255.19/32    ! Loopback R19
ip prefix-list DENY-AREA101 seq 100 permit 0.0.0.0/0 le 32
!
router ospf 1
 area 102 filter-list prefix DENY-AREA101 in
```

Тонкий момент про направление в `area X filter-list`:

- `out` режет LSA-3 про сети самой area X при их трансляции наружу
- `in` режет LSA-3 про сети других area при их установке в area X

Нам нужно второе — `in`. С `out` команда формально применится, но фильтра не будет (он будет работать в другую сторону, для сетей самой 102, которых под фильтр не подпадают). Я на этом и наступил — первый прогон не сработал, разобрался по `show ip ospf database summary 10.1.254.8`: видел что R15 продолжает анонсировать LSA-3 в area 102, поменял `out` на `in` и сделал `clear ip ospf process`.

Default route к R20 проходит и при включённом фильтре, потому что он LSA-5 (от `originate always`), а filter-list режет только LSA-3.

## Virtual-link

R14 и R15 поднимают виртуальное OSPF-соседство через area 10 как transit:

```
! на R14
router ospf 1
 area 10 virtual-link 10.1.255.15

! на R15
router ospf 1
 area 10 virtual-link 10.1.255.14
```

После сходимости `show ip ospf virtual-links` показывает `OSPF_VL0 is up`, transit area 10, через тот интерфейс R14/R15 в area 10, который OSPF выбрал как ближайший путь к peer-RID.

## Router-ID

Везде явно зафиксирован на IP loopback'а, чтобы не зависел от того, какой интерфейс поднялся первым:

| Роутер | RID |
|---|---|
| R12 | 10.1.255.12 |
| R13 | 10.1.255.13 |
| R14 | 10.1.255.14 |
| R15 | 10.1.255.15 |
| R19 | 10.1.255.19 |
| R20 | 10.1.255.20 |

## Network type

На всех /31 поставил `ip ospf network point-to-point`. По умолчанию IOS на Ethernet ставит broadcast, идут выборы DR/BDR, на /31 это никому не нужно и просто замедляет схождение. P2P-тип даёт быстрее adj и читаемее логи.

## Интерфейсы в OSPF

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

Не в OSPF: R14 Eth0/2 (к R22, другая AS), R15 Eth0/2 (к R21, другая AS), Eth1/3 везде (mgmt OOB через Cloud1).

## Проверка работы

Все проверки автоматизированы в `verify.yml`. Запуск:

```bash
ansible-playbook homework/03.OSPF/verify.yml
```

Ниже фактические куски вывода после применения плейбука.

### 1. Соседства

Все FULL, в том числе виртуальный сосед через OSPF_VL0:

```
R14:
10.1.255.15  FULL/  -      -        10.1.254.3   OSPF_VL0
10.1.255.13  FULL/  -   00:00:34    10.1.254.4   Ethernet0/1
10.1.255.12  FULL/  -   00:00:30    10.1.254.0   Ethernet0/0
10.1.255.19  FULL/  -   00:00:31    10.1.254.9   Ethernet0/3

R15:
10.1.255.14  FULL/  -      -        10.1.254.5   OSPF_VL0
10.1.255.12  FULL/  -   00:00:34    10.1.254.2   Ethernet0/1
10.1.255.13  FULL/  -   00:00:36    10.1.254.6   Ethernet0/0
10.1.255.20  FULL/  -   00:00:39    10.1.254.11  Ethernet0/3

R19:
10.1.255.14  FULL/  -   00:00:32    10.1.254.8   Ethernet0/0

R20:
10.1.255.15  FULL/  -   00:00:37    10.1.254.10  Ethernet0/0
```

### 2. Virtual-link

```
R14# show ip ospf virtual-links
Virtual Link OSPF_VL0 to router 10.1.255.15 is up
  Transit area 10, via interface Ethernet0/0
  Cost 20
  Adjacency State FULL (Hello suppressed)
```

Зеркально на R15.

### 3. Маршруты R12 (area 10)

Видит intra-area (O), inter-area (O IA) и default (O*E2). Default приходит сразу через ECMP (две дороги — к R14 и R15):

```
O*E2  0.0.0.0/0       [110/1] via 10.1.254.3, Ethernet0/3
                       [110/1] via 10.1.254.1, Ethernet0/2
O     10.1.254.4/31   [110/20] via 10.1.254.1, Ethernet0/2
O     10.1.254.6/31   [110/20] via 10.1.254.3, Ethernet0/3
O IA  10.1.254.8/31   [110/20] via 10.1.254.1, Ethernet0/2
O IA  10.1.254.10/31  [110/20] via 10.1.254.3, Ethernet0/3
O     10.1.255.13/32  [110/21] via 10.1.254.3, Ethernet0/3
                       [110/21] via 10.1.254.1, Ethernet0/2
O IA  10.1.255.14/32  [110/11] via 10.1.254.1, Ethernet0/2
O IA  10.1.255.15/32  [110/11] via 10.1.254.3, Ethernet0/3
O IA  10.1.255.19/32  [110/21] via 10.1.254.1, Ethernet0/2
O IA  10.1.255.20/32  [110/21] via 10.1.254.3, Ethernet0/3
```

### 4. Маршруты R19 (area 101 totally stubby)

Только default, и всё. Так и должно быть для totally stubby:

```
Gateway of last resort is 10.1.254.8 to network 0.0.0.0

O*IA  0.0.0.0/0  [110/11] via 10.1.254.8, Ethernet0/0
```

### 5. Маршруты R20 (area 102 + фильтр)

Default + все loopback'и + P2P из area 0/10. Префиксов area 101 (`10.1.254.8/31` и `10.1.255.19/32`) нет:

```
O*E2  0.0.0.0/0       [110/1] via 10.1.254.10, Ethernet0/0
O IA  10.1.254.0/31   [110/30] via 10.1.254.10, Ethernet0/0
O IA  10.1.254.2/31   [110/20] via 10.1.254.10, Ethernet0/0
O IA  10.1.254.4/31   [110/30] via 10.1.254.10, Ethernet0/0
O IA  10.1.254.6/31   [110/20] via 10.1.254.10, Ethernet0/0
O IA  10.1.255.12/32  [110/21] via 10.1.254.10, Ethernet0/0
O IA  10.1.255.13/32  [110/21] via 10.1.254.10, Ethernet0/0
O IA  10.1.255.14/32  [110/31] via 10.1.254.10, Ethernet0/0
O IA  10.1.255.15/32  [110/11] via 10.1.254.10, Ethernet0/0
```

### 6. LSDB на R15 — что фильтр реально режет

`show ip ospf database summary 10.1.254.8`:

```
                Summary Net Link States (Area 0)
  Link State ID: 10.1.254.8
  Advertising Router: 10.1.255.14
  Metric: 10

                Summary Net Link States (Area 10)
  Link State ID: 10.1.254.8
  Advertising Router: 10.1.255.14
  Metric: 10
```

В Area 0 есть LSA-3 (положил R14 как ABR area 101), в Area 10 R15 транслирует её дальше. А блока `Summary Net Link States (Area 102)` для этого префикса нет — R15 знает префикс, но в area 102 его не пускает.

## Чего нет и почему

- IPv6 (OSPFv3). В задании сказано что повторяет логику IPv4, но IPv6-адресации в проекте ещё нет. Дизайн копировался бы 1:1: `ipv6 router ospf 1`, `ipv6 ospf 1 area X` на интерфейсах, тот же `area X virtual-link` для связи backbone, тот же filter-list (но prefix-list IPv6).
- Inter-AS линки в OSPF не включены. R14 Eth0/2 и R15 Eth0/2 идут в другую AS, OSPF Moscow-домена туда не утекает.
- Static redistribution не делал. Default отдаётся через `originate always`, никаких внешних статических маршрутов в OSPF не тянем.

## Файлы

- `ospf.yml` — плейбук, применяет OSPF на R12, R13, R14, R15, R19, R20
- `verify.yml` — проверочный плейбук со всеми acceptance-проверками за один прогон
- Зависимости в корне: `inventory.yml`, `group_vars/all.yml`, `ansible.cfg`
