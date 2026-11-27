# Measure Windows process overhead before adding a parser or MCP runtime.
$ErrorActionPreference = 'Stop'
$source = Join-Path $env:RUNNER_TEMP 'stdio-floor.c'
$exe = Join-Path $env:RUNNER_TEMP 'stdio-floor.exe'
@'
#include <windows.h>
void start(void) {
    DWORD transferred;
    char byte;
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), "ready\n", 6, &transferred, NULL);
    ReadFile(GetStdHandle(STD_INPUT_HANDLE), &byte, 1, &transferred, NULL);
    ExitProcess(0);
}
'@ | Set-Content -Encoding ascii $source
& clang --target=x86_64-pc-windows-msvc -Os -fno-stack-protector -nostdlib $source -o $exe -Xlinker /entry:start -Xlinker /subsystem:console -lkernel32
if ($LASTEXITCODE) { throw 'Failed to compile Windows baseline' }
$info = [Diagnostics.ProcessStartInfo]::new($exe)
$info.UseShellExecute = $false
$info.RedirectStandardInput = $true
$info.RedirectStandardOutput = $true
$info.CreateNoWindow = $true
$p = [Diagnostics.Process]::Start($info)
try {
    if ($p.StandardOutput.ReadLine() -ne 'ready') { throw 'Probe initialization failed' }
    $p.Refresh()
    Write-Output "Baseline bytes: working set $($p.WorkingSet64), peak $($p.PeakWorkingSet64), private committed $($p.PrivateMemorySize64)"
} finally {
    $p.StandardInput.Close()
    $p.WaitForExit()
    $p.Dispose()
}
