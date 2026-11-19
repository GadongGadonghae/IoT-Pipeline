# 🛠️ 개발 지시서 · KAMP CSV 재생 프로그램 (가상 설비)

> 라인 가동 현황 모니터링 시스템 · 1조 · 작성 2026-11-17 · 기준 DB v0.3.7 · **API 명세서 v3**
> 이 문서는 `sfaas/player/CLAUDE.md`예요. Claude Code가 이 폴더에서 시작하면 자동으로 읽어요.
> 담당: 정연창 (PM·DB, 데이터 수집 KAMP 쪽). PLC 쪽(슬리팅 실설비)은 다른 팀원이 맡아요.

> 🆕 **11/17 추가 요청 (이미 만든 player.py에 추가해 주세요)**
> 1. **생존 신호(heartbeat)** 전송 → 3-5절, F10, 7장 4번
> 2. 키보드로 **통신 끊김(DC) 흉내** → F11
> 3. 코일 입고는 DB에서 1롤 = 제품 20개로 정함 (🆕 4차: 코일 입고도 이 프로그램이 보내요 → 3-5절)

> 🆕 **11/17 2차 추가 요청: 메시지를 API 명세서 v3에 맞추기** (3장 전체를 새로 썼어요)
> 1. 측정값 이름을 **camelCase**로 (`pressure_left` → `pressureLeft`)
> 2. 검사 메시지를 `paintId`, `visionPass`(true/false)로
> 3. 생존 신호를 **`sfaas/hb/{equipmentId}` 토픽, 1초마다**로
> 4. 고장 신호 형식은 백엔드가 API에 정하는 중 → 지금 방식을 두되 **한 곳만 고치면 바뀌게**
> 5. 토픽 앞부분(`sfaas/raw`, `sfaas/hb`)을 설정으로 바꿀 수 있게

> 🆕 **11/17 3차 추가 요청: 1분 → 3분 리허설로 변경**
> 1. 입력 폴더를 `sfaas/rehearsal_3min/`으로 (파일 이름 `*_3min.csv`). 폴더·길이는 설정으로 바꿀 수 있게
> 2. **슬리팅 CSV가 생겼어요** (`slitting_3min.csv`, 값 없는 생산 신호) → 프로그램 안에서 만들던 슬리팅 신호 대신 이 파일을 써요
> 3. 고장 일정: C-PR3 **90초 고장 → 155초 복구** (기존 30·50초)
> 4. 완료 확인 숫자가 바뀌었어요 (F6, 7장). 🆕 11/17 처리 시간 재조정(프레스 1대당 17초·도장 4·검사 7초)으로 숫자를 한 번 더 고쳤어요
> 5. 받는 쪽 구조 확정 (참고): Node-RED가 원시 값을 DB에 바로 저장하고, 백엔드가 DB를 읽어 판정해요. 상태·고장 신호는 Node-RED가 DB `equipment_signal`에 기록해요

> 🆕 **11/17 4차 추가 요청 (API 명세서 v3 대조 결과)**
> 1. **코일 입고 전송** 추가: 재생 시작할 때 `sfaas/raw/COIL`로 코일 4롤 (3-5절, F12). 슬리팅 앞 재공이 이걸로 채워져요
> 2. **고장 신호 형식 확정**: 따로 보내던 `fault: 1/0` 메시지를 없애고, **상태 신호(heartbeat) 안의 `fault: true/false`**로 보내요 (API v3 Q-04, 3-3·3-4절)
> 3. 비전 검사 메시지(`visionPass`)는 **지금 그대로** 둬요. OK/NG로 바꾸는 건 Node-RED가 해요

---

## 0. Claude Code에게 먼저 (작업 규칙)

- 사용자는 소프트웨어 개발이 처음이에요. **쉬운 말로, 실행 방법은 단계별로** 설명해 주세요.
- 작업 폴더는 `C:\jyc\sfaas_project`예요. **새 파일은 `sfaas/player/` 안에만** 만들어요.
- `sfaas/player/` 밖의 파일(schema.sql, seed.sql, rehearsal_1min/ 등)을 **고치거나 지우거나 옮기기 전에는 반드시 사용자에게 먼저 허락**을 받아요. 무엇을 어떻게 바꿀지 먼저 보여주고 승인받은 뒤 실행해요.
- 환경: **Windows**, Python 3. 코드 주석과 안내 문구는 **한국어**로 써요.
- Docker는 쓰지 않아요.

