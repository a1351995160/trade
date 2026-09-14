param([switch]$Once,[switch]$InputRecovery39,[ValidateRange(2,3600)][int]$IntervalSeconds=10,[ValidateSet(21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43)][int]$Batch=21)
$evidenceRoot="E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/train-search-batch-v$Batch"
if($InputRecovery39){if($Batch -ne 39){throw "InputRecovery39 only supports Batch 39"};$evidenceRoot+="/input-recovery-v1"}
function Read-Receipt($path){
    if(Test-Path -LiteralPath $path){
        try{return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json}
        catch{Write-Host "回执暂不可读：$path" -ForegroundColor Yellow}
    }
    return $null
}
do{
    if(-not $Once){Clear-Host}
    Write-Host "A股第$Batch 批研究进度（只读） $(Get-Date -Format s)" -ForegroundColor Cyan
    $plan=Read-Receipt "$evidenceRoot/PREREGISTRATION.json"
    $completed=Read-Receipt "$evidenceRoot/RUNNER_COMPLETED.json"
    $total=@($plan.contracts.PSObject.Properties).Count
    $finished=0
    foreach($property in $plan.contracts.PSObject.Properties){
        $name=$property.Name
        $feedback=Read-Receipt "$evidenceRoot/$name/FEEDBACK.json"
        $ready=Read-Receipt "$evidenceRoot/$name/READY.json"
        $rejected=Read-Receipt "$evidenceRoot/$name/REJECTED.json"
        $status='等待输入准备'
        if($rejected){$status='新颖性拒绝';$finished++}
        elseif($feedback){$status="账户完成；训练筛选=$($feedback.screen_passed)";$finished++}
        elseif($ready -and -not $ready.feasibility_passed){$status='可行性未过，无账户结果';$finished++}
        elseif($ready){$status='可行性通过，等待账户结算'}
        Write-Host "$name : $status"
        $sourceAccess="$evidenceRoot/$name/source-access"
        if(Test-Path -LiteralPath $sourceAccess){
            $sourceCount=@(Get-ChildItem -LiteralPath $sourceAccess -Filter '*.json' -File -ErrorAction SilentlyContinue).Count
            Write-Host "  已写证券来源记录：$sourceCount（不是账户完成比例）"
        }
    }
    Write-Host "候选处理完成：[$('#'*$finished)$('-'*($total-$finished))] $finished/$total（非阶段内耗时估计）"
    $used=0
    if($InputRecovery39){$prior=Read-Receipt "$evidenceRoot/RECOVERY.json";if($prior){$used+=$prior.historical_worker_seconds;Write-Host "Includes original failed preparation time; original failure records retained."}}
    foreach($file in @(Get-ChildItem -LiteralPath "$evidenceRoot/resources" -Filter '*.started.json' -ErrorAction SilentlyContinue)){
        $start=Read-Receipt $file.FullName
        $end=Read-Receipt ($file.FullName -replace '\.started\.json$','.completed.json')
        if($end){
            $used+=$end.elapsed_seconds
            if($end.returncode -ne 0){Write-Host "失败：$($file.BaseName)；code=$($end.returncode)，timeout=$($end.timed_out)" -ForegroundColor Red;Write-Host $end.stderr}
        }else{
            $process=Get-Process -Id $start.pid -ErrorAction SilentlyContinue
            Write-Host "未结算：$($file.BaseName) PID=$($start.pid)；进程存在=$([bool]$process)；阶段内进度未知"
        }
    }
    Write-Host "已结算计算：$([math]::Round($used/60,1))/90分钟；未含当前worker"
    if($completed){Write-Host "批次最终状态：$($completed.status)"}
    Write-Host "证据：$evidenceRoot"
    Write-Host 'Ctrl+C仅停止查看；本脚本不能执行、重试或追加额度。'
    if($Once -or $completed){break}
    Start-Sleep -Seconds $IntervalSeconds
}while($true)
