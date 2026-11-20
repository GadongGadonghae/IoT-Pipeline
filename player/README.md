# ▶️ KAMP CSV 재생 프로그램 (가상 설비)

시연·리허설 때 **가상 설비 39대**(슬리팅 6, 프레스 24, 용접 6, 도장 2, 비전 검사 1) 역할을 하는 프로그램이에요.
미리 만든 **3분 리허설 CSV**를 정해진 시각에 한 줄씩 **MQTT**로 보내고, 코일 입고와 상태 신호(고장 여부 포함)도 보내요.
메시지 모양은 **API 명세서 v3** 기준이에요.

```
[player.py] ──MQTT──> Mosquitto(1883) ──> Node-RED(수집만) ──> Spring Boot(판정·저장·조회) ──> PostgreSQL / Vue
```

---

## 1. 처음 한 번만: 설치

### 1-1. 전용 conda 환경 만들기 + 라이브러리 설치
이 프로그램은 Anaconda의 **전용 환경 `sfaas-player`**에서 실행해요 (base 환경을 깨끗하게 두기 위해서).
**Anaconda Prompt** 또는 PowerShell에서:
```powershell
conda create -n sfaas-player python=3.13 -y
conda activate sfaas-player
cd C:\jyc\sfaas_project\sfaas\player
pip install -r requirements.txt
```
`Successfully installed paho-mqtt ...`가 나오면 OK예요.

> ⚠️ **실행할 때마다 먼저 `conda activate sfaas-player`** 를 입력해요.
> 프롬프트 앞에 `(sfaas-player)`가 보이면 제대로 된 거예요.
> PowerShell에서 `conda activate`가 안 되면 한 번만 `conda init powershell` 실행 후 PowerShell을 다시 열어요.

### 1-2. Mosquitto(MQTT 브로커) 설치 — 실제로 보낼 때만 필요
> 브로커 없이 화면으로만 확인하려면 이 단계는 건너뛰고 **3-1(--dry-run)**으로 가도 돼요.

1. https://mosquitto.org/download/ 에서 **Windows** 설치 파일(`mosquitto-x.x.x-install-windows-x64.exe`)을 받아요.
2. 실행해서 기본값으로 **Next → Install** 해요. (설치 위치: `C:\Program Files\mosquitto`)
3. 설치하면 Windows 서비스로 자동 실행돼요. 확인:
   ```powershell
   Get-Service mosquitto
   ```
   `Running`이면 OK. `Stopped`이면 **관리자 PowerShell**에서 `Start-Service mosquitto`
4. `mosquitto_sub` 명령은 경로를 붙여서 실행해요 (또는 PATH에 `C:\Program Files\mosquitto` 추가):
   ```powershell
   & "C:\Program Files\mosquitto\mosquitto_sub.exe" -h localhost -t "sfaas/raw/#" -v
   ```

> 💡 Mosquitto 2.x는 기본 설정에서 **같은 PC(localhost)에서만** 접속을 받아요. 다른 PC의 Node-RED가 접속해야 하면
> `C:\Program Files\mosquitto\mosquitto.conf`에 `listener 1883` 과 `allow_anonymous true` 두 줄을 넣고 서비스를 다시 시작해요.

---

## 2. 설정 (`config.json`)

