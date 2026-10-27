# Pilot of own_data/PROTOCOL.md 1.1: 5 tasks x 3 repeats, strictly sequential, resumable (finished runs are skipped).
# After every run: SWE-bench evaluation, processing (adapter, features, risk), key scan, LOG.md line.
# Commit + push after every 5 finished runs; hard stop at a cumulative pilot spend of $5.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File own_data\infra\pilot.ps1
Set-Location C:\Users\1\Desktop\diss
$env:Path = "C:\Users\1\.local\mingit\cmd;$env:Path"
$env:ZAI_API_KEY = [Environment]::GetEnvironmentVariable("ZAI_API_KEY", "User")
if (-not $env:ZAI_API_KEY) { Write-Output "ZAI_API_KEY is not set"; exit 2 }
$env:OWN_PROTOCOL = "1.1"
$env:WSLENV = "ZAI_API_KEY/u:OWN_PROTOCOL/u"
Remove-Item Env:OWN_CFG, Env:OWN_DEST_ROOT -ErrorAction SilentlyContinue
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$py = ".\.venv\Scripts\python.exe"
$infra = "/mnt/c/Users/1/Desktop/diss/own_data/infra"
$tasks = (Get-Content own_data\tasks_pilot.json -Raw | ConvertFrom-Json).tasks | ForEach-Object { $_.task_id }

function KeyInFiles {
    $k = $env:ZAI_API_KEY
    foreach ($f in Get-ChildItem own_data -Recurse -File) {
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

& $py own_data\infra\pilot_lib.py header
$stop = $null
:outer foreach ($k in 0, 1, 2) {
    foreach ($t in $tasks) {
        $rid = "own-v1.1_mini-v1.17.1_glm-4.6_pilot_r${k}_$t"
        if (Test-Path "own_data\raw\$rid") { continue }
        $spent = [double](& $py own_data\infra\pilot_lib.py spent)
        if ($spent -ge 5.0) { $stop = "budget: pilot spend $spent >= 5"; break outer }
        Write-Output ("{0} start {1} (spent {2:N4})" -f (Get-Date -Format s), $rid, $spent)
        & wsl.exe -d own-ubuntu -- bash "$infra/run_agent.sh" $rid $t 2>&1 | Select-Object -Last 2 | Out-String | Write-Output
        $code = $LASTEXITCODE
        if ($code -eq 3) { $stop = "API key found in the output of $rid (output deleted)"; break outer }
        if (-not (Test-Path "own_data\raw\$rid")) { & $py own_data\infra\pilot_lib.py fail $rid $code }
        else {
            & wsl.exe -d own-ubuntu -- bash "$infra/eval_run.sh" $rid 2>&1 | Select-Object -Last 1 | Out-String | Write-Output
            & $py own_data\infra\process_runs.py | Out-Null
        }
        if (KeyInFiles) { $stop = "API key found in own_data after $rid"; break outer }
        $res = & $py own_data\infra\pilot_lib.py log $rid
        Write-Output ("{0} done {1}: {2}" -f (Get-Date -Format s), $rid, $res)
        if ($res -like "STOP*") { $stop = $res; break outer }
        $n = (Get-ChildItem own_data\raw -Directory -Filter "*_pilot_*").Count
        if ($n % 5 -eq 0) { CommitPush "own_data: pilot runs 1-$n (protocol 1.1)" }
    }
}
if ($stop) { Write-Output "PILOT STOPPED: $stop"; & $py -c "import sys; open('own_data/LOG.md','a',encoding='utf-8').write('- PILOT STOPPED: ' + sys.argv[1] + '\n')" $stop }
& $py own_data\infra\stage2_report.py
CommitPush "own_data: pilot finished, STAGE2_REPORT.md"
