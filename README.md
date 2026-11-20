# SFaaS 수집 파이프라인 (가상 설비 → MQTT → Node-RED → DB)

> 1조 가동가동해 · 라인 가동 현황 모니터링 시스템 · DB v0.3.9 · 2026-11-19
> 이 저장소 하나 + 백엔드·프론트 저장소로 **내 PC에서 전체 과정을 처음부터 끝까지** 돌릴 수 있어요.

```
[player.py] 가상 설비 39대 + 코일 입고 (리허설 CSV 재생)
     │ MQTT (sfaas/raw/#, sfaas/hb/#, sfaas/ctl/replay)
     ▼
[Mosquitto] localhost:1883
     ▼
[Node-RED] 정제만 하고 DB에 저장 (판정 안 함)          ← nodered/flows_now/kamp_flows.json
     ▼
[PostgreSQL] sfaas DB                                     ← schema.sql · grants.sql · seed.sql
     ▼
[Spring Boot 백엔드] 1초마다 판정(OK/NG, 설비 상태) + REST API   ← GadongGadonghae/Back-End
     ▼
[Vue 프론트] 3D 대시보드·각 화면                          ← GadongGadonghae/Front-end
```

**걸리는 시간:** 처음 설치 1~2시간 (프로그램 설치 포함) · 그다음부터는 매번 5분 + 재생 3분

---

## 0. 이 문서에서 쓰는 폴더 위치

아래처럼 **`C:\sfaas` 한 폴더에 저장소 3개**를 받는다고 가정하고 설명해요. 다른 위치에 받았으면 명령의 경로만 바꾸면 돼요.

```
C:\sfaas\
├─ IoT-Pipeline\   ← 이 저장소 (player, Node-RED, DB SQL, 리허설 CSV)
├─ Back-End\       ← 백엔드
└─ Front-end\      ← 프론트
```

⚠️ SQL Shell의 `\i` 명령은 경로에 **슬래시(`/`)** 를 써요: `C:/sfaas/IoT-Pipeline/schema.sql`

---

## 1. 처음 한 번만: 프로그램 설치

| 프로그램 | 버전 | 받는 곳 | 확인 명령 (PowerShell) |
|---|---|---|---|
| Git | 아무거나 | https://git-scm.com | `git --version` |
| PostgreSQL | **17** | https://www.postgresql.org/download/windows/ (EDB 설치 파일) | 시작 메뉴에 **SQL Shell (psql)** 이 있으면 OK |
| Mosquitto | 2.x | https://mosquitto.org/download/ (Windows x64) | `Get-Service mosquitto` → `Running` |
| Node.js | **22 LTS** (20.19 이상) | https://nodejs.org | `node -v` |
| Java (JDK) | **17** | https://adoptium.net (Temurin 17) | `java -version` → `17.x` |
| Anaconda 또는 Python | Python 3.10 이상 | https://www.anaconda.com/download | `python --version` |

설치할 때 참고:
- **PostgreSQL:** 설치 중 `postgres` 관리자 비밀번호를 정해요. **꼭 기억하세요** (아래에서 계속 써요). 포트는 기본 5432, 나머지는 기본값으로 Next.
- **Mosquitto:** 기본값으로 설치하면 Windows 서비스로 자동 실행돼요. `Stopped`이면 **관리자 PowerShell**에서 `Start-Service mosquitto`.
- **Node.js:** 설치 마지막의 "필요한 도구 설치(Chocolatey)" 체크는 안 해도 돼요.
- 설치 후 **PowerShell을 새로 열어야** 새 명령(`node`, `java` 등)이 인식돼요.

---

## 2. 처음 한 번만: 저장소 받기

PowerShell에서:
```powershell
mkdir C:\sfaas
cd C:\sfaas
git clone https://github.com/GadongGadonghae/IoT-Pipeline.git
git clone -b fix/first-idle-and-actual-qty https://github.com/GadongGadonghae/Back-End.git
git clone -b fix/demo-live https://github.com/GadongGadonghae/Front-end.git
```