---

## 1. 이 프로그램이 하는 일

시연·리허설 때 **가상 설비 33대**(프레스 24, 용접 6, 도장 2, 비전 검사 1)와 **가상 슬리팅 6대** 역할을 해요. 🆕 **코일 입고**(설비 없음)도 보내요.

1. 미리 만든 CSV를 읽어서, **정해진 시각(`planned_offset_sec`)이 되면 한 줄씩 MQTT로 보내요.**
2. **고장·복구 신호**를 보내요 (자동 시나리오 또는 키보드 입력).
3. CSV 끝까지 보내면 멈춰요. 다시 할 때는 처음부터 재생해요.

```
[이 프로그램] ──MQTT──> Mosquitto(1883) ──> Node-RED(수집만) ──> Spring Boot(판정·저장·조회) ──> PostgreSQL / Vue
```

이 프로그램은 **원시 값만** 보내요. 정상/불량(OK·NG) 판정, 상태(R/S/I/D/DC) 판정, 알람, DB 저장은 모두 Spring Boot가 해요 (11/17 결정). OPC UA는 쓰지 않아요.

---

## 2. 입력 파일 (이미 있음)

위치: `C:\jyc\sfaas_project\sfaas\rehearsal_3min\` (UTF-8 BOM, 쉼표 구분) · 🆕 11/17 3분 리허설로 변경

| 파일 | 줄 수 | 칸 |
|---|---|---|
| `slitting_3min.csv` | 60 | planned_offset_sec, equipment_id, row_no, 🆕 product_id, scenario_note (값 없음) |
| `press_3min.csv` | 163 | planned_offset_sec, equipment_id, row_no, 🆕 product_id, pressure_left, pressure_right, 🆕 pressure_diff, press_time, scenario_note (🆕 줄마다 그 프레스 항목 1개만 채워짐, 나머지 빈칸) |
| `welding_3min.csv` | 26 | planned_offset_sec, equipment_id, row_no, 🆕 product_id, press_force, current, voltage, weld_time, scenario_note |
| `painting_3min.csv` | 23 | planned_offset_sec, equipment_id, row_no, 🆕 product_id, line_id, ph, temperature, current, voltage, scenario_note |
| `inspection_3min.csv` | 11 | planned_offset_sec, equipment_id, product_id, line_id, paint_equipment_id, result, scenario_note |

- `planned_offset_sec`: 재생 시작 후 이 줄을 보낼 시각(초). **0.1초 단위**예요.
- `scenario_note`가 ★로 시작하는 줄은 일부러 넣은 불량이에요 (보낼 때는 이 칸을 빼요).
- 시나리오 설명: 같은 폴더의 `시나리오_3분.md`
- CSV는 `sfaas/tools/make_rehearsal.py`로 만들었어요. 더 긴 시연용 CSV도 이 스크립트로 만들 수 있어요.

### 슬리팅 (가상 생산 신호)

슬리팅은 PLC 실설비 담당이지만, PLC를 연결하기 전까지는 이 프로그램이 `slitting_3min.csv`대로 **슬리팅 6대(A-SL ~ F-SL)의 생산 신호**를 보내요 (`values: {}`).
- 나중에 PLC가 연결되면 해당 설비(실물은 A-SL 1대)는 이 프로그램에서 **끌 수 있게** 설정을 둬요.

---

## 3. 보내는 메시지 (MQTT) — API 명세서 v3 기준

- 브로커: 기본 `localhost:1883` (설정으로 바꿀 수 있게)
- 시각 `ts`: **실제로 보낸 시각**, ISO-8601 한국 시간 (예: `2026-11-18T09:30:01.500+09:00`)
- 토픽 앞부분은 설정으로: 기본 `sfaas/raw`(값), `sfaas/hb`(상태 신호). Node-RED가 중간에서 받아 다시 보내는 구조로 바뀌면 앞부분만 바꾸면 되게 해 주세요.

| API ID | 토픽 | 내용 | QoS | 보내는 때 |
|---|---|---|---|---|
| Q-01 | `sfaas/raw/{equipmentId}` | 측정값 (프레스·용접·도장·슬리팅) | 0 | CSV 시각에 맞춰 제품 1개마다 |
| Q-02 | `sfaas/raw/VI` | 비전 검사 결과 | 1 | CSV 시각에 맞춰 제품 1개마다 |
| Q-04 | `sfaas/hb/{equipmentId}` | 상태 신호 (heartbeat) + 고장 여부 `fault` | 0 | **1초마다, 39대 모두** |
| 🆕 (코일) | `sfaas/raw/COIL` | 코일 입고 (1롤 = 1건) | 1 | 재생 시작할 때 (설정의 입고 일정대로) |

### 3-1. 측정값 (Q-01)
```json
{ "equipmentId": "A-PR1", "ts": "2026-11-18T09:30:01+09:00", "source": "REPLAY",
  "productId": "A-20261118-0001", "values": { "pressureLeft": 275.1 } }