| 항목 | 기본값 | 설명 |
|---|---|---|
| `broker.host` / `broker.port` | `localhost` / `1883` | MQTT 브로커 주소 |
| `broker.username` / `password` | 빈 값 | 브로커에 로그인이 필요할 때만 |
| `topics.raw` / `topics.hb` | `sfaas/raw` / `sfaas/hb` | 토픽 앞부분 (값·검사·고장 / 상태 신호) |
| `csv_dir` | `../rehearsal_3min` | CSV 폴더 (이 폴더 기준 상대 경로 또는 전체 경로) |
| `csv_suffix` | `3min` | 파일 이름 끝부분 → `press_3min.csv` 등 5개를 읽어요 |
| `duration_sec` | `180` | 리허설 길이(초). 상태 신호는 이 시각까지 보내요 |
| `value_naming` | `camel` | `values` 안 이름. `camel` → `pressureLeft`, `snake` → `pressure_left` |
| `product_date` | `today` | 검사 제품 ID의 날짜 부분. `today` = 재생하는 날, `"20261118"`처럼 직접 지정, `""` = CSV 그대로 |
| `faults` | C-PR3 90→155초 | 자동 고장 일정. `equipment`, `on`(고장 초), `off`(복구 초) |
| `slitting.enabled` | `true` | 가상 슬리팅 신호(`slitting_3min.csv`) 전체 켜기/끄기 |
| `slitting.disabled` | `[]` | 끌 슬리팅 설비. PLC 연결 후에는 `["A-SL"]` |
| 🆕 `slitting.values` | `plc` | 슬리팅 측정값. `plc` = 값 없이 보내고 Node-RED가 실제 인버터(A-SL) 값으로 채움 · `dummy` = 이 프로그램이 토크·전류·전압 **더미 값**을 넣음 (인버터 없이 시연 영상용). `*_noplc.json`은 `dummy` |
| 🆕 `slitting.dummy` | 토크 12.0±0.6 · 전류 3.5±0.2 · 전압 220±2.5 | 더미 값 범위 `{"항목": [가운데, 흔들림]}`. ⚠️ 가짜 시연 값 (실측 아님). 같은 제품 번호면 늘 같은 값 |
| `heartbeat.enabled` | `true` | 상태 신호 전체 켜기/끄기 |
| `heartbeat.interval_sec` | `1` | 상태 신호 간격(초). 0초부터 시작 |
| `heartbeat.disabled` | `[]` | 상태 신호를 끌 설비. 예: `["A-SL"]` |
| `coil.enabled` | `true` | 코일 입고 전송 켜기/끄기 |
| `coil.product_qty` | `20` | 1롤로 만들 수 있는 제품 수 |
| `coil.schedule` | `[{"at_sec": 0, "rolls": 4}]` | 입고 일정. `at_sec`(보낼 초), `rolls`(롤 수). 예: 0초 4롤 + 120초 1롤 → `[{"at_sec": 0, "rolls": 4}, {"at_sec": 120, "rolls": 1}]` |

> 💡 코일 번호(`materialId`)는 `COIL-날짜-001`부터 붙어요. 날짜는 `product_date` 설정을 같이 써요.

> ⚠️ 상태 신호를 끄면(`heartbeat.enabled: false`) **고장·복구도 알릴 수 없어요** (고장 여부가 상태 신호 안에 들어 있어서).

> 💡 `slitting.disabled`에 넣은 슬리팅 설비는 **상태 신호도 자동으로 꺼져요** (PLC가 대신 보내니까).

> 🆕 **슬리팅 더미 → Node-RED(실제 인버터)로 바꾸기** (11/21)
> | 하고 싶은 것 | 설정 |
> |---|---|
> | 인버터 없이 시연 영상 (지금) | `"slitting": {"values": "dummy", "disabled": []}` → 6대 모두 더미 값 |
> | A-SL만 실제 인버터, 나머지 더미 | `"slitting": {"values": "dummy", "disabled": ["A-SL"]}` + Node-RED "A-SL 슬리팅" 탭의 `sfaas/raw/A-SL` 출력 노드 켜기 |
> | 예전처럼 6대 모두 인버터 값 복사 | `"slitting": {"values": "plc"}` (Node-RED 정제가 A-SL 16초 평균을 6대에 채움) |
>
> Node-RED 정제는 메시지에 값이 **없을 때만** 인버터 값을 채워요. 그래서 더미 값이 있으면 더미가, 없으면 인버터 값이 들어가요 (Node-RED는 고칠 것 없음).