> 백엔드·프론트는 지금 **PM 작업 브랜치**를 받아요 (코일·3D 물류·판정 수정이 들어 있음).
> 이 브랜치가 팀 브랜치에 합쳐진 뒤에는 `-b develop`(백엔드), `-b feat/factory-3d`(프론트)로 받으면 돼요.
> 이미 받아 둔 저장소가 있으면: `cd C:\sfaas\Back-End` → `git fetch` → `git switch fix/first-idle-and-actual-qty` (프론트도 같은 방식)

---

## 3. 처음 한 번만: DB 만들기

**시작 메뉴 → SQL Shell (psql)** 을 열어요. 물어보는 것 5개 중 **비밀번호만 입력**하고 나머지는 Enter:
```
Server [localhost]:            ← Enter
Database [postgres]:           ← Enter
Port [5432]:                   ← Enter
Username [postgres]:           ← Enter
postgres 사용자의 암호:         ← 1단계에서 정한 postgres 비밀번호
```

프롬프트가 `postgres=#`이면 아래를 **한 줄씩** 붙여 넣어요.

**① 계정 2개 + DB 만들기** — `'비밀번호1'`, `'비밀번호2'`는 **직접 정한 값으로 바꿔서** 넣어요 (작은따옴표는 그대로)
```sql
CREATE ROLE sfaas_be LOGIN PASSWORD '비밀번호1';
CREATE ROLE sfaas_nr LOGIN PASSWORD '비밀번호2';
CREATE DATABASE sfaas ENCODING 'UTF8' TEMPLATE template0;
```
- `sfaas_be` = 백엔드가 쓰는 계정 → 6단계 `application-local.yml`에 넣어요
- `sfaas_nr` = Node-RED가 쓰는 계정 → 4단계 Node-RED DB 설정에 넣어요
- 각각 `CREATE ROLE`, `CREATE DATABASE`가 나오면 OK

**② 테이블·권한·기준정보·로그인 계정 넣기**
```
\c sfaas
\encoding UTF8
\i C:/sfaas/IoT-Pipeline/schema.sql
\i C:/sfaas/IoT-Pipeline/grants.sql
\i C:/sfaas/IoT-Pipeline/seed.sql
\i C:/sfaas/IoT-Pipeline/demo_user.sql
\encoding UHC
```
- 프롬프트가 `sfaas=#`으로 바뀌어야 해요
- 마지막에 `abc | 데모 관리자 | MANAGER` 한 줄이 보이면 성공 (화면 로그인 계정 **abc / 1234**)
- `\encoding UTF8`은 SQL 파일(한글)을 읽을 때, `\encoding UHC`는 한글 결과를 화면에 볼 때 써요

| 파일 | 하는 일 |
|---|---|
| `schema.sql` | 테이블을 **전부 지우고 새로 만들기** (구조가 바뀐 날에만 다시 실행) |
| `grants.sql` | 계정별 권한 (Node-RED는 수집 테이블에 저장만) |
| `seed.sql` | 기준정보: 공정 6 · 라인 6 · 설비 39 · 비가동 코드 · 관리값 기준 |
| `demo_user.sql` | 데모 로그인 계정 abc / 1234 |
| `demo_reset.sql` | **기록만 비우기** (매번 재생 전에) |

> 🆕 **11/19 DB v0.3.10 (AI 계획수정 보고서 저장):** 위 순서로 새로 만들면 이미 들어 있어요.
> **11/19 전에 DB를 만들었다면** 아래 한 줄만 더 실행하세요 (기존 데이터는 그대로, 여러 번 실행해도 괜찮아요). 안 하면 백엔드가 `missing column [ai_content]`로 안 켜져요.
> ```
> \c sfaas
> \i C:/sfaas/IoT-Pipeline/alter_v0.3.10.sql
> ```
> 마지막에 `kind`, `remaining_plan` 두 줄이 나오면 성공 (중간의 "이미 있습니다" 안내는 정상)

---

## 4. 처음 한 번만: Node-RED

