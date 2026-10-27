$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$pythonPath = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $pythonPath -m ruff check src scripts tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonPath -m ruff format --check src scripts tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$testPath = Join-Path 'work' ('pytest-' + [Guid]::NewGuid().ToString('N'))
& $pythonPath -X utf8 -m pytest --basetemp $testPath
exit $LASTEXITCODE