> ⚠️ `faults`를 바꿔도 **CSV의 시각은 바뀌지 않아요.** 지금 CSV는 "C-PR3 90~155초 고장"에 맞춰 만들어져 있어요.
> 고장 일정을 바꾸려면 `sfaas/tools/make_rehearsal.py`로 CSV도 다시 만들어야 해요.

---

## 3. 실행

모든 명령은 전용 환경을 켜고 이 폴더에서 해요:
```powershell
conda activate sfaas-player
cd C:\jyc\sfaas_project\sfaas\player
```

### 3-1. 브로커 없이 확인 (dry-run)
```powershell
python player.py --dry-run --speed 10
```
18초쯤 뒤 끝나고 마지막에 이렇게 나오면 정상이에요.
```
=== 재생 완료 요약 ===
  슬리팅 60 · 프레스 163 · 용접 26 · 도장 23 · 검사 11 · 코일 4 · 고장 전환 2 · 상태 신호 7059
```
(고장 전환 2 = 고장 시작 1번 + 복구 1번. 메시지 개수가 아니라 상태 신호의 `fault` 값이 바뀐 횟수예요)
(상태 신호 7059 = 39대 × 181번 (0, 1, 2, … 180초))
(🆕 A-SL을 실제 인버터로 쓸 때 `config.json`의 `"slitting": {"disabled": ["A-SL"]}` → 슬리팅 50 · 상태 신호 6878)
(메시지 한 줄씩 출력이 너무 많으면 `--quiet` 추가)

### 3-2. 실제 리허설 (MQTT 전송, 3분)
**터미널 ①** — 값·검사·코일 메시지 보기
```powershell
& "C:\Program Files\mosquitto\mosquitto_sub.exe" -h localhost -t "sfaas/raw/#" -v
```
**터미널 ②** — 상태 신호 보기 (1초마다 39줄씩 나와요. 고장 확인만 하려면 C-PR3만 보기)
```powershell
& "C:\Program Files\mosquitto\mosquitto_sub.exe" -h localhost -t "sfaas/hb/C-PR3" -v
```
(39대 전부 보려면 `-t "sfaas/hb/#"`)
**터미널 ③** — 재생 시작
```powershell
conda activate sfaas-player
cd C:\jyc\sfaas_project\sfaas\player
python player.py
```
3분 동안 확인할 것:
| 시각 | 터미널 | 보일 것 |
|---|---|---|
| 0초 | ① | `sfaas/raw/COIL` 4건 (`"productQty": 20`) |
| 86초쯤 | ① | `sfaas/raw/B-PR2` … `"productId": "B-…-0003"`, `"pressureRight": 278.4` (★ 공정 불량 → 이 제품은 PR3로 안 감) |
| 90초 | ② | `sfaas/hb/C-PR3` … `"fault": true`로 바뀜 (고장) |
| 90~155초 | ① | C-PR3 **측정값 없음**. 앞 공정(C-SL, C-PR1·2)은 계속 나옴 |
| 90~155초 | ② | `sfaas/hb/C-PR3`는 **계속** 나오고 그동안 `"fault": true` |
| 151초쯤 | ① | `sfaas/raw/VI` … `"visionPass": false` (★ E라인 비전 불량) |
| 155초 | ② | `sfaas/hb/C-PR3` … `"fault": false`로 돌아옴 (복구) |
| 처음~끝 | ① | `sfaas/raw/C-PR3`에 `fault` 메시지는 **한 번도 안 나옴** (고장은 상태 신호로만) |
| 180초 | ③ | 요약 출력 후 끝 |

다시 하려면 그냥 `python player.py`를 다시 실행해요 (항상 처음부터 재생).
> 같은 날 두 번 재생하면 같은 제품 ID가 다시 들어가서 DB 중복 오류가 날 수 있어요.
> 그때는 DB 리셋(`demo_reset.sql`)을 먼저 하거나 `product_date`를 다른 날짜로 바꿔요.

