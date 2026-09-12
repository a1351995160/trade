param([Parameter(Mandatory=$true)][int]$AcquisitionProcessId)
$ErrorActionPreference = 'Stop'
$researchRoot = 'E:/llmwiki/bounded-offline-strategy-research-v1'
$evidenceRoot = 'E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/monthly-independent-window-v1'
Set-Location -LiteralPath $researchRoot
# 等待本次已启动的顺序取数结束；不重启、不重试任何失败worker。
# 不吞掉Wait-Process错误后直接执行；只在实际进程清单已不存在该PID时继续。
while (@(Get-Process -ErrorAction Stop | Where-Object { $_.Id -eq $AcquisitionProcessId }).Count -gt 0) {
    Start-Sleep -Seconds 10
}
$env:PYTHONPATH = "$researchRoot/src"
$env:PYTHONDONTWRITEBYTECODE = '1'
& "$researchRoot/.venv/Scripts/python.exe" "$researchRoot/scripts/execute_monthly_window_v1.py"
$runExitCode = $LASTEXITCODE
$receipt = @{finished_at=(Get-Date -Format o); exit_code=$runExitCode; acquisition_process_id=$AcquisitionProcessId}
$receiptPath = "$evidenceRoot/PIPELINE_COMPLETED_V3.json"
if (Test-Path -LiteralPath $receiptPath) { throw 'PIPELINE_ALREADY_SETTLED_NO_OVERWRITE' }
$receipt | ConvertTo-Json | Set-Content -LiteralPath $receiptPath -Encoding utf8
exit $runExitCode
