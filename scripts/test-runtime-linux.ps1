param(
    [string]$Image = 'alexgshaw/build-cython-ext:20251031',
    [switch]$AllowDependencyNetwork
)

$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dockerArgs = @('run', '--rm', '--pull', 'never', '--workdir', '/work', '-e', 'UV_LINK_MODE=copy')
if (-not $AllowDependencyNetwork) { $dockerArgs += @('--network', 'none') }

# Mount package/test paths only: never expose .env, host sessions, or task outputs.
$testPaths = @(
    'forge', 'tests', 'evals', 'extensions', 'examples/mcp_server.py', 'pyproject.toml', 'README.md',
    'benchmark/harbor', 'benchmark/__init__.py', 'benchmark/__main__.py',
    'benchmark/cli.py', 'benchmark/catalog.py'
)
foreach ($relativePath in $testPaths) {
    $sourcePath = (Resolve-Path (Join-Path $projectPath $relativePath)).Path
    $dockerArgs += @('--mount', "type=bind,source=$sourcePath,target=/work/$relativePath,readonly")
}
$cachePath = (Resolve-Path (Join-Path $projectPath 'benchmark/.cache/harbor')).Path
$dockerArgs += @('--mount', "type=bind,source=$cachePath,target=/cache")
$offline = if ($AllowDependencyNetwork) { '' } else { '--offline ' }
$command = "export UV_PYTHON_INSTALL_DIR=/cache/python; /cache/bin/uv python install 3.12.11 && /cache/bin/uv venv --python 3.12.11 /opt/runtime-test && /cache/bin/uv pip install --python /opt/runtime-test/bin/python ${offline}--cache-dir /cache --require-hashes -r /work/benchmark/harbor/runtime-requirements.txt && /cache/bin/uv pip install --python /opt/runtime-test/bin/python ${offline}--cache-dir /cache -c /work/benchmark/harbor/runtime-requirements.txt /work pytest==9.1.1 harbor==0.18.0 && export PATH=/opt/runtime-test/bin:`$PATH && python --version && python -m pytest -q --tb=short -p no:cacheprovider"
$dockerArgs += @('--entrypoint', 'sh', $Image, '-c', $command)
& docker @dockerArgs
exit $LASTEXITCODE
