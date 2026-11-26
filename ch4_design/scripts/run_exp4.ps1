# 실험 4 한 큐: 생성부터 수집, 단위 자르기까지. 설계 docs/design_exp4.md
#
#   powershell -ExecutionPolicy Bypass -File scripts\run_exp4.ps1            1~6 전부
#   powershell -ExecutionPolicy Bypass -File scripts\run_exp4.ps1 -From 5    끊긴 단계부터
#
# 단계마다 이어하기가 됨. 생성은 빈 번호만, 수집은 없는 파일만 다시 함. 실패하면 그 단계에서 멈춤.
# 채점과 평가는 여기 없음. 조작 확인, 위약 대조, 기제 기술 코드를 수집 동안 만들고 따로 돌림(결과를 미리 안 봄)

param([int]$From = 1, [int]$To = 6)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = "1"

function Run([string[]]$cmd) {
    Write-Host ("   > " + ($cmd -join " ")) -ForegroundColor DarkGray
    & $cmd[0] $cmd[1..($cmd.Length - 1)]
    if ($LASTEXITCODE -ne 0) { throw ("실패(종료코드 {0}): {1}" -f $LASTEXITCODE, ($cmd -join " ")) }
}

function Step([int]$n, [string]$title, [scriptblock]$body) {
    if ($n -lt $From -or $n -gt $To) { return }
    Write-Host ""
    Write-Host ("== [{0}/6] {1}  ({2})" -f $n, $title, (Get-Date -Format "MM-dd HH:mm")) -ForegroundColor Cyan
    & $body
}

$PY = @("py", "-3.11", "-X", "utf8")
$PY37 = @("py", "-3.7", "-X", "utf8")
# ptree가 pknob보다 앞(pknob이 같은 시드 ptree 계획을 읽음)
$ARMS = "plain,knob,ptree,pknob"
$SRC = "data/outputs/arms_exp4"
$UNITS = "data/outputs/units_exp4"
$PILOT = "data/outputs/pilot_exp4"

Step 1 "코드 시험" {
    Run ($PY + @("-m", "pytest", "-q", "tests"))
}

Step 2 "생성: 성향 넷 x 트리 5 = 20 (OpenRouter, 카탈로그 v3)" {
    Run ($PY + @("scripts/exp4_author.py", "generate", "--all", "--n", "5"))
}

Step 3 "생성 점검: 배치 수, 컴파일러 대조" {
    Run ($PY + @("scripts/exp4_author.py", "check"))
}

Step 4 "파일럿: Town03, 시드 100, 30초, 네 조건" {
    Run ($PY37 + @("scripts/collect.py", "sweep", "--arms", $ARMS, "--towns", "Town03", "--seeds", "1",
                   "--seed-from", "100", "--seconds", "30", "--warmup", "10", "--tick-log", "--out", $PILOT))
    Run ($PY + @("scripts/exp4_check.py", "--src", $PILOT))
    Run ($PY + @("scripts/exp3_eval.py", "logs", "--src", $PILOT, "--out", "$PILOT/eval",
                 "--arm", "ptree", "--sets", "exp4_*"))
    Write-Host "   파일럿 확인: $PILOT/eval/e2.md 재현 확인이 1에 가깝고, 위 짝 점검 문제 0건이어야 함" -ForegroundColor Yellow
}

Step 5 "수집: 4 조건 x 8 맵 x 시드 24~31 = 256회 (약 12시간, 실험 3 실측으로 추정)" {
    $sweep = $PY37 + @("scripts/collect.py", "sweep", "--arms", $ARMS, "--seeds", "8", "--seed-from", "24",
                       "--tick-log", "--out", $SRC)
    Run ($sweep + @("--dry-run"))
    # 한 판이 CARLA 부팅 문제로 빠지면 sweep이 1을 냄. 빠진 것만 한 번 더 돌림(이미 있는 판은 건너뜀)
    & $sweep[0] $sweep[1..($sweep.Length - 1)]
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   빠진 판 다시 돌림" -ForegroundColor Yellow
        Run $sweep
    }
}

Step 6 "짝 점검, 단위 자르기" {
    Run ($PY + @("scripts/exp4_check.py", "--src", $SRC))
    Run ($PY + @("scripts/measure.py", "units", "--src", $SRC, "--dst", $UNITS))
}

Write-Host ""
Write-Host "끝. 수집 $SRC, 단위 $UNITS. 채점, 평가는 따로" -ForegroundColor Green
