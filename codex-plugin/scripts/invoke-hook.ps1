param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop')]
    [string]$Event
)

$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$pluginDirectory = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $pluginDirectory 'runtime\between-the-lines-plugin\between-the-lines-plugin.exe'
if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) {
    [Console]::Error.WriteLine('between-the-lines runtime is missing. Install the complete Windows package.')
    exit 0
}

# Hook JSON arrives on the native stdin handle. Forward it as UTF-8 bytes rather
# than PowerShell objects, which can alter a prompt or corrupt non-ASCII text.
$request = [Console]::In.ReadToEnd()
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = $runtimePath
$start.Arguments = '--hook ' + $Event
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$start.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
$start.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
$process = New-Object System.Diagnostics.Process
$process.StartInfo = $start
try {
    # Installation resolves one data directory for both MCP and hook children.
    # Read that value instead of relying on which environment the host forwards.
    $configPath = Join-Path $pluginDirectory '.mcp.json'
    $prepared = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    $dataDirectory = [string]$prepared.mcpServers.'between-the-lines'.env.BETWEEN_THE_LINES_PLUGIN_DATA
    if ([string]::IsNullOrWhiteSpace($dataDirectory) -or -not [System.IO.Path]::IsPathRooted($dataDirectory)) {
        [Console]::Error.WriteLine('between-the-lines needs installation preparation. Run Install.cmd again.')
        return
    }
    $start.EnvironmentVariables['BETWEEN_THE_LINES_PLUGIN_DATA'] = $dataDirectory
    [void]$process.Start()
    $output = $process.StandardOutput.ReadToEndAsync()
    $diagnostic = $process.StandardError.ReadToEndAsync()
    $inputBytes = [System.Text.Encoding]::UTF8.GetBytes($request)
    $inputStream = $process.StandardInput.BaseStream
    $inputStream.Write($inputBytes, 0, $inputBytes.Length)
    $inputStream.Flush()
    $inputStream.Close()
    if (-not $process.WaitForExit(8500)) {
        $process.Kill()
        [Console]::Error.WriteLine('between-the-lines hook timed out; the coding turn can continue.')
        return
    }
    [Console]::Out.Write($output.GetAwaiter().GetResult())
    [Console]::Error.Write($diagnostic.GetAwaiter().GetResult())
} catch {
    [Console]::Error.WriteLine('between-the-lines could not process this hook event.')
} finally {
    $process.Dispose()
}
