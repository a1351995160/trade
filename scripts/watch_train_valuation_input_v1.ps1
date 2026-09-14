param([switch]$Once,[ValidateRange(10,3600)][int]$IntervalSeconds=60)
$ErrorActionPreference='Stop'
$root='E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/train-valuation-input-v1'
$receiptPath='E:/llmwiki/autonomous-strategy-research-v1/revised-exploration-v1/governance/train_valuation_input_v1/confirmation.json'
do {
    $receipt=Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    $count=0
    foreach($symbol in $receipt.plan.symbols) {
        if(Test-Path -LiteralPath "$root/responses/$symbol/quality.json") {$count++}
    }
    $used=0.0;$errors=0;$running=0
    foreach($item in Get-ChildItem -LiteralPath "$root/resources" -Filter '*.completed.json') {
        $r=Get-Content -LiteralPath $item.FullName -Raw | ConvertFrom-Json
        $used+=$r.elapsed_seconds
        if($r.returncode -ne 0) {$errors++;Write-Output "失败回执：$($item.FullName)"}
    }
    foreach($item in Get-ChildItem -LiteralPath "$root/resources" -Filter '*.started.json') {
        $end=$item.FullName.Replace('.started.json','.completed.json')
        if(-not (Test-Path -LiteralPath $end)) {$running++}
    }
    Write-Output ("{0} 原TRAIN估值字段：{1}/{2}只已存schema检查；{3:N1}/{4}分钟已结算，未结算worker={5}，失败={6}" -f (Get-Date -Format o),$count,$receipt.plan.symbols.Count,($used/60),($receipt.plan.wall_seconds/60),$running,$errors)
    $recoveryReceiptPath=Join-Path (Split-Path $receiptPath) 'recovery-v1.json'
    $recoveryExists=Test-Path -LiteralPath $recoveryReceiptPath
    $recoveryErrors=0
    if($recoveryExists) {
        $recovery=Get-Content -LiteralPath $recoveryReceiptPath -Raw -Encoding utf8 | ConvertFrom-Json
        $recoveryUsed=0.0;$recoveryRunning=0
        if(Test-Path -LiteralPath "$root/recovery-v1/resources") {
            foreach($item in Get-ChildItem -LiteralPath "$root/recovery-v1/resources" -Filter '*.completed.json') {
                $r=Get-Content -LiteralPath $item.FullName -Raw -Encoding utf8 | ConvertFrom-Json
                $recoveryUsed+=$r.elapsed_seconds
                if($r.returncode -ne 0) {$recoveryErrors++;Write-Output "续取失败回执：$($item.FullName)"}
            }
            foreach($item in Get-ChildItem -LiteralPath "$root/recovery-v1/resources" -Filter '*.started.json') {
                if(-not (Test-Path -LiteralPath $item.FullName.Replace('.started.json','.completed.json'))) {$recoveryRunning++}
            }
        }
        Write-Output ("独立续取修订：{0:N1}/{1}分钟已结算，未结算worker={2}，新失败={3}；原失败与原90分钟消耗保留。" -f ($recoveryUsed/60),($recovery.additional_data_seconds/60),$recoveryRunning,$recoveryErrors)
        if($recoveryRunning -eq 0) {Write-Output '没有未结算续取worker；这不代表取数或物化已完成。'}
    }
    $complete=Test-Path -LiteralPath "$root/FETCH_COMPLETED.json"
    Write-Output "取数完成回执=$complete；schema检查不等于完整覆盖或PIT证明。本脚本只读，不启动账户回测。"
    if($Once -or $complete -or $recoveryErrors -gt 0 -or ($errors -gt 0 -and -not $recoveryExists)) {break}
    Start-Sleep -Seconds $IntervalSeconds
} while($true)
