param(
    [string]$TempRoot,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectPath '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create the project .venv first.' }
$runId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
$logRoot = Join-Path $projectPath '.local/logs/tests'
if (-not $TempRoot) { $TempRoot = Join-Path $projectPath '.local/t' }
$baseTemp = Join-Path ([IO.Path]::GetFullPath($TempRoot)) $runId.Substring($runId.Length - 6)
if (Test-Path -LiteralPath $baseTemp) { throw 'Test temporary directory already exists.' }
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($baseTemp)) -Force | Out-Null
$logPath = Join-Path $logRoot ($runId + '.log')
Push-Location $projectPath
try {
    & $pythonPath -X utf8 -m pytest --basetemp $baseTemp @PytestArgs 2>&1 | Tee-Object -FilePath $logPath
    $testExit = $LASTEXITCODE
    Write-Host "Test log: $logPath"
    Write-Host "Temporary files: $baseTemp"
} finally {
    Pop-Location
}
exit $testExit
