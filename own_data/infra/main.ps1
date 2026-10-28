# Main collection of own_data/PROTOCOL.md 1.2 (sections 11.6-11.8): rounds 0, 1, 2 over the pool, then rounds 3, 4
# over the first 30 tasks with a mixed outcome; strictly sequential; resumable (runs with a label are skipped,
# unfinished ones are moved to raw_failed/<run_id>-attempt<N>/ and repeated). After every run: SWE-bench evaluation,
# processing (adapter, features, risk), key scan, LOG.md line, spend check (stop at $35 without the pilot).
# Commit + push after every 10 finished runs. At the end (all rounds done or budget stop): gold_post validation.
# Requires the stage V result own_data/gold_validation.json. Images are pulled before, never during the runs.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File own_data\infra\main.ps1
# Pause after the current run: create the file .own_main_stop in the repository root (main_lib.py log); no Cyrillic in
# this file: Windows PowerShell 5.1 reads it as cp1251, LOG.md lines are written by main_lib.py.
Set-Location C:\Users\1\Desktop\diss
$env:Path = "C:\Users\1\.local\mingit\cmd;$env:Path"
$env:ZAI_API_KEY = [Environment]::GetEnvironmentVariable("ZAI_API_KEY", "User")
if (-not $env:ZAI_API_KEY) { Write-Output "ZAI_API_KEY is not set"; exit 2 }
if (-not (Test-Path own_data\gold_validation.json)) { Write-Output "no own_data\gold_validation.json (stage V first)"; exit 2 }
$env:OWN_PROTOCOL = "1.2"
$env:WSLENV = "ZAI_API_KEY/u:OWN_PROTOCOL/u"
Remove-Item Env:OWN_CFG, Env:OWN_DEST_ROOT, Env:OWN_HTTPBIN_PROBE -ErrorAction SilentlyContinue
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$py = ".\.venv\Scripts\python.exe"
$infra = "/mnt/c/Users/1/Desktop/diss/own_data/infra"
# keep the machine awake while this process runs (ES_CONTINUOUS | ES_SYSTEM_REQUIRED); released when it exits
Add-Type -Namespace Own -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[void][Own.Power]::SetThreadExecutionState([uint32]2147483649)

function KeyInFiles([string]$rid) {
    $k = $env:ZAI_API_KEY
    $files = @(Get-ChildItem "own_data\raw\$rid", "own_data\eval\$rid" -Recurse -File -ErrorAction SilentlyContinue) +
             @(Get-Item own_data\LOG.md, own_data\process_status.json, own_data\scores_own.csv)
    foreach ($f in $files) {
        if ((Get-Content $f.FullName -Raw -ErrorAction SilentlyContinue) -like "*$k*") { return $true }
    }
    return $false
}

function CommitPush([string]$msg) {
    git add own_data | Out-Null
    $hits = git grep --cached -F -l -e $env:ZAI_API_KEY -- own_data
    if ($hits) { Write-Output "KEY IN STAGED FILES - not committing"; return $false }
    git commit -q -m $msg | Out-Null
    git push -q 2>&1 | Out-Null
    Write-Output ("committed: " + (git log --oneline -1))
    return $true
}

function Archive([string]$rid) {
    $n = & $py own_data\infra\main_lib.py attempt $rid
    & wsl.exe -d own-ubuntu -u root -- bash "$infra/archive_failed.sh" $rid $n | Out-Null
    $dest = "own_data\raw_failed\$rid-attempt$n"
    New-Item -ItemType Directory -Force $dest | Out-Null
    if (Test-Path "own_data\raw\$rid") { Move-Item "own_data\raw\$rid" "$dest\raw" }
    if (Test-Path "own_data\eval\$rid") { Move-Item "own_data\eval\$rid" "$dest\eval" }
    Write-Output ("{0} archived {1} -> {2}" -f (Get-Date -Format s), $rid, $dest)
    & $py own_data\infra\main_lib.py archived $rid $dest
}

function Evaluate([string]$rid) {
    foreach ($try in 1, 2) {
        & wsl.exe -d own-ubuntu -- bash "$infra/eval_run.sh" $rid 2>&1 | Select-Object -Last 1 | Out-String | Write-Output
        if ((& $py own_data\infra\main_lib.py state $rid) -eq "done") { return }
    }
    & $py own_data\infra\main_lib.py evalfail $rid
}

& $py own_data\infra\main_lib.py header
$stop = $null; $techfail = 0; $skipGold = $false
while ($true) {
    $next = & $py own_data\infra\main_lib.py next
    if ($next -eq "DONE") { break }
    $rid, $task, $round = $next -split " "
    $spent = [double](& $py own_data\infra\main_lib.py spent)
    if ($spent -ge 35.0) { $stop = "budget: spend $spent >= 35"; break }
    $st = & $py own_data\infra\main_lib.py state $rid
    & wsl.exe -d own-ubuntu -u root -- test -e "/opt/own/runs/$rid"
    $wslLeft = ($LASTEXITCODE -eq 0) -and ($st -eq "new")
    if ($st -eq "incomplete" -or $wslLeft) { Archive $rid; $st = "new" }
    if ($st -eq "new") {
        Write-Output ("{0} start {1} (round {2}, spent {3:N4})" -f (Get-Date -Format s), $rid, $round, $spent)
        & wsl.exe -d own-ubuntu -- bash "$infra/run_agent.sh" $rid $task 2>&1 | Select-Object -Last 2 | Out-String | Write-Output
        $code = $LASTEXITCODE
        if ($code -eq 3) { $stop = "API key found in the output of $rid (output deleted)"; $skipGold = $true; break }
        $st = & $py own_data\infra\main_lib.py state $rid
        if ($st -eq "new" -or $st -eq "incomplete") {
            $techfail += 1
            Write-Output ("{0} technical failure {1} (exit {2}, {3} in a row)" -f (Get-Date -Format s), $rid, $code, $techfail)
            Archive $rid
            if ($techfail -ge 3) { $stop = "3 technical failures in a row (last: $rid, exit $code)"; break }
            continue
        }
    }
    $techfail = 0
    if ($st -eq "needs_eval") { Evaluate $rid }
    & $py own_data\infra\process_runs.py --only $rid | Out-Null
    if (KeyInFiles $rid) { $stop = "API key found in own_data after $rid"; $skipGold = $true; break }
    $res = & $py own_data\infra\main_lib.py log $rid $round
    Write-Output ("{0} done {1}: {2}" -f (Get-Date -Format s), $rid, $res)
    if ($res -like "STOP*") { $stop = $res; if ($res -notlike "*budget*") { $skipGold = $true }; break }
    $n = [int](& $py own_data\infra\main_lib.py summary)
    if ($rid -like "*_main_*" -and $n % 10 -eq 0) { CommitPush "own_data: main collection, $n runs (protocol 1.2)" }
}
if ($stop) {
    Write-Output "MAIN COLLECTION STOPPED: $stop"
    & $py own_data\infra\main_lib.py stopped $stop
} else {
    Write-Output "MAIN COLLECTION DONE"
    & $py own_data\infra\main_lib.py finished
}
CommitPush "own_data: main collection finished or stopped (protocol 1.2)"
if (-not $skipGold) {
    Write-Output ("{0} gold_post start" -f (Get-Date -Format s))
    & wsl.exe -d own-ubuntu -- bash "$infra/gold_eval.sh" gold_post 2>&1 | Select-Object -Last 1 | Out-String | Write-Output
    CommitPush "own_data: gold_post validation (protocol 1.2)"
}
[void][Own.Power]::SetThreadExecutionState([uint32]2147483648)
