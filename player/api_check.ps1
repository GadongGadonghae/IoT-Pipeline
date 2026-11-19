# =====================================================================
# 백엔드 API 확인 스크립트 (3분 리허설 기준, 11/18 · 검사 2초 기준값)
#   재생 중 (100~150초쯤):  powershell -ExecutionPolicy Bypass -File .\api_check.ps1 live
#   재생 끝난 뒤:           powershell -ExecutionPolicy Bypass -File .\api_check.ps1
# 각 줄 오른쪽 [기대]와 비교하면 돼요. 백엔드(localhost:8080)가 켜져 있어야 해요.
# =====================================================================
param([string]$mode = "end")
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$api = "http://localhost:8080/api"
try {
    $login = Invoke-RestMethod -Method Post -Uri "$api/auth/login" -ContentType "application/json" `
        -Body '{"loginId":"abc","password":"1234"}'
} catch {
    Write-Host "로그인 실패: 백엔드가 켜져 있는지, abc/1234 계정이 있는지 확인해 주세요. ($($_.Exception.Message))" -ForegroundColor Red
    exit 1
}
$h = @{ Authorization = "Bearer $($login.accessToken)" }
$d = Get-Date -Format "yyyy-MM-dd"
$ymd = Get-Date -Format "yyyyMMdd"

function Get-Api($path) {
    try { return Invoke-RestMethod -Uri "$api$path" -Headers $h }
    catch { Write-Host "  ✗ $path 호출 실패: $($_.Exception.Message)" -ForegroundColor Red; return $null }
}
function Write-Title($t) { Write-Host ""; Write-Host "===== $t" -ForegroundColor Cyan }
function Write-Expect($t) { Write-Host "  [기대] $t" -ForegroundColor DarkGray }

# ---------------- 재생 중 확인 ----------------
if ($mode -eq "live") {
    Write-Title "① 설비 상태 (M-01 /monitoring/status)"
    $st = Get-Api "/monitoring/status"
    if ($st) {
        $st | Group-Object status | ForEach-Object { "  $($_.Name): $($_.Count)대" }
        $c = $st | Where-Object equipmentId -eq "C-PR3"
        "  C-PR3 → status=$($c.status), reasonCode=$($c.reasonCode), since=$($c.since)"
        Write-Expect "90~155초 사이면 C-PR3 status=D, reasonCode=UP_BREAKDOWN. C-PR4·C-WD는 재공이 떨어지면 I(UP_SHORTAGE)"
    }
    Write-Title "② 진행 중 비가동 (U-08 /downtime/events?inProgress=true)"
    $ev = Get-Api "/downtime/events?from=$d&to=$d&inProgress=true&minMinutes=0&size=100"
    if ($ev) {
        $ev.items | Select-Object equipmentId, status, reasonCode, startAt | Format-Table -AutoSize | Out-String | Write-Host
        Write-Expect "C-PR3 D UP_BREAKDOWN 이 보임 (90~155초 사이). 시작 직후 첫 대기는 여기 안 나옴"
    }
    Write-Title "③ 코일 (M-16 /coil/today)"
    $co = Get-Api "/coil/today"
    if ($co) {
        "  오늘 입고 $($co.receivedRolls)롤 ($($co.receivedQty)개) · 재고 $($co.stockRolls)롤 ($($co.stockQty)개) · 슬리팅 투입 $($co.inputQty)"
        Write-Expect "입고 4롤(80개) 그대로, 재고 = 80 − 투입 (16초마다 6개씩 줄어요, 예: 투입 36 → 재고 2.2롤 44개)"
    }
    exit 0
}

# ---------------- 재생 끝난 뒤 확인 ----------------
Write-Title "① 생산 수 (M-07 /production/counts)"
$pc = Get-Api "/production/counts?date=$d"
if ($pc) {
    "  검사 inspected=$($pc.inspected) · 비전불량 ngVision=$($pc.ngVision) · 출고양품 ok=$($pc.ok) · 공정불량 processNg=$($pc.processNg) · total=$($pc.total)"
    Write-Expect "inspected 24 · ngVision 1 · ok 23 · processNg 1"
    "  라인별: " + (($pc.byGroup | ForEach-Object { "$($_.key) $($_.good)/$($_.total)" }) -join " · ")
    Write-Expect "양품 → A 5 · B 4 · C 2 · D 5 · E 3 · F 4 (total = 검사 + 공정 불량)"
}

Write-Title "② 재공 (M-13 /production/wip)"
$wip = Get-Api "/production/wip"
if ($wip) {
    $wip | ForEach-Object { "  $($_.process) → $($_.nextProcess): $($_.wipQty)" }
    Write-Expect "코일→슬리팅 14 · 슬리팅→프레스 12 · 프레스→용접 5 · 용접→도장 1 · 도장→검사 1"
}

Write-Title "③ 공정 불량 (P-01 /production/process-ng)"
$ng = Get-Api "/production/process-ng?from=$d&to=$d"
if ($ng) {
    "  총 $($ng.total)건"
    $ng.items | ForEach-Object { "  $($_.productId) $($_.equipmentId) $($_.itemName)=$($_.measuredValue) (기준 $($_.lowerLimit)~$($_.upperLimit))" }
    Write-Expect "1건: B-$ymd-0003 B-PR2 pressure_right=278.4 (상한 276.3 초과)"
}

Write-Title "④ 제품 추적 (U-? /products/B-$ymd-0003)"
$pd = Get-Api "/products/B-$ymd-0003"
if ($pd) {
    "  finalResult=$($pd.finalResult) · 폐기 설비=$($pd.scrappedEquipmentId)"
    $pd.steps | ForEach-Object { "  $($_.equipmentId) 들어감 $($_.startedAt) → 나옴 $($_.endedAt) [$($_.result)]" }
    Write-Expect "B-SL → B-PR1 → B-PR2 에서 끝 (NG), 폐기 설비 B-PR2. 단계마다 들어감·나옴 시각 있음"
}

Write-Title "⑤ C-PR3 상태 이력 (U-12 /equipments/C-PR3/status-history)"
$sh = Get-Api "/equipments/C-PR3/status-history?from=$d&to=$d"
if ($sh) {
    $sh | Select-Object -Last 8 | ForEach-Object { "  $($_.status) $($_.startAt) → $($_.endAt) $($_.reasonCode)" }
    Write-Expect "D UP_BREAKDOWN 구간이 약 65초 (90→155초). 재생이 끝난 뒤엔 DC"
}

Write-Title "⑥ 비가동 사유 (U-08 /downtime/events, 1분 이상)"
$ev = Get-Api "/downtime/events?from=$d&to=$d&size=100"
if ($ev) {
    "  총 $($ev.total)건"
    $ev.items | Group-Object status, reasonCode | ForEach-Object { "  $($_.Name): $($_.Count)건" }
    $ev.items | Where-Object status -eq "D" | ForEach-Object { "  D → $($_.equipmentId) $($_.reasonCode) $($_.minutes)분 detail=$($_.detail)" }
    $ev.items | Where-Object status -eq "I" | ForEach-Object { "  I → $($_.equipmentId) $($_.reasonCode) $($_.minutes)분" }
    Write-Expect "D UP_BREAKDOWN 1건 (C-PR3) + I UP_SHORTAGE 2건 정도 (C-PR4·C-WD, C-PR3 고장 영향). 시작 직후 첫 대기는 없어야 함"
}

Write-Title "⑦ 사이클 (M-15 /monitoring/processes/PRESS/cycles?lineId=C)"
$cy = Get-Api "/monitoring/processes/PRESS/cycles?lineId=C&date=$d"
if ($cy) {
    $cy | ForEach-Object { "  $($_.equipmentId): $($_.cycles)개, CT $($_.avgCtMs)ms" }
    Write-Expect "C-PR1~PR4 모두 CT가 나옴 (약 16000~17000ms). 예전엔 PR4만 나왔음"
}

Write-Title "⑧ 알람 (/alarms/active)"
$al = Get-Api "/alarms/active"
if ($null -ne $al) {
    "  진행 중 알람 $(@($al).Count)건"
    Write-Expect "지금은 0건이 정상 (알람 저장이 아직 구현 안 됨 → 다음 할 일)"
}

Write-Title "⑨ 코일 (M-16 /coil/today)"
$co = Get-Api "/coil/today"
if ($co) {
    "  오늘 입고 $($co.receivedRolls)롤 ($($co.receivedQty)개) · 재고 $($co.stockRolls)롤 ($($co.stockQty)개) · 슬리팅 투입 $($co.inputQty) · 입고 기록 $(@($co.receipts).Count)건"
    Write-Expect "입고 4롤(80개) · 재고 0.7롤(14개) · 투입 66 · 입고 기록 4건 (같은 시각)"
}

Write-Title "⑩ 생산 탭 실적 (G-01 /production/summary)"
$ps = Get-Api "/production/summary?from=$d&to=$d"
if ($ps) {
    "  " + (($ps | ForEach-Object { "$($_.lineId) $($_.actualQty)" }) -join " · ") + "  (합계 $(($ps | Measure-Object actualQty -Sum).Sum))"
    Write-Expect "A 5 · B 4 · C 2 · D 5 · E 3 · F 4 (합계 23 = 대시보드 출고 양품과 같음)"
}
Write-Host ""
