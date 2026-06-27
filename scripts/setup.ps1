$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$hookScript = Join-Path $scriptDir "feishu_codex_hook.py"
$env:PYTHONIOENCODING = "utf-8"

$candidates = @(
    @{ Name = "py"; PrefixArgs = @("-3") },
    @{ Name = "python"; PrefixArgs = @() },
    @{ Name = "python3"; PrefixArgs = @() }
)

foreach ($candidate in $candidates) {
    $command = Get-Command $candidate.Name -ErrorAction SilentlyContinue
    if (-not $command) {
        continue
    }

    $versionCheckArgs = @($candidate.PrefixArgs) + @(
        "-c",
        "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    )
    & $candidate.Name @versionCheckArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        continue
    }

    $versionArgs = @($candidate.PrefixArgs) + @(
        "-c",
        "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    )
    $version = (& $candidate.Name @versionArgs).Trim()
    Write-Host "Python: OK $version ($($command.Source))"

    Push-Location $repoRoot
    try {
        $setupArgs = @($candidate.PrefixArgs) + @($hookScript, "setup") + $args
        & $candidate.Name @setupArgs
        exit $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

Write-Host "未找到可用的 Python 3.10+。请先安装 Python，然后重新运行本向导。"
Write-Host "Windows 推荐安装后确认: py -3 --version"
exit 1