**① 설치·실행** (PowerShell)
```powershell
npm install -g --unsafe-perm node-red
node-red
```
`Server now running at http://127.0.0.1:1880/` 가 나오면 이 창은 **켜 둔 채로** 두고, 브라우저로 http://127.0.0.1:1880 을 열어요.
(Windows 방화벽 창이 뜨면 "허용"을 눌러도 되고 취소해도 돼요. 이 PC 안에서만 쓰니까요.)

**② PostgreSQL 노드 설치**
오른쪽 위 **≡ 메뉴 → Manage palette → Install 탭** → `node-red-contrib-postgresql` 검색 → **install** → 다시 install

**③ 수집 플로우 가져오기**
**≡ 메뉴 → Import → select a file to import** → `C:\sfaas\IoT-Pipeline\nodered\flows_now\kamp_flows.json` → **Import**
- `SFaaS 수집 (정제 → DB)` 탭이 생겨요
- ⚠️ `plc_flows.json`(A-SL 실제 인버터 탭)은 **가져오지 마세요.** 교육장의 실제 설비용이고, 추가 노드가 필요해요

**④ DB 비밀번호 넣기**
1. `DB 저장 (측정값·검사·코일)` 노드를 **더블클릭**
2. Connection 칸 `sfaas DB (sfaas_nr)` 오른쪽 **연필(✏️)** 클릭
3. **Security** 탭: User `sfaas_nr`, Password `비밀번호2` (3단계 ①에서 정한 값)
4. **Update → Done**
5. 오른쪽 위 빨간 **Deploy** 클릭

**⑤ 확인**
- MQTT 노드 2개(`sfaas/raw/#`, `sfaas/hb/#`) 아래에 초록 **connected**
- 탭이 흐리게(꺼짐) 보이면: 탭 이름 더블클릭 → 아래쪽 **Enabled** 켜기 → Done → Deploy

---

## 5. 처음 한 번만: player(가상 설비)

PowerShell(또는 Anaconda Prompt)에서:
```powershell
conda create -n sfaas-player python=3.13 -y
conda activate sfaas-player
cd C:\sfaas\IoT-Pipeline\player
pip install -r requirements.txt
```
- `conda activate`가 안 되면 한 번만 `conda init powershell` 실행 후 PowerShell을 다시 열어요
- Anaconda 없이 Python만 있으면 `conda` 두 줄은 건너뛰고 `pip install -r requirements.txt`만 해도 돼요

**브로커 없이 동작 확인** (18초):
```powershell
python player.py --config config_noplc.json --dry-run --quiet --speed 10
```
끝에 `=== 재생 완료 요약 ===`과 `슬리팅 66 · 프레스 170 · 용접 26 · 도장 25 · 검사 24 · 코일 4 · 고장 전환 2 …`가 나오면 OK.

> **`config_noplc.json`을 쓰는 이유:** 기본 `config.json`은 교육장의 실제 인버터가 A-SL 상태 신호를 보낸다고 가정해서 A-SL 신호를 안 보내요. 인버터 없이 돌리면 A-SL만 계속 "통신 끊김(DC)"으로 보여요. **인버터가 없는 PC에서는 항상 `_noplc` 설정**을 쓰세요 (3분 `config_noplc.json` · 5분 `config_5min_noplc.json` · 10분 `config_10min_noplc.json`).

---

## 6. 처음 한 번만: 백엔드

**① 로컬 설정 파일 만들기** (PowerShell)
```powershell
cd C:\sfaas\Back-End\src\main\resources
copy application-local.yml.example application-local.yml
[Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }))
notepad application-local.yml
```
세 번째 줄이 출력한 긴 글자(JWT 키)를 복사해 두고, 메모장 내용을 **전부 지우고** 아래로 바꿔 저장해요:
```yaml
spring:
  datasource:
    username: sfaas_be
    password: 비밀번호1

jwt:
  secret: 위에서-복사한-긴-글자

mqtt:
  client-id: sfaas-be-내이름

# AI 계획수정 보고서 (선택). Google Gemini 무료 키: https://aistudio.google.com → Get API key → Create API key
ai:
  report:
    api-key: 여기에_Gemini_키
```
- `password`는 3단계 ①의 `sfaas_be` 비밀번호
- `ai` 부분은 빼도 돼요. 키가 없으면 AI 보고서가 템플릿 문장으로 만들어지고 화면에 "AI 생성 실패 · 템플릿 문장"이라고 나와요
- DB 주소는 기본값(`localhost`)을 쓰니 `url` 줄은 안 써요
- 이 파일은 Git에 올라가지 않아요 (비밀번호 보호)

