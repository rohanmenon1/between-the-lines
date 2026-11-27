# Measure the server process only. Each timed request includes stdio and JSON.
$ErrorActionPreference = 'Stop'
$fixture = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
$null = New-Item -Type Directory -Path (Join-Path $fixture '.git') -Force
$functions = foreach ($i in 1..1000) { "def helper_$i(): return $i" }
$calls = (1..32 | ForEach-Object { "helper_$_()" }) -join ', '
Set-Content (Join-Path $fixture 'sample.py') ($functions + "def caller(): return ($calls)") -Encoding utf8
$records = @{}
foreach ($i in 1..1000) { $records["helper_$i"] = @{ intent = 'Fixture intent'; ast_hash = 'obsolete' } }
@{ 'sample.py' = $records } | ConvertTo-Json -Depth 4 -Compress | Set-Content (Join-Path $fixture 'intent.json') -Encoding utf8NoBOM
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = (Get-Command python -CommandType Application | Select-Object -First 1).Source
$start.WorkingDirectory = $fixture
$start.UseShellExecute = $false
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
foreach ($arg in @('-I','-B', (Join-Path $PSScriptRoot 'plugin/scripts/intent_server.py'))) { $start.ArgumentList.Add($arg) }
$server = [Diagnostics.Process]::Start($start)
function Request($method, $parameters) {
    $message = @{jsonrpc='2.0'; id=1; method=$method; params=$parameters}
    $server.StandardInput.WriteLine(($message | ConvertTo-Json -Compress -Depth 6))
    $reply = $server.StandardOutput.ReadLine() | ConvertFrom-Json
    if (!$reply.result -or $reply.result.isError) { throw "MCP request failed for $method" }
    return $reply.result
}
try {
    $null = Request 'initialize' @{protocolVersion='2025-06-18'}
    $null = Request 'tools/list' @{}
    $server.Refresh()
    $idle = $server.WorkingSet64 / 1MB
    $samples = foreach ($i in 1..31) {
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $reply = Request 'tools/call' @{name='lookup_intent'; arguments=@{path='sample.py';symbol='caller'}}
        $elapsed = $timer.Elapsed.TotalMilliseconds
        $result = $reply.content[0].text | ConvertFrom-Json
        if ($result.calls.Count -ne 32 -or @($result.calls | Where-Object status -ne stale).Count) { throw 'Unexpected fixture result' }
        $elapsed
    }
    $server.Refresh()
    $sorted = @($samples[1..30] | Sort-Object)
    $report = 'Server working set: idle {0:N1} MiB, peak {1:N1} MiB. Lookup: first {2:N1} ms, warm median {3:N1} ms, warm p95 {4:N1} ms.' -f $idle, ($server.PeakWorkingSet64 / 1MB), $samples[0], (($sorted[14]+$sorted[15])/2), $sorted[28]
    Write-Output $report
    if ($env:GITHUB_STEP_SUMMARY) { Add-Content $env:GITHUB_STEP_SUMMARY $report }
} finally {
    $server.StandardInput.Close()
    if (!$server.WaitForExit(3000)) { $server.Kill() }
    $server.Dispose()
}
