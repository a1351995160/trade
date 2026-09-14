param([switch]$Once,[ValidateRange(10,3600)][int]$IntervalSeconds=60)
$ErrorActionPreference='Stop'
$root='E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/turnover-fixed-window-v1/turnover-input-v1'
$receiptPath='E:/llmwiki/autonomous-strategy-research-v1/revised-exploration-v1/governance/turnover_window_input_v1/confirmation.json'
do {
    $receipt=Get-Content -LiteralPath $receiptPath -Raw -Encoding utf8 | ConvertFrom-Json
    $count=0
    foreach($symbol in $receipt.symbols) {if(Test-Path -LiteralPath "$root/responses/$symbol/quality.json") {$count++}}
    $used=0.0;$errors=0;$running=0
    foreach($item in Get-ChildItem -LiteralPath "$root/resources" -Filter '*.completed.json') {
        $r=Get-Content -LiteralPath $item.FullName -Raw -Encoding utf8 | ConvertFrom-Json
        $used+=$r.elapsed_seconds
        if($r.returncode -ne 0) {$errors++;Write-Output "失败回执：$($item.FullName)"}
    }
    foreach($item in Get-ChildItem -LiteralPath "$root/resources" -Filter '*.started.json') {
        if(-not(Test-Path -LiteralPath $item.FullName.Replace('.started.json','.completed.json'))) {$running++}
    }
    $fetched=Test-Path -LiteralPath "$root/FETCH_COMPLETED.json"
    $ready=Test-Path -LiteralPath "$root/READY.json"
    Write-Output ("{0} 固定跨期换手字段：{1}/{2}只schema检查；{3:N1}/{4}分钟已结算，未结算worker={5}，失败={6}" -f (Get-Date -Format o),$count,$receipt.symbols.Count,($used/60),($receipt.wall_seconds/60),$running,$errors)
    Write-Output "取数完成=$fetched；物化完成=$ready。schema不等于完整覆盖或PIT证明；本脚本只读，不启动账户回测。"
    if($Once -or $ready -or $errors -gt 0) {break}
    Start-Sleep -Seconds $IntervalSeconds
} while($true)