**② 실행 확인**
```powershell
cd C:\sfaas\Back-End
.\gradlew.bat bootRun
```
- 처음엔 라이브러리를 받느라 몇 분 걸려요
- `Started SfaasApplication`이 나오면 OK. 그 아래 `80% EXECUTING`에서 멈춰 있는 건 **정상** (서버가 켜져 있다는 뜻)
- 끄기: `Ctrl+C`

---

## 7. 처음 한 번만: 프론트

```powershell
cd C:\sfaas\Front-end
npm install
copy .env.example .env.local
notepad .env.local
```
메모장에서 `VITE_DATA_SOURCE=mock`을 **`VITE_DATA_SOURCE=api`** 로 바꾸고 저장해요 (나머지 줄은 그대로).

---

## 8. ⚡ 매번 돌리는 순서

창을 **4개** 열어요. ①②③은 켜 둔 채로 둬요.

| 창 | 하는 일 | 명령 | 정상 표시 |
|---|---|---|---|
| (확인) | Mosquitto | `Get-Service mosquitto` | `Running` |
| ① | Node-RED | `node-red` | http://127.0.0.1:1880 에서 MQTT 노드 **connected** |
| ② | 백엔드 | `cd C:\sfaas\Back-End` → `.\gradlew.bat bootRun` | `Started SfaasApplication` |
| ③ | 프론트 | `cd C:\sfaas\Front-end` → `npm run dev` | `Local: http://localhost:5173/` |
| ④ | DB 비우기 (SQL Shell) | 아래 ④ | `sfaas=#` |
| ⑤ | 재생 (PowerShell) | 아래 ⑤ | `=== 재생 완료 요약 ===` |

**④ DB 기록 비우기** (SQL Shell, postgres로 접속)
```
\c sfaas
\encoding UTF8
\i C:/sfaas/IoT-Pipeline/demo_reset.sql
\encoding UHC
```
- 측정값·상태 이력·비가동·코일 등 **기록만** 비워요. 기준정보와 로그인 계정은 남아요
- **같은 날 재생을 다시 할 때는 꼭 먼저 비워요** (제품 번호가 겹쳐요)

**브라우저:** http://localhost:5173 → `abc` / `1234` 로그인 → **F5(새로고침)** 한 번 (DB를 비운 뒤 화면에 옛 알림이 남지 않게)

**⑤ 재생** (새 PowerShell)
```powershell
conda activate sfaas-player
cd C:\sfaas\IoT-Pipeline\player
python player.py --config config_noplc.json
```
3분 걸려요. 테스트용으로 빠르게: 뒤에 `--speed 10` (18초)

---

## 9. 잘 됐는지 확인

### 화면 (3분 리허설 기준)
| 시각 | 보일 것 |
|---|---|
| 0초 | 코일 4롤 입고, 3D "코일 재고 4.0롤" → 슬리팅이 시작되면 3.7롤 |
| 84초 | B-PR2 공정 불량 1개 (B-…-0003, 우 압력 278.4) → 그 제품은 B-PR2에서 사라짐 |
| 90초 | **C-PR3 장비고장(빨강) + 고장 팝업** |
| 107초 | 첫 완제품 |
| 130초 | 비전 불량 1개 (E-…-0002) |
| 155초 | C-PR3 복구 |
| 끝 | 출고 양품(실적) **23** · 검사 24 · 비전 불량 1 · 공정 불량 1 · 코일 재고 0.7롤 |
| 끝 + 10초 | 39대 모두 DC(통신 끊김) — 재생이 끝나 신호가 멈췄으니 정상 |

