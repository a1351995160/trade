param([switch]$Once)
$evidenceRoot='E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/monthly-independent-window-v1'
do {
    Write-Host "检查时间：$(Get-Date -Format o)"
    $poolCount=@(Get-ChildItem -LiteralPath "$evidenceRoot/acquisition/pool" -Filter '*.access.json' -ErrorAction SilentlyContinue).Count
    Write-Host "历史股票池响应：$poolCount/263个日期"
    if (Test-Path -LiteralPath "$evidenceRoot/WINDOW_UNIVERSE.json") {
        $universe=Get-Content -LiteralPath "$evidenceRoot/WINDOW_UNIVERSE.json" -Raw | ConvertFrom-Json
        $prices=@(Get-ChildItem -LiteralPath "$evidenceRoot/acquisition/prices" -Filter '*.access.json' -Recurse -ErrorAction SilentlyContinue).Count + 2
        $actions=@(Get-ChildItem -LiteralPath "$evidenceRoot/acquisition/actions" -Filter '*.access.json' -ErrorAction SilentlyContinue).Count
        Write-Host "价格响应：$prices/$($universe.count*2)份；调整日期响应：$actions/$($universe.count)份"
    } else { Write-Host '股票池尚未冻结，行情批次尚未开始。' }
    $receipts=@(Get-ChildItem -LiteralPath "$evidenceRoot/resources" -Filter '*.completed.json' | ForEach-Object {
        $record=Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json
        [pscustomobject]@{name=$_.Name; seconds=$record.elapsed_seconds; code=$record.returncode}
    })
    $seconds=($receipts | Measure-Object -Property seconds -Sum).Sum
    Write-Host "已结算worker累计：$([math]::Round($seconds/60,1))分钟（不含正在运行的worker）"
    $failed=@($receipts | Where-Object {$_.code -ne 0})
    Write-Host "保留失败回执数：$($failed.Count)；metadata-0旧失败已由分批修订承接，未删除。"
    if (Test-Path -LiteralPath "$evidenceRoot/READY.json") {
        $ready=Get-Content -LiteralPath "$evidenceRoot/READY.json" -Raw | ConvertFrom-Json
        Write-Host "输入状态：$($ready.status)；可行性通过：$($ready.feasibility_passed)"
    }
    if (Test-Path -LiteralPath "$evidenceRoot/ACCOUNT_SETTLEMENT.json") {
        $settlement=Get-Content -LiteralPath "$evidenceRoot/ACCOUNT_SETTLEMENT.json" -Raw | ConvertFrom-Json
        Write-Host "账户完成：$($settlement.completed)；主曝光：$($settlement.MAIN_BACKTEST_EXPOSURES_USED)；修复曝光：$($settlement.REPAIR_BACKTEST_EXPOSURES_USED)"
    } else { Write-Host '账户尚未结算，不代表已经完成或成功。' }
    $pipelineReceipt="$evidenceRoot/PIPELINE_COMPLETED.json"
    if (Test-Path -LiteralPath "$evidenceRoot/PIPELINE_STARTED_V2.json") { $pipelineReceipt="$evidenceRoot/PIPELINE_COMPLETED_V2.json" }
    if (Test-Path -LiteralPath $pipelineReceipt) {
        $pipeline=Get-Content -LiteralPath $pipelineReceipt -Raw | ConvertFrom-Json
        Write-Host "流程已退出：$($pipeline.exit_code)；具体限制查看pipeline.stderr.log与原回执。"
    }
    Write-Host '此脚本只读状态，不读取精确绩效，不重启或重试。Ctrl+C仅退出查看。'
    if (-not $Once) { Start-Sleep -Seconds 60 }
} while (-not $Once)