```
- `values` 이름은 **DB 컬럼명을 camelCase로** 바꾼 것이에요 (CSV 칸 이름 → camelCase 변환)
  - 🆕 (v0.3.8) 모든 측정값에 `productId` (CSV `product_id`, 날짜 부분은 재생하는 날짜로). 빈 칸은 보내지 않아요
  - 프레스 (🆕 프레스마다 1개만) PR1 `pressureLeft` · PR2 `pressureRight` · PR3 `pressureDiff` · PR4 `pressTime`
  - 용접 `pressForce`·`current`·`voltage`·`weldTime`
  - 도장 `ph`·`temperature`·`current`·`voltage`
  - 슬리팅 `torque`·`current`·`voltage` (가상 슬리팅은 값이 없어서 `values: {}`)
- 🆕 (v0.3.9, 11/18) **`event`**: `"start"` = 제품이 설비에 들어감 (CSV `planned_start_sec` 시각, `values` 없음) / `"end"` = 값을 쓰고 내보냄 (`planned_offset_sec` 시각). 검사(VI)도 같음 (start엔 `visionPass` 없음). 옛 CSV(`planned_start_sec` 칸 없음)면 end만 보냄. 같은 시각이면 end를 start보다 먼저 보냄. Node-RED (v0.3.9 확정): start → `product_flow`에만 줄 생성(`started_at`), end → 공정 테이블에 값 (v0.3.8 방식, `measured_at` = 끝난 시각, 프레스는 PR1 끝난 시각 고정) + 같은 SQL로 `product_flow.ended_at`
- **도장은 `"lineId": "C"`를 꼭 넣어요** (`values`와 같은 높이). 공용 설비라 어느 라인 제품인지 알아야 해요.
- 이름 규칙은 설정으로 snake_case로도 바꿀 수 있게 두면 좋아요 (기본은 camelCase).

### 3-2. 비전 검사 결과 (Q-02)
```json
{ "ts": "2026-11-18T09:32:00+09:00", "productId": "C-20261118-0123", "lineId": "C", "paintId": "PT1", "visionPass": false }
```
- CSV `result`가 `OK`면 `visionPass: true`, `NG`면 `false`
- CSV `paint_equipment_id` → `paintId`
- 제품 ID의 날짜 부분(20261118)은 **재생하는 날짜로 바꿀 수 있게** 해요 (같은 ID가 다시 들어오면 DB 중복 오류가 나요).

### 3-3. 상태 신호 (Q-04 heartbeat)
```json
{ "equipmentId": "A-PR1", "ts": "2026-11-18T09:30:01+09:00", "fault": false }
```
- 🆕 `fault`: 평소 `false`, 고장 중 `true` (API v3 Q-04와 같은 형식)
- 측정값은 제품이 있을 때만 나가서, 처리할 제품이 없는 설비(대기, I)와 통신 끊김(DC)을 구분하려고 따로 보내요.
- **39대 모두 1초마다** (가상 슬리팅 포함, 주기는 설정 가능). 설비별로 끌 수 있게 (PLC가 연결된 슬리팅 등)
- **고장 중에도 계속 보내요** (설비는 살아 있음)
- 받는 쪽 판정 (참고): 상태 신호가 오는데 제품이 없음 → I, 상태 신호가 10초 이상 끊김 → DC

### 3-4. 고장·복구 신호 (🆕 4차: 형식 확정)
고장·복구는 **따로 메시지를 보내지 않고**, 그 설비의 상태 신호(3-3)에 든 `fault` 값을 바꿔요.
```json
// 토픽 sfaas/hb/C-PR3 (1초마다)
{ "equipmentId": "C-PR3", "ts": "...", "fault": true }    // 고장 중
{ "equipmentId": "C-PR3", "ts": "...", "fault": false }   // 정상 (복구 후)
```
- 고장 동안 그 설비는 측정값을 보내지 않아요. 상태 신호는 계속 보내요 (`fault: true`로).
- 예전의 `sfaas/raw/{id}`로 보내던 `fault: 1/0` 메시지는 **없애요**.
- 토픽 이름은 API v3에서 아직 "제안"이라, 상태 신호 메시지를 만드는 함수는 **하나로 모아 두는 규칙을 그대로** 지켜 주세요.

### 3-5. 🆕 코일 입고 (4차)
```json
// 토픽 sfaas/raw/COIL
{ "materialId": "COIL-20261118-001", "ts": "2026-11-18T09:00:00+09:00", "productQty": 20 }
```
- 코일 1롤 = 메시지 1건. `productQty`는 그 롤로 만들 수 있는 제품 수 (기본 20)
- 3분 리허설: **재생 시작(0초)에 4롤** (80개분). 입고 일정(시각·롤 수)은 `config.json`에서 바꿀 수 있게
- `materialId`의 날짜 부분은 제품 ID처럼 **재생하는 날짜로** 바꿔요. 번호는 001부터
- Node-RED가 받아서 DB `coil_receipt`(material_id, received_at, product_qty)에 저장해요

---

## 4. 고장 시나리오와 조작

3분 리허설 시나리오 (CSV 시각은 이미 이 시나리오를 반영하고 있어요):

| 시각 | 사건 | 보내는 것 |
|---|---|---|
| 🆕 0초 | 코일 4롤 입고 | `sfaas/raw/COIL` 4건 |
| 86초 | B-PR2 공정 불량 1개 (우 압력 278.4) → 그 제품은 PR3로 안 감 | press CSV의 ★ 줄 (기준 밖 값) |
| 90초 | C-PR3 고장 | C-PR3 상태 신호 `fault: true` |
| 151초 | E라인 비전 불량 1개 | inspection CSV의 ★ 줄 (`visionPass: false`) |
| 155초 | C-PR3 복구 | C-PR3 상태 신호 `fault: false` |

**조작 방식 두 가지를 모두 지원해 주세요**
- **자동 모드 (기본):** 설정 파일의 고장 일정(설비, 고장 시각, 복구 시각)대로 신호를 보내요. 리허설은 이걸로 해요.
- **수동 모드:** 실행 중 키보드로 고장·복구를 보내요. 예: `f C-PR3` 입력 → 고장, `r C-PR3` → 복구, `q` → 종료
  - 수동 고장 중에는 **그 설비의 값 전송을 멈췄다가**, 복구하면 밀린 줄부터 이어서 보내요 (뒤 공정도 그만큼 밀리게 하면 더 좋아요).

---

## 5. 기능 요구사항

| # | 요구사항 |
|---|---|
| F1 | CSV 4개를 읽어 `planned_offset_sec` 순서로 합쳐서, 시작 시각 기준으로 **정해진 시각에** 보냄 (오차 ±0.2초 이내) |
| F2 | 슬리팅 6대 생산 신호 생성·전송 (2장 규칙). 설비별로 끌 수 있게 |
| F3 | 고장·복구 신호: 자동 일정 + 키보드 수동 |
| F4 | 재생 속도 배율 옵션 (예: `--speed 2`면 2배 빠르게. 테스트용) |
| F5 | 화면에 진행 상황 출력: 경과 초, 보낸 개수(설비 종류별), 불량 줄·고장 신호를 보낼 때 강조 표시 |
| F6 | 끝나면 요약 출력 (3분): 슬리팅 60 · 프레스 163 · 용접 26 · 도장 23 · 검사 11 · 🆕 코일 4 · 고장 전환 2 (고장 시작·복구) |
| F7 | `--dry-run`: MQTT로 보내지 않고 화면에만 출력 (브로커 없이 확인용) |
| F8 | 설정은 `config.json` 하나로: 브로커 주소·포트, CSV 폴더, 토픽 앞부분(`sfaas/raw`, `sfaas/hb`), 이름 규칙(기본 camelCase), 제품 ID 날짜, 고장 일정, 슬리팅 끄기 목록, 상태 신호 주기(기본 1초), 🆕 코일 입고 일정 |
| F9 | Ctrl+C로 깔끔하게 종료 |
| 🆕 F10 | **상태 신호(Q-04)**: `sfaas/hb/{equipmentId}`로 39대 모두 **1초마다** (설정 가능), 고장 중에도 전송, 설비별로 끌 수 있게 (PLC 연결된 슬리팅 등) |
| 🆕 F11 | **통신 끊김 흉내**: 키보드 `c C-PR1` → 그 설비의 값·생존 신호 전송을 모두 멈춤, `u C-PR1` → 다시 보냄 (DC 화면 테스트용) |
| 🆕 F12 | **코일 입고 (4차)**: 설정의 입고 일정대로 `sfaas/raw/COIL`에 1롤 1건 전송 (3-5절). 기본은 0초에 4롤 |
| 🆕 F13 | **고장 신호 (4차)**: 고장·복구는 상태 신호의 `fault` true/false로만 표시 (3-4절). 수동 `f`/`r` 키도 같은 방식 |

---

## 6. 만들 파일

```
sfaas/player/
├── CLAUDE.md                  (이 문서, 개발 지시서)
├── player.py                  재생 프로그램
├── config.json                설정
├── requirements.txt           paho-mqtt 등
└── README.md                  설치·실행 방법 (초보자용, 단계별)
```

---

## 7. 완료 확인 (테스트)

1. **브로커 없이:** `python player.py --dry-run --speed 10` → 18초 안에 끝나고 요약 숫자가 F6과 같음
2. **Mosquitto 설치 후:** 터미널 하나에서 `mosquitto_sub -h localhost -t "sfaas/raw/#" -v`, 다른 터미널에서 `python player.py`
   - 86초쯤 B-PR2의 `"pressureRight": 278.4`가 보임 (🆕 `productId` 포함)
   - 151초쯤 VI의 `"visionPass": false`가 보임 (E라인 2번째 제품)
   - 🆕 시작하자마자 `sfaas/raw/COIL` 4건 (`productQty: 20`)
   - 90~155초 사이 C-PR3 측정값이 없음 (상태 신호는 계속). C-PR3 앞 공정(C-SL, C-PR1·2)은 계속 보내고, 뒤 공정은 재공이 떨어지면 멈춤 (CSV에 이미 반영)
3. README대로 처음 보는 사람이 설치부터 실행까지 할 수 있음
4. 🆕 상태 신호: `mosquitto_sub -t "sfaas/hb/#" -v`에서 39대 각각 1초마다 보임. C-PR3 고장(90~155초) 중에도 C-PR3 상태 신호는 계속 보이고, 🆕 그동안만 `"fault": true` (나머지는 `false`)
7. 🆕 API v3 형식: 프레스 값이 `pressureLeft` 등 camelCase로 (v0.3.8: 프레스마다 1개, 모든 측정값에 `productId`), 검사 메시지에 `paintId`·`visionPass`가 있고 E라인 ★ 제품은 `visionPass: false`
5. 🆕 `c C-PR1` 입력 후 C-PR1의 메시지가 완전히 끊기고, `u C-PR1` 후 다시 보임
6. 끝 요약에 생존 신호 보낸 개수도 표시
8. 🆕 `sfaas/raw/C-PR3`로 `fault` 메시지가 **나가지 않음** (고장은 상태 신호로만)

Mosquitto 설치가 필요하면 Windows 설치 방법을 README에 같이 적어 주세요.

---

## 8. 참고 (DB v0.3.7 · API 명세서 v3)

- 설비 ID: `A-SL`, `A-PR1`~`4`, `A-WD` (B~F 동일), `PT1`, `PT2`, `VI`
- 기본 도장 경로: A·B·C → PT1, D·E·F → PT2
- 처리 시간(초): 슬리팅 16, 프레스 17 × 4대, 용접 17, 도장 4, 비전 검사 7 · 라인 박자 18초
- 관리값 기준 예 (프레스): 압력 합계 541~560, 좌 압력 270~285.3 → ★ 줄은 이 범위를 벗어나요
- 자세한 테이블 설명: `docs/db/DB_테이블정의서_v0.3.7.md`