### 3-3. 옵션 모음
| 옵션 | 설명 |
|---|---|
| `--dry-run` | MQTT로 보내지 않고 화면에만 출력 |
| `--speed 2` | 2배 빠르게 (테스트용) |
| `--manual` | 자동 고장 일정을 끄고 키보드로만 고장·복구 |
| `--quiet` | dry-run 때 메시지 한 줄씩 출력하지 않기 |
| `--config 다른설정.json` | 다른 설정 파일 사용 |

---

## 4. 실행 중 키보드 조작

재생 중인 터미널(③)에 글자를 치고 **Enter**를 눌러요.

| 입력 | 하는 일 |
|---|---|
| `f C-PR3` | C-PR3 상태 신호를 `"fault": true`로 바꾸기 + **C-PR3와 뒤 공정**(C-PR4, C-WD, C라인 제품의 도장·검사) 값 전송 멈춤. 앞 공정은 계속 |
| `r C-PR3` | 상태 신호를 `"fault": false`로 되돌리기 + 멈췄던 값을 **밀린 줄부터 이어서** 보내기 (멈춘 시간만큼 뒤로 밀려요) |
| `c C-PR1` | **통신 끊김 흉내**: C-PR1의 값·상태 신호를 모두 보내지 않아요 (약 10초 뒤 화면이 DC가 돼야 해요) |
| `u C-PR1` | 통신 다시 연결: C-PR1 메시지를 다시 보내요 |
| `s` | 지금까지 보낸 개수, 고장·통신 끊김 중인 설비 보기 |
| `h` | 도움말 |
| `q` | 종료 (Ctrl+C도 OK) |

- 공용 설비를 고장 내면: `PT1` → PT1 값 + 그 라인들(A·B·C) 검사 결과가 멈춰요. `VI` → 검사 결과만 멈춰요.
- **고장(f)과 통신 끊김(c)의 차이**
  - 고장: 값이 **멈췄다가 복구 후 이어서** 나가요. 상태 신호는 `"fault": true`로 계속 나가요 → 화면 **D**
  - 통신 끊김: 그 설비 메시지가 **그동안 사라져요** (다시 연결해도 끊긴 동안의 값은 안 보내요) → 화면 **DC**
  - 끊김 중에 고장·복구 시각이 와도 설비 안의 고장 상태는 바뀌어요. 다시 연결하면 그때의 `fault` 값으로 상태 신호가 나가요.
  - 고장·복구는 다음 상태 신호(최대 1초 뒤)에 반영돼요.
- 수동 조작은 자동 모드에서도 쓸 수 있어요. 다만 기본 CSV에는 90~155초 C라인 고장이 이미 들어 있으니,
  **수동으로만 시연하려면 `--manual`**로 실행하는 게 깔끔해요.

---

## 5. 화면 보는 법
- 5초마다 회색 줄: `[ 85.0s] 보낸 개수: 슬리팅 30 · 프레스 40 · …`
- **빨간 ★ 줄**: 일부러 넣은 불량 줄을 보낼 때
- **파란 ◆ 코일 입고**: 코일 입고 메시지를 보낼 때
- **빨간 ■ 고장 ▼ / 초록 ■ 복구 ▲**: 상태 신호의 `fault` 값이 바뀔 때
- **노란 ✂ / 초록 ↺**: 통신 끊김 흉내 시작 / 다시 연결
- dry-run에서는 `♥ 상태 신호 39대` 줄이 1초마다 나와요 (고장 중이면 `(fault: true → C-PR3)`이 붙어요)
- 끝나면 요약: 종류별로 보낸 개수 + 코일 + 고장 전환 횟수 + 상태 신호 개수

---

## 6. 보내는 메시지 모양 (API 명세서 v3)

`ts`는 **실제로 보낸 시각**이에요 (한국 시간, 예: `2026-11-18T09:30:01.500+09:00`).