### API로 확인 (선택, 백엔드가 켜져 있을 때)
```powershell
cd C:\sfaas\IoT-Pipeline\player
powershell -ExecutionPolicy Bypass -File .\api_check.ps1 live    # 재생 중 100~150초쯤
powershell -ExecutionPolicy Bypass -File .\api_check.ps1         # 재생 끝난 뒤
```
결과마다 바로 아래에 **[기대]** 값이 같이 나와요. 숫자가 같으면 정상이에요.

### DB로 확인 (선택, SQL Shell)
```sql
SELECT COUNT(*) AS flow, COUNT(ended_at) AS ended FROM product_flow;
SELECT (SELECT COUNT(*) FROM slitting_data), (SELECT COUNT(*) FROM press_data), (SELECT COUNT(*) FROM welding_data),
       (SELECT COUNT(*) FROM painting_data), (SELECT COUNT(*) FROM inspection_result);
```
기대: **311 · 311** / **66 · 54 · 26 · 25 · 24**

시나리오 전체 설명과 기대값: `rehearsal_3min/시나리오_3분.md` (5분·10분도 같은 폴더 구조)

---

## 10. 문제가 생기면

| 증상 | 원인 · 해결 |
|---|---|
| SQL Shell 한글이 깨짐 | 파일 실행 전 `\encoding UTF8`, 결과 볼 땐 `\encoding UHC` |
| SQL Shell 프롬프트가 `postgres-#` | 입력이 덜 끝난 상태. `\r` 입력 후 다시. `sfaas=#`이 아니면 `\c sfaas` |
| `role "sfaas_be" does not exist` | 3단계 ①의 `CREATE ROLE`을 안 했어요 |
| 백엔드 `password authentication failed` | `application-local.yml`의 `username`(sfaas_be)·`password` 확인 |
| 백엔드 `missing column [ai_content]` | 11/19 전에 만든 DB. 3단계 아래의 `alter_v0.3.10.sql` 실행 |
| 백엔드 `Port 8080 was already in use` | 백엔드가 이미 켜져 있어요. 다른 창의 백엔드를 `Ctrl+C`로 끄기 |
| 백엔드 `release version 17 not supported` 등 | JDK 17이 아니에요. `java -version` 확인 |
| 프론트 `ECONNREFUSED` / `http proxy error` | 백엔드가 꺼져 있어요 (②번 창) |
| 화면에 "데모 데이터" 표시가 있음 | `.env.local`이 `VITE_DATA_SOURCE=api`인지 확인 후 `npm run dev` 다시 |
| 로그인 안 됨 | `demo_user.sql`을 실행했는지 (`SELECT login_id FROM users;`에 abc) |
| Node-RED MQTT 노드가 `disconnected` | Mosquitto가 꺼져 있어요 (`Start-Service mosquitto`, 관리자) |
| Node-RED DB 노드 `Error` | 4단계 ④ 비밀번호. 🐞 Debug 탭의 빨간 메시지 확인 |
| Node-RED `permission denied for table …` | `grants.sql`을 다시 실행 (`\c sfaas` → `\encoding UTF8` → `\i …/grants.sql`) |
| A-SL만 계속 DC | `--config config_noplc.json` 없이 실행했어요 |
| 재생했는데 화면 숫자가 0 | Node-RED 수집 탭이 꺼져 있거나 Deploy 안 함. Debug 탭 확인 |
| `api_check.ps1` 한글이 깨지며 문법 오류 | 파일을 다른 편집기로 저장하면서 인코딩이 바뀜. 저장소 파일을 다시 받기 (`git checkout -- player/api_check.ps1`) |
| `conda activate`가 안 됨 | `conda init powershell` 후 PowerShell 다시 열기 |
| 같은 날 두 번째 재생 숫자가 이상함 | 재생 전에 8단계 ④(DB 비우기)를 안 했어요 |

