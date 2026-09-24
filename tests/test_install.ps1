# The Windows installer and uninstaller, run for real against a throwaway HOME.
#
# There is no gateway on a Windows runner (service containers are Linux only), so this pins
# the parts that are Windows-specific: the files, the config, the ACL, and that an unreachable
# gateway still installs and says so. The gateway round trip is covered on Linux.
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Fail($msg) { Write-Host "FAIL: $msg"; exit 1 }
function Pass($msg) { Write-Host "ok   $msg" }

$TestHome = Join-Path ([System.IO.Path]::GetTempPath()) ("px-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path (Join-Path $TestHome ".claude/skills/seo") -Force | Out-Null
$RealProfile = $env:USERPROFILE
try {
    # Both installers resolve home from USERPROFILE, which is also what Python's Path.home()
    # reads on Windows, so pointing it at a throwaway directory redirects everything.
    $env:USERPROFILE = $TestHome
    $env:PROXLANE_URL = "http://127.0.0.1:1"
    $env:PROXLANE_API_KEY = "a-gateway-key-for-windows-tests"

    $out = & "$Repo\install.ps1" 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { Fail "install exited $LASTEXITCODE`n$out" }

    $SkillDir = Join-Path $TestHome ".claude/skills/seo-proxlane"
    foreach ($f in "SKILL.md", "proxlane_fetch.py", "LICENSE") {
        if (-not (Test-Path (Join-Path $SkillDir $f))) { Fail "missing $f`n$out" }
    }
    Pass "installs the skill files"

    $Config = Join-Path $TestHome ".config/claude-seo/proxlane.json"
    if (-not (Test-Path $Config)) { Fail "no config written`n$out" }
    $doc = Get-Content $Config -Raw | ConvertFrom-Json
    if ($doc.url -ne "http://127.0.0.1:1" -or $doc.api_key -ne "a-gateway-key-for-windows-tests") { Fail "config contents: $($doc | ConvertTo-Json)" }
    Pass "records the url and key"

    $acl = (Get-Acl $Config).Access | ForEach-Object { $_.IdentityReference.Value }
    $others = $acl | Where-Object { $_ -notmatch [regex]::Escape($env:USERNAME) }
    if ($others) {
        Write-Host "--- installer output ---"
        Write-Host $out
        Write-Host "--- icacls ---"
        & icacls $Config
        Write-Host "--- whoami /user ---"
        & whoami /user /fo csv /nh
        Fail "config is readable by others: $($acl -join ', ')"
    }
    Pass "restricts the config to the current user"

    if ($out -notmatch "check above failed") { Fail "an unreachable gateway was not reported`n$out" }
    Pass "installs before the gateway exists, and says the check failed"

    # The key never reaches a command line. A `python.cmd` shim first on PATH logs every
    # argument list the installer passes, then runs the real interpreter.
    $Shim = Join-Path ([System.IO.Path]::GetTempPath()) ("px-shim-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $Shim | Out-Null
    $RealPy = (Get-Command python).Source
    $Log = Join-Path $Shim "argv.log"
    Set-Content -Path (Join-Path $Shim "python.cmd") -Encoding ascii -Value @(
        "@echo off",
        "echo %*>>`"$Log`"",
        "`"$RealPy`" %*",
        "exit /b %ERRORLEVEL%"
    )
    $Secret = "argv-canary-" + [guid]::NewGuid()
    $OldPath = $env:PATH
    try {
        $env:PATH = "$Shim;$OldPath"
        $env:PROXLANE_API_KEY = $Secret
        & "$Repo\install.ps1" *> $null
    } finally {
        $env:PATH = $OldPath
        $env:PROXLANE_API_KEY = "a-gateway-key-for-windows-tests"
    }
    if (-not (Test-Path $Log)) { Fail "the shim saw no python calls, so this test proved nothing" }
    $logged = Get-Content $Log -Raw
    if ($logged -notmatch "configure") { Fail "the shim did not see the configure call" }
    if ($logged -match [regex]::Escape($Secret)) { Fail "the key reached a python command line" }
    Remove-Item $Shim -Recurse -Force
    Pass "the key never reaches a python command line"

    & "$Repo\uninstall.ps1" | Out-Null
    if (Test-Path $SkillDir) { Fail "skill dir left behind" }
    if (Test-Path $Config) { Fail "config left behind" }
    if (-not (Test-Path (Join-Path $TestHome ".claude/skills/seo"))) { Fail "uninstall touched Claude SEO itself" }
    Pass "uninstalls cleanly"
} finally {
    $env:USERPROFILE = $RealProfile
    Remove-Item Env:PROXLANE_URL, Env:PROXLANE_API_KEY -ErrorAction SilentlyContinue
    Remove-Item $TestHome -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "all Windows installer tests passed"
