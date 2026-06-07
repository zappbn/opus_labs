# Домашнее задание - PBR

## Постановка задачи

Цель: настроить политику маршрутизации в офисе Чокурдах и распределить трафик между двумя линками.

Требования:
1. Настроить политику маршрутизации для сетей офиса
2. Распределить трафик между двумя линками с провайдером
3. Настроить отслеживание линка через технологию IP SLA (только для IPv4)
4. Настроить для офиса Лабытнанги маршрут по умолчанию
5. План работы и изменения зафиксировать в документации

## Маппинг названий

В нашей лабе названия сайтов исторические (другие), поэтому маппинг:

| В задании | В нашей лабе | Устройства | Кол-во uplinks |
|---|---|---|---|
| **Чокурдах** | **Vologda (AS65007)** | R28 + SW29 + VPC30 + VPC31 | 2 (к R25 и R26 в Triada) |
| **Лабытнанги** | **Cherepovets (AS65006)** | R27 | 1 (к R25 в Triada) |

## Архитектура решения

### Чокурдах (Vologda)

SW29 - L3-свитч (включаем маршрутизацию):
- `ip routing` (inter-VLAN routing)
- `interface Vlan10` SVI с IP `10.7.10.1/24` - gateway пользователей VLAN 10
- `interface Ethernet0/2` - routed port (`no switchport`), IP `10.7.254.0/31`, линк к R28
- `ip route 0.0.0.0 0.0.0.0 10.7.254.1` - всё незнакомое уходит на R28

R28 - border router (граница AS65007):
- `interface Ethernet0/2` - routed port, IP `10.7.254.1/31`, линк к SW29
- На него же повешена политика: `ip policy route-map PBR-VOLOGDA`
- `interface Ethernet0/1` - линк к R25 (Triada) = ISP-1, сеть `172.16.0.14/31`
- `interface Ethernet0/0` - линк к R26 (Triada) = ISP-2, сеть `172.16.0.12/31`
- Static обратно в VLAN 10: `ip route 10.7.10.0/24 via 10.7.254.0`

### Лабытнанги (Cherepovets)

R27 - тут просто default route, никакого PBR:
- `ip route 0.0.0.0 0.0.0.0 172.16.0.10 name default-via-R25`

## Дизайн PBR

### Классы трафика (ACL)

| ACL | Источник | Назначение | Назначение в домашке |
|---|---|---|---|
| `USERS_TO_MSK_SPB` | `10.7.0.0/16` | `10.1.0.0/16` (Moscow) или `10.2.0.0/16` (SPB) | Трафик к центральным офисам |
| `USERS_TO_TRD_CHR` | `10.7.0.0/16` | `10.5.0.0/16` (Triada) или `10.6.0.0/16` (Cherepovets) | Трафик к локальным соседям |
| _(нет ACL)_ | любой | любой (catch-all) | Всё остальное |

### Политика (route-map PBR-VOLOGDA)

```
route-map PBR-VOLOGDA permit 10        // MSK/SPB трафик
 match ip address USERS_TO_MSK_SPB
 set ip next-hop verify-availability 172.16.0.14 10 track 1   // основной ISP-1
 set ip next-hop verify-availability 172.16.0.12 20 track 2   // резерв ISP-2

route-map PBR-VOLOGDA permit 20        // TRD/CHR трафик
 match ip address USERS_TO_TRD_CHR
 set ip next-hop verify-availability 172.16.0.12 10 track 2   // основной ISP-2
 set ip next-hop verify-availability 172.16.0.14 20 track 1   // резерв ISP-1

route-map PBR-VOLOGDA permit 30        // catch-all (любой остальной трафик)
 set ip next-hop verify-availability 172.16.0.14 10 track 1   // основной ISP-1
 set ip next-hop verify-availability 172.16.0.12 20 track 2   // резерв ISP-2
```

Получается балансировка: разные классы трафика идут через разные ISP параллельно. Если один из них падает - всё перетекает на оставшийся.

### IP SLA + Track

```
ip sla 1 / icmp-echo 172.16.0.14 source-interface Ethernet0/1 / frequency 5
ip sla 2 / icmp-echo 172.16.0.12 source-interface Ethernet0/0 / frequency 5
ip sla schedule 1 / 2 life forever start-time now

track 1 ip sla 1 reachability / delay down 10 up 5
track 2 ip sla 2 reachability / delay down 10 up 5
```

Каждые 5 секунд пингуется сторона соседа на P2P-линке. Если 10 секунд подряд нет ответа - track падает. Через 5 секунд после восстановления - track снова up.

## Mgmt мимо PBR

Mgmt-трафик через PBR не гоняем. Если он попадёт в падающий ISP - на устройство не зайдёшь именно тогда, когда зайти нужнее всего.

У нас mgmt и так в стороне: OOB живёт на отдельном `Ethernet1/3` через Cloud1 в подсети `192.168.100.0/24`. С PBR-интерфейсом не пересекается.

## Адресация

| Назначение | Адрес |
|---|---|
| VLAN 10 USERS (Чокурдах) | `10.7.10.0/24`, gateway `10.7.10.1` (SVI Vlan10 на SW29) |
| L3-линк SW29 ↔ R28 | `10.7.254.0/31` (SW29 = `.0`, R28 = `.1`) |
| Inter-AS R28 ↔ R25 (ISP-1) | `172.16.0.14/31` (R28 = `.15`, R25 = `.14`) |
| Inter-AS R28 ↔ R26 (ISP-2) | `172.16.0.12/31` (R28 = `.13`, R26 = `.12`) |
| R28 Loopback0 | `10.7.255.28/32` |
| VPC30 | `10.7.10.30/24`, gateway `10.7.10.1` |
| VPC31 | `10.7.10.31/24`, gateway `10.7.10.1` |
| Inter-AS R27 ↔ R25 | `172.16.0.10/31` (R27 = `.11`, R25 = `.10`) |
| R27 default route | через `172.16.0.10` (R25) |

