[CmdletBinding()]
param(
    [string]$Vsix,
    [string]$ModelPath
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$package = Get-Content -LiteralPath (Join-Path $root 'package.json') -Raw | ConvertFrom-Json
$model = Get-Content -LiteralPath (Join-Path $root 'config\model.json') -Raw | ConvertFrom-Json
$tests = Join-Path $root 'dist\integration'
if (-not (Test-Path -LiteralPath (Join-Path $tests 'index.js') -PathType Leaf)) {
    throw 'Build the extension first with npm run compile.'
}
if (-not $Vsix) {
    $target = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'win32-arm64' } else { 'win32-x64' }
    $Vsix = Join-Path $root "artifacts\$($package.name)-$($package.version)-$target.vsix"
}
if (-not $ModelPath) { $ModelPath = Join-Path $root "models\$($model.filename)" }
$Vsix = (Resolve-Path -LiteralPath $Vsix).Path
$ModelPath = (Resolve-Path -LiteralPath $ModelPath).Path
if ((Get-Item -LiteralPath $ModelPath).Length -ne $model.sizeBytes -or
    (Get-FileHash -LiteralPath $ModelPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $model.sha256) {
    throw 'The local model does not match config/model.json. No model will be downloaded.'
}

# Read the installed identity from the actual package being tested.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($Vsix)
try {
    $entry = $archive.GetEntry('extension/package.json')
    if (-not $entry) { throw 'The VSIX has no extension/package.json.' }
    $reader = [System.IO.StreamReader]::new($entry.Open())
    try { $installedPackage = $reader.ReadToEnd() | ConvertFrom-Json }
    finally { $reader.Dispose() }
} finally { $archive.Dispose() }

$code = Get-Command code -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$cli = if ($code) { $code.Source } else { $null }
$applications = @()
if ($cli) {
    $cliDirectory = Split-Path -Parent $cli
    $applications += Join-Path $cliDirectory 'Code.exe'
    $applications += Join-Path (Split-Path -Parent $cliDirectory) 'Code.exe'
}
if ($env:LOCALAPPDATA) { $applications += Join-Path $env:LOCALAPPDATA 'Programs\Microsoft VS Code\Code.exe' }
if ($env:ProgramFiles) { $applications += Join-Path $env:ProgramFiles 'Microsoft VS Code\Code.exe' }
$application = $applications | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $application) { throw 'Install Visual Studio Code, or add its code command to PATH.' }
$application = (Resolve-Path -LiteralPath $application).Path
if (-not $cli) { $cli = Join-Path (Split-Path -Parent $application) 'bin\code.cmd' }
if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) { throw "VS Code CLI missing: $cli" }

$run = Join-Path $root ('artifacts\editor-test-' + [guid]::NewGuid().ToString('N'))
$userData = Join-Path $run 'user-data'
$extensions = Join-Path $run 'extensions'
$workspace = Join-Path $run 'workspace'
$temporary = Join-Path $run 'temp'
$reportPath = Join-Path $run 'report.json'
New-Item -ItemType Directory -Path $userData, $extensions, $workspace, $temporary -Force | Out-Null
Write-Host "Editor test artifacts: $run"

$previous = @{}
foreach ($name in @('BTL_TEST_RESULT', 'BTL_TEST_EXTENSION_PATH', 'BTL_TEST_MODEL', 'TEMP', 'TMP', 'ELECTRON_RUN_AS_NODE')) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    & $cli --user-data-dir $userData --extensions-dir $extensions --install-extension $Vsix --force
    if ($LASTEXITCODE -ne 0) { throw "VSIX installation failed with exit code $LASTEXITCODE." }
    $identity = "$($installedPackage.publisher).$($installedPackage.name)-$($installedPackage.version)"
    $installed = @(Get-ChildItem -LiteralPath $extensions -Directory | Where-Object {
        $_.Name -eq $identity -or $_.Name.StartsWith($identity + '-')
    })
    if ($installed.Count -ne 1) { throw "Expected one installed extension matching $identity." }
    $env:BTL_TEST_RESULT = $reportPath
    $env:BTL_TEST_EXTENSION_PATH = $installed[0].FullName
    $env:BTL_TEST_MODEL = $ModelPath
    $env:TEMP = $temporary
    $env:TMP = $temporary
    $env:ELECTRON_RUN_AS_NODE = $null

    # Start-Process joins its arguments; quote paths explicitly for Windows.
    $arguments = @(
        '--user-data-dir', ('"' + $userData + '"'),
        '--extensions-dir', ('"' + $extensions + '"'),
        '--extensionDevelopmentPath', ('"' + $installed[0].FullName + '"'),
        '--extensionTestsPath', ('"' + $tests + '"'),
        '--skip-welcome', '--skip-release-notes', '--disable-workspace-trust',
        '--new-window', ('"' + $workspace + '"')
    )
    $process = Start-Process -FilePath $application -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(300)
    $report = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $reportPath -PathType Leaf) {
            try { $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json }
            catch { $report = $null } # The test host may still be finishing its write.
            if ($report -and $report.status -in @('passed', 'failed')) { break }
        }
        if ($process.HasExited -and $process.ExitCode -ne 0) {
            throw "VS Code exited with code $($process.ExitCode). Artifacts: $run"
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $report -or $report.status -notin @('passed', 'failed')) {
        throw "Editor tests did not write a final report within 300 seconds. Artifacts: $run"
    }
    if ($report.status -ne 'passed') { throw "Editor tests failed: $($report.error)`nReport: $reportPath" }
    Write-Host "Editor tests passed ($($report.tests.Count) checks). Report: $reportPath"
} finally {
    foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
