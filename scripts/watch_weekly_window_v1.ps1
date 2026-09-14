param([switch]$Once,[ValidateRange(2,3600)][int]$IntervalSeconds=5,[switch]$Json)
$projectRoot=Split-Path -Parent $PSScriptRoot
$python=Join-Path $projectRoot '.venv/Scripts/python.exe'
$reader=Join-Path $PSScriptRoot 'weekly_progress_v1.py'
function Progress-Bar($done,$total) {
    if ($null -eq $done -or $null -eq $total -or $total -le 0) { return '[        无准确计数        ]' }
    $ratio=[math]::Min(1,[math]::Max(0,$done/$total))
    $n=[int][math]::Floor(24*$ratio)
    return '['+('#'*$n)+('-'*(24-$n))+'] '+('{0:P1}' -f $ratio)+" ($done/$total)"
}
do {
    $payload=& $python $reader
    if ($LASTEXITCODE -ne 0) { Write-Host '读取失败；未对执行任务做任何操作。' -ForegroundColor Red; break }
    if ($Json) { $payload; break }
    try { $s=$payload | ConvertFrom-Json } catch { Write-Host '进度响应正在变化，稍后再读。'; if($Once){break}; Start-Sleep -Seconds $IntervalSeconds; continue }
    if(-not $Once){Clear-Host}
    Write-Host '固定周低波动策略 · 执行进度（只读）' -ForegroundColor Cyan
    Write-Host "更新时间：$($s.time)"
    Write-Host "当前状态：$($s.status)" -ForegroundColor $(if($s.current_error){'Red'}elseif($s.complete){'Green'}else{'Yellow'})
    Write-Host ''
    foreach($stage in $s.stages){
        $bar=Progress-Bar $stage.done $stage.total
        if($stage.status -in @('完成','通过','已结算')){$bar='[########################] 完成'}
        Write-Host ("{0,-14} {1}  {2}" -f $stage.name,$bar,$stage.status)
    }
    if($s.current_worker){
        $w=$s.current_worker
        Write-Host "`n当前/最近worker：$($w.name)；PID=$($w.pid)；耗时=$([math]::Round($w.elapsed_seconds/60,1))分钟；已结算=$($w.finished)"
        if(-not $w.finished){Write-Host "进程存活：$($w.alive)；此阶段没有准确完成计数，不把耗时当完成百分比。"}
    }
    if($s.feasibility){
        $c=$s.feasibility.counts;$t=$s.feasibility.thresholds
        Write-Host "可行性：完整路径$($c.closed_paths)/门槛$($t.closed_paths)，日期$($c.entry_dates)/门槛$($t.entry_dates)，证券$($c.symbols)/门槛$($t.symbols)"
    }
    if($s.streamed_rows){Write-Host "物化记录：行情$($s.streamed_rows.'DAILY.parquet')；状态$($s.streamed_rows.'STATES.parquet')；可计算性$($s.streamed_rows.'COMPUTABILITY.parquet')"}
    Write-Host "已结算资源：数据$([math]::Round($s.data_settled_seconds/60,1))/360分钟；账户/报告$([math]::Round($s.account_settled_seconds/60,1))/90分钟（未含当前worker）。"
    if($s.current_error){Write-Host "`n当前错误：$($s.current_error.error)" -ForegroundColor Red;Write-Host "原始回执：$($s.current_error.receipt)"}
    Write-Host "`n历史失败：$(@($s.historical_failures).Count)份（不等于当前故障）"
    foreach($failure in @($s.historical_failures | Select-Object -Last 4)){Write-Host "  $($failure.name)：$($failure.error)"}
    foreach($warning in $s.warnings){Write-Host "核验提示：$warning" -ForegroundColor Yellow}
    Write-Host "`n证据目录：$($s.root)"
    Write-Host 'Ctrl+C仅退出查看；不会停止回测、重试或增加额度。未知进度不伪造百分比。'
    if($Once -or $s.complete){break}
    Start-Sleep -Seconds $IntervalSeconds
} while($true)
