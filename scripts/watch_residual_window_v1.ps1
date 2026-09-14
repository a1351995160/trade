param([switch]$Once,[ValidateRange(2,3600)][int]$IntervalSeconds=10,[ValidateSet('Legacy','WEEKLY_LOW_VOL_STOCK_TREND_ONLY_HOLD_20','WEEKLY_LOW_VOL_TREND60_FIXED_HOLD_20','WEEKLY_SMALL_SCALE_TREND_HOLD_20','LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20','RESIDUAL_SELF_REVERSION_CONFIRM_HOLD_20','RESIDUAL_MARKET_LAG_CONFIRM_HOLD_20')][string]$Candidate='Legacy')
$evidenceRoot='E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/residual-fixed-window-v1'
if($Candidate -ne 'Legacy'){$evidenceRoot="E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/response-confirmation-window-v1/$Candidate"}
if($Candidate -eq "LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20"){$evidenceRoot="E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/turnover-fixed-window-v1"}
if($Candidate -eq "WEEKLY_SMALL_SCALE_TREND_HOLD_20"){$evidenceRoot="E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/scale-fixed-window-v1"}
if($Candidate -eq "WEEKLY_LOW_VOL_TREND60_FIXED_HOLD_20"){$evidenceRoot="E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/trend-risk-fixed-window-v1"}
if($Candidate -eq "WEEKLY_LOW_VOL_STOCK_TREND_ONLY_HOLD_20"){$evidenceRoot="E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/stock-trend-only-fixed-window-v1"}
function Read-Receipt($path){
    if(Test-Path -LiteralPath $path){
        try{return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json}
        catch{Write-Host "回执暂不可读：$path" -ForegroundColor Yellow}
    }
    return $null
}
do{
    if(-not $Once){Clear-Host}
    Write-Host "固定候选跨期核验（只读） $(Get-Date -Format s)" -ForegroundColor Cyan
    Write-Host '窗口：2025-08-01至2026-07-31；已有历史曝光，不是完全盲测。'
    foreach($prefix in @('base','factor')){
        $last=Get-ChildItem -LiteralPath "$evidenceRoot/progress" -Filter "$prefix-*.json" -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
        if($last){
            $value=Read-Receipt $last.FullName
            if($value -and $value.total -gt 0){
                $percent=[math]::Min(100,100*$value.done/$value.total)
                $ticks=[int][math]::Floor($percent/5)
                Write-Host "$($value.stage)：[$('#'*$ticks)$('-'*(20-$ticks))] $($value.done)/$($value.total) ($([math]::Round($percent,1))%)"
            }
        }
    }
    $ready=Read-Receipt "$evidenceRoot/READY.json"
    if($ready){Write-Host "输入阶段已结束；可行性通过=$($ready.feasibility_passed)"}
    $used=0
    foreach($stage in @('prepare','account-main','report')){
        $start=Read-Receipt "$evidenceRoot/resources/$stage.started.json"
        $end=Read-Receipt "$evidenceRoot/resources/$stage.completed.json"
        if($end){
            $used+=$end.elapsed_seconds
            Write-Host "$stage 已结算：code=$($end.returncode)，timeout=$($end.timed_out)"
            if($end.returncode -ne 0){Write-Host $end.stderr -ForegroundColor Red}
        }elseif($start){
            $process=Get-Process -Id $start.pid -ErrorAction SilentlyContinue
            Write-Host "$stage 未结算；协调进程存在=$([bool]$process)；不据此推定成功或剩余时间。"
        }else{Write-Host "$stage 尚未开始"}
    }
    $final=Read-Receipt "$evidenceRoot/FINAL_STATUS.json"
    Write-Host "已结算计算：$([math]::Round($used/60,1))/90分钟（不含未结算worker）"
    if($final){
        Write-Host "账户开始=$($final.account_started)；账户完成=$($final.account_completed)；报告完成=$($final.report_completed)"
        if($final.error){Write-Host "$($final.error_type)：$($final.error)" -ForegroundColor Red}
        if($final.reason){Write-Host $final.reason -ForegroundColor Yellow}
    }
    Write-Host "证据：$evidenceRoot"
    Write-Host '进度按已写检查点显示；账户阶段不伪造百分比。Ctrl+C仅停止查看，无执行或重试功能。'
    if($Once -or $final){break}
    Start-Sleep -Seconds $IntervalSeconds
}while($true)