| API ID | 토픽 | 내용 | QoS |
|---|---|---|---|
| Q-01 | `sfaas/raw/{설비ID}` | 측정값 (슬리팅·프레스·용접·도장) | 0 |
| Q-02 | `sfaas/raw/VI` | 비전 검사 결과 | 1 |
| Q-04 | `sfaas/hb/{설비ID}` | 상태 신호 + 고장 여부 `fault` — 39대 모두 1초마다, 고장 중에도 | 0 |
| (코일) | `sfaas/raw/COIL` | 코일 입고 (1롤 = 1건) — 기본 0초에 4롤 | 1 |

고장·복구는 **따로 메시지를 보내지 않아요.** 상태 신호의 `fault` 값(true/false)으로만 알려요.

```json
// 🆕 v0.3.8: 모든 측정값에 productId (슬리팅 투입 때 생긴 제품 번호, 날짜는 실행한 날)
// Q-01 측정값 (가상 슬리팅 — 값 없음, 제품 번호가 여기서 생김)
{"equipmentId": "A-SL", "ts": "...", "source": "REPLAY", "productId": "A-20261117-0001", "values": {}}
// Q-01 측정값 (프레스 — 프레스마다 자기 항목 1개만: PR1 pressureLeft · PR2 pressureRight · PR3 pressureDiff · PR4 pressTime)
{"equipmentId": "A-PR1", "ts": "...", "source": "REPLAY", "productId": "A-20261117-0001", "values": {"pressureLeft": 275.2}}
{"equipmentId": "A-PR2", "ts": "...", "source": "REPLAY", "productId": "A-20261117-0001", "values": {"pressureRight": 273.2}}
// Q-01 측정값 (용접)
{"equipmentId": "A-WD", "ts": "...", "source": "REPLAY", "productId": "A-20261117-0001", "values": {"pressForce": 2.33, "current": 14.57, "voltage": 2.701, "weldTime": 72.0}}
// Q-01 측정값 (도장 — lineId: 어느 라인 제품인지, 도장 투입 때의 용액 상태)
{"equipmentId": "PT1", "ts": "...", "source": "REPLAY", "productId": "A-20261117-0001", "lineId": "A", "values": {"ph": 2.84, "temperature": 27.92, "current": 3.11, "voltage": 3.43}}
// Q-02 비전 검사 결과 (OK → true, NG → false)
{"ts": "...", "productId": "C-20261118-0001", "lineId": "C", "paintId": "PT1", "visionPass": true}
// Q-04 상태 신호 (평소)
{"equipmentId": "A-PR1", "ts": "...", "fault": false}
// Q-04 상태 신호 (고장 중)
{"equipmentId": "C-PR3", "ts": "...", "fault": true}
// 코일 입고
{"materialId": "COIL-20261118-001", "ts": "...", "productQty": 20}
```

- 받는 쪽 판정 (참고): 상태 신호 O + 제품 없음 → **I** · 상태 신호 10초 이상 끊김 → **DC** · `"fault": true` → **D**
- 🔧 **메시지 모양을 바꿔야 할 때:** `player.py`의 "메시지 만들기" 부분(`msg_values`, `msg_vision`, `msg_heartbeat`, `msg_coil`)만 고치면 돼요.
  상태 신호(고장 여부 포함)는 **`msg_heartbeat` 함수 하나**에서 만들어요.

---

## 7. 문제 해결
| 증상 | 해결 |
|---|---|
| `paho-mqtt가 설치되지 않았어요` | `conda activate sfaas-player`를 먼저 했는지 확인 → 그래도 안 되면 `pip install -r requirements.txt` |
| `MQTT 브로커에 연결할 수 없어요` | Mosquitto 서비스 확인 (`Get-Service mosquitto`), `config.json`의 주소·포트 확인 |
| `CSV 파일이 없어요` | `config.json`의 `csv_dir`·`csv_suffix` 확인 |
| 한글이 깨져 보여요 | Windows Terminal 또는 PowerShell 사용, 또는 `chcp 65001` 입력 후 실행 |
| 터미널 ②가 너무 빨리 올라가요 | 정상이에요 (1초마다 39줄). 한 설비만 보려면 `-t "sfaas/hb/C-PR3"` |