### 다른 PC의 DB에 붙을 때 (선택)
DB·Node-RED·player는 한 PC(예: PM PC)에서 돌리고, 내 PC에서는 **백엔드·프론트만** 켜서 결과를 볼 때예요.
백엔드는 MQTT를 쓰지 않고 DB만 읽어서, DB에만 붙으면 돼요. 내 PC에는 3~5단계(DB·Node-RED·player)가 필요 없어요.

**① DB PC에서 (한 번만, 관리자 PowerShell)**
- 방화벽 열기: `New-NetFirewallRule -DisplayName "PostgreSQL 5432" -Direction Inbound -Protocol TCP -LocalPort 5432 -Action Allow -Profile Private`
- `C:\Program Files\PostgreSQL\17\data\pg_hba.conf` 맨 아래에 한 줄 추가 (같은 와이파이 대역, 예: 192.168.0.x):
  ```
  host    sfaas    sfaas_be    192.168.0.0/24    scram-sha-256
  ```
- `postgresql.conf`의 `listen_addresses`가 `'*'`인지 확인 (설치 기본값)
- PostgreSQL 다시 시작: `Restart-Service postgresql-x64-17`
- DB PC IP 확인: `ipconfig` → 무선 LAN의 IPv4 주소

**② 내 PC (백엔드) `application-local.yml`**
```yaml
spring:
  datasource:
    url: jdbc:postgresql://{DB PC IP}:5432/sfaas
    username: sfaas_be
    password: {DB PC의 sfaas_be 비밀번호}
```
- 접속 시험: `Test-NetConnection {DB PC IP} -Port 5432` → `TcpTestSucceeded : True`
- AI 보고서 키(`ai.report.api-key`)는 내 PC 설정에 넣어요 (6단계)

**③ 판정은 그 DB를 보는 백엔드 한 대만**
- DB PC에서 백엔드를 **안 켜면**: 내 백엔드가 판정해요. 그대로 두면 돼요 (기본값)
- DB PC에서도 백엔드를 **켜 두면**: 둘이 같이 판정해서 기록이 겹쳐요. 내 백엔드 판정을 꺼요:
  ```yaml
  sfaas:
    judge:
      enabled: false
  ```

---

## 11. 폴더 안내

| 위치 | 내용 |
|---|---|
| `schema.sql` · `grants.sql` · `seed.sql` · `demo_user.sql` · `demo_reset.sql` · `alter_v0.3.9.sql` · `alter_v0.3.10.sql` | DB (alter는 예전에 만든 DB를 새 버전으로 올릴 때만) |
| `player/` | 재생 프로그램 `player.py`, 설정(`config*.json`), API 확인 `api_check.ps1`, 자세한 설명 `README.md` |
| `nodered/flows_now/` | Node-RED 플로우: `kamp_flows.json`(수집, 필수) · `plc_flows.json`(교육장 실제 인버터용, 선택) |
| `nodered/*.js` | 플로우 안 함수 코드의 사본 (참고용) |
| `rehearsal_3min/` · `5min/` · `10min/` | 리허설 CSV + 시나리오·기대값 문서 + 결과 SQL(MQTT 없이 화면만 볼 때) |
| `tools/make_rehearsal.py` | 리허설 CSV 생성기 (KAMP 원본 데이터 필요, 보통은 안 써도 됨) |

**메시지 토픽**
| 토픽 | 내용 | 저장 테이블 |
|---|---|---|
| `sfaas/raw/COIL` | 코일 입고 (1롤 = 1건, 제품 20개분) | `coil_receipt` |
| `sfaas/raw/{설비ID}` | 측정값 (event `start` = 투입, `end` = 완료 + 값) | 공정 테이블 + `product_flow` |
| `sfaas/raw/VI` | 비전 검사 결과 (`visionPass` → OK/NG) | `inspection_result` + `product_flow` |
| `sfaas/hb/{설비ID}` | 상태 신호 1초마다 + 고장 여부 `fault` | `equipment_signal` |
| `sfaas/ctl/replay` | 재생 시작·끝 (`running`) — 인버터 탭이 재생 중일 때만 신호를 보내게 | (저장 안 함) |