## Проверка работы

### 1. Маршруты SW29

```bash
ansible SW29 -m ios_command -a "commands='show ip route'"
```

Ожидается:
```
Gateway of last resort is 10.7.254.1 to network 0.0.0.0
S*    0.0.0.0/0 [1/0] via 10.7.254.1
C     10.7.10.0/24 is directly connected, Vlan10
C     10.7.254.0/31 is directly connected, Ethernet0/2
```

### 2. PBR прицеплен к Eth0/2

```bash
ansible R28 -m ios_command -a "commands='show ip policy'"
```

Ожидается:
```
Interface      Route map
Ethernet0/2    PBR-VOLOGDA
```

### 3. Track в Up

```bash
ansible R28 -m ios_command -a "commands='show track brief'"
```

Ожидается:
```
Track 1 ip sla 1 reachability Up
Track 2 ip sla 2 reachability Up
```

### 4. SLA отвечает

```bash
ansible R28 -m ios_command -a "commands='show ip sla statistics'"
```

Ожидается: `Return Code: OK`, число successes растёт.

### 5. Default на R27

```bash
ansible R27 -m ios_command -a "commands='show ip route static'"
```

Ожидается: `S* 0.0.0.0/0 [1/0] via 172.16.0.10`

## Тестирование PBR с VPC30

В EVE-NG GUI настроить VPC:

```
VPCS> ip 10.7.10.30/24 10.7.10.1
VPCS> save
VPCS> ping 10.7.10.1                  # gateway SVI на SW29 - должно работать
```

Затем тестовые пинги (ответов не будет - return path в нашей лабе не настроен, но PBR классификация подтвердится счётчиками):

```
VPCS> ping 10.1.255.12                # Moscow - попадёт в seq 10
VPCS> ping 10.5.255.23                # Triada - попадёт в seq 20
VPCS> ping 10.3.255.21                # Lamas - попадёт в seq 30 (catch-all)
```

После каждой пары пингов:

```bash
ansible R28 -m ios_command -a "commands='show route-map PBR-VOLOGDA'"
```

В блоках `route-map ... permit X` строки `Policy routing matches: N packets` должны расти. Так видно что пакеты реально попадают в нужные правила.

## Тест отказоустойчивости (failover)

Имитируем падение ISP-1 шатдауном порта на R25:

```bash
ansible R25 -m cisco.ios.ios_config -a "lines='shutdown' parents='interface Ethernet0/3'"
sleep 15
ansible R28 -m ios_command -a "commands='show track brief'"
# Track 1: Down   (за 10 сек после потери пингов)
# Track 2: Up

ansible R28 -m ios_command -a "commands='show route-map PBR-VOLOGDA'"
# В каждом блоке: 172.16.0.14 [down], 172.16.0.12 [up]
# - значит трафик MSK/SPB теперь идёт через ISP-2 (резерв)

# Восстанавливаем
ansible R25 -m cisco.ios.ios_config -a "lines='no shutdown' parents='interface Ethernet0/3'" -a "save_when=modified"
sleep 10
ansible R28 -m ios_command -a "commands='show track brief'"
# Track 1: Up (через 5 сек, delay up 5)
```

## Результаты (реальные)

Получено в ходе выполнения:

**Track статус в нормальном режиме:**
```
Track 1 ip sla 1 reachability Up   00:00:54
Track 2 ip sla 2 reachability Up   00:00:54
```

**Счётчики PBR после тестовых пингов:**
```
route-map PBR-VOLOGDA permit 10 - Policy routing matches: 4 packets, 392 bytes
route-map PBR-VOLOGDA permit 20 - Policy routing matches: 6 packets, 588 bytes
route-map PBR-VOLOGDA permit 30 - Policy routing matches: 2 packets, 196 bytes
```

**После shutdown R25 Eth0/3 (имитация падения ISP-1):**
```
Track 1 ip sla 1 reachability Down  00:00:08
Track 2 ip sla 2 reachability Up    00:20:04
```

И в route-map везде:
- `172.16.0.14 ... [down]` - ISP-1 для PBR теперь недоступен
- `172.16.0.12 ... [up]` - трафик едет через ISP-2

Никаких ручных переключений - всё само.

## Чего нет и почему

- **Пинги с VPC30 не возвращаются**. На R12, R23, R21 ответы не приходят - между AS-ами в лабе нет общей маршрутизации (ни BGP, ни OSPF, ни обратной статики). PBR классификацию это не ломает - счётчики растут, видно что пакет ушёл туда куда надо. Дальше return path - уже другая задача.
- **Default route на R28 не делал**. В задании default только для Лабытнанги (R27). На R28 catch-all закрыт через PBR seq 30 с tracking - если оба ISP лягут одновременно, трафик дропнется, и это нормально.

## Файлы

- `pbr.yml` - этот плейбук применяет всю конфигурацию (R28 + SW29 + R27)
- Зависимости в корне проекта:
  - `inventory.yml` - inventory с группами
  - `group_vars/all.yml` - креды, SSH-параметры
  - `ansible.cfg` - конфиг Ansible
