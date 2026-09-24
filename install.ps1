# Proxlane integration for Claude SEO: installer for Windows.
#
# Mirrors install.sh. Set PROXLANE_URL and PROXLANE_API_KEY in the environment to skip the
# prompts; the key still never reaches a command line.
$ErrorActionPreference = "Stop"

# USERPROFILE rather than $HOME, so this and the script agree on where home is: Python's
# Path.home() reads USERPROFILE on Windows, and PowerShell's $HOME is fixed at startup from the
# profile and cannot be redirected. They are the same directory on any normal machine.
$UserHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$SkillDir = Join-Path $UserHome ".claude/skills/seo-proxlane"
$SeoSkillDir = Join-Path $UserHome ".claude/skills/seo"
$ConfigFile = Join-Path $UserHome ".config/claude-seo/proxlane.json"

Write-Host "Proxlane integration for Claude SEO"
Write-Host ""

if (Test-Path $SeoSkillDir) {
    Write-Host "v Claude SEO detected"
} else {
    Write-Host "! Claude SEO not found at $SeoSkillDir."
    Write-Host "  The skill still works standalone. Install Claude SEO for the audit integration:"
    Write-Host "  https://github.com/AgriciDaniel/claude-seo"
}

# `python` is what the Windows installer registers; `python3` is usually the Store stub.
$Python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $Python) { throw "Python 3.9+ is required and python was not found." }
& python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
# A native command's non-zero exit does not throw, even with Stop.
if ($LASTEXITCODE -ne 0) { throw "Python 3.9+ is required (found $(& python -V 2>&1))." }
Write-Host "v $(& python -V 2>&1) detected"

$SourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path (Join-Path $SourceDir "skills/seo-proxlane/SKILL.md"))) {
    throw "Cannot find the skill files next to this script. Run it from a clone of proxlane/claude-seo-proxlane."
}

$GatewayUrl = $env:PROXLANE_URL
if (-not $GatewayUrl) {
    if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
        $GatewayUrl = Read-Host "Gateway URL [http://localhost:8787]"
    }
    if (-not $GatewayUrl) { $GatewayUrl = "http://localhost:8787" }
}

$GatewayKey = $env:PROXLANE_API_KEY
if (-not $GatewayKey) {
    if ([Console]::IsInputRedirected) { throw "No terminal to prompt on, and PROXLANE_API_KEY is not set." }
    Write-Host ""
    Write-Host "The gateway's key: the PROXLANE_API_KEY your gateway was started with."
    Write-Host "Not a ScraperAPI or Firecrawl key. Those stay on the gateway."
    $Secure = Read-Host "Gateway key" -AsSecureString
    $GatewayKey = [System.Net.NetworkCredential]::new("", $Secure).Password
}
if (-not $GatewayKey) { throw "The key cannot be empty." }

Write-Host ""
Write-Host "-> Installing the skill to $SkillDir"
New-Item -ItemType Directory -Path $SkillDir -Force | Out-Null
Copy-Item (Join-Path $SourceDir "skills/seo-proxlane/SKILL.md") (Join-Path $SkillDir "SKILL.md") -Force
Copy-Item (Join-Path $SourceDir "skills/seo-proxlane/proxlane_fetch.py") (Join-Path $SkillDir "proxlane_fetch.py") -Force
Copy-Item (Join-Path $SourceDir "LICENSE") (Join-Path $SkillDir "LICENSE") -Force

Write-Host "-> Writing $ConfigFile"
# Through the environment, never argv. write_config() restricts the file to you with icacls.
$env:PROXLANE_CONFIGURE_KEY = $GatewayKey
try {
    & python (Join-Path $SkillDir "proxlane_fetch.py") configure --url $GatewayUrl
    if ($LASTEXITCODE -ne 0) { throw "Nothing was saved (python exited $LASTEXITCODE). See the message above." }
} finally {
    Remove-Item Env:PROXLANE_CONFIGURE_KEY -ErrorAction SilentlyContinue
}

Write-Host ""
& python (Join-Path $SkillDir "proxlane_fetch.py") check
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "v Installed."
} else {
    # Not fatal: installing before the gateway is started is a reasonable order of work.
    Write-Host ""
    Write-Host "! Installed, but the check above failed. Fix what it says, then run:"
    Write-Host "  python `"$SkillDir\proxlane_fetch.py`" check"
}
Write-Host ""
Write-Host "In Claude Code:  /seo proxlane fetch https://example.com"
Write-Host "Uninstall:       .\uninstall.ps1"
exit 0
