param([switch]$Once)
$ErrorActionPreference='Stop'
$evidenceBase='E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1'
$sequenceRoot="$evidenceBase/train-search-sequence-v2"
do {
    Write-Host "检查时间：$(Get-Date -Format o)"
    if (-not (Test-Path "$sequenceRoot/PLAN.json")) { Write-Host '连续队列尚未启动。'; break }
    $plan=Get-Content "$sequenceRoot/PLAN.json" -Encoding utf8 -Raw | ConvertFrom-Json
    foreach ($batch in 12..20) {
        if ($batch -ge 16) {
            $preregPath="$evidenceBase/train-search-batch-v$batch/PREREGISTRATION.json"
            if (-not (Test-Path $preregPath)) { continue }
            $prereg=Get-Content $preregPath -Encoding utf8 -Raw | ConvertFrom-Json
            $names=$prereg.contracts.psobject.Properties.Name
        } else { $names=$plan.batches.psobject.Properties["$batch"].Value }
        foreach ($name in $names) {
            $candidateRoot="$evidenceBase/train-search-batch-v$batch/$name"
            if (Test-Path "$candidateRoot/FEEDBACK.json") {
                $feedback=Get-Content "$candidateRoot/FEEDBACK.json" -Encoding utf8 -Raw | ConvertFrom-Json
                Write-Host "$name : $($feedback.status)，训练筛选=$($feedback.screen_passed)"
            } elseif (Test-Path "$candidateRoot/READY.json") {
                $ready=Get-Content "$candidateRoot/READY.json" -Encoding utf8 -Raw | ConvertFrom-Json
                Write-Host "$name : 可行性=$($ready.feasibility_passed)，账户结果尚未保存"
            } elseif (Test-Path "$candidateRoot/source-access") {
                $count=@(Get-ChildItem "$candidateRoot/source-access" -Filter '*.json' -File).Count
                Write-Host "$name : 准备中，已处理${count}只证券"
            } else { Write-Host "$name : 尚未进入特征准备（可能尚未开始或被新颖性拒绝）" }
        }
    }
    if (Test-Path "$sequenceRoot/COMPLETED.json") {
        $done=Get-Content "$sequenceRoot/COMPLETED.json" -Encoding utf8 -Raw | ConvertFrom-Json
        Write-Host "原8项队列已停止：$($done.status)"
    } elseif (Test-Path "$sequenceRoot/STARTED.json") {
        $started=Get-Content "$sequenceRoot/STARTED.json" -Encoding utf8 -Raw | ConvertFrom-Json
        $alive=Get-Process -Id $started.pid -ErrorAction SilentlyContinue
        Write-Host "队列进程存活=$([bool]$alive)"
    }
    foreach ($researchBatch in 16..20) {
    $compositeRoot="$evidenceBase/train-search-batch-v$researchBatch"
    if (Test-Path "$compositeRoot/RUNNER_COMPLETED.json") {
        $done=Get-Content "$compositeRoot/RUNNER_COMPLETED.json" -Encoding utf8 -Raw | ConvertFrom-Json
        Write-Host "第${researchBatch}批组合研究已结束：$($done.status)"
    } elseif (Test-Path "$compositeRoot/RUNNER_STARTED.json") {
        $started=Get-Content "$compositeRoot/RUNNER_STARTED.json" -Encoding utf8 -Raw | ConvertFrom-Json
        $alive=Get-Process -Id $started.pid -ErrorAction SilentlyContinue
        Write-Host "第${researchBatch}批组合研究进程存活=$([bool]$alive)"
    }
    }
    Write-Host '只读状态，不重跑、不读取精确收益。Ctrl+C仅退出查看。'
    if (-not $Once) { Start-Sleep -Seconds 60 }
} while (-not $Once)
