# Proxlane integration for Claude SEO: uninstaller for Windows. Removes only what install.ps1 created.
$ErrorActionPreference = "Stop"

# USERPROFILE rather than $HOME, so this and the script agree on where home is: Python's
# Path.home() reads USERPROFILE on Windows, and PowerShell's $HOME is fixed at startup from the
# profile and cannot be redirected. They are the same directory on any normal machine.
$UserHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$SkillDir = Join-Path $UserHome ".claude/skills/seo-proxlane"
$ConfigFile = Join-Path $UserHome ".config/claude-seo/proxlane.json"
$UsageFile = Join-Path $UserHome ".config/claude-seo/proxlane-usage.json"

if (Test-Path $SkillDir) {
    Remove-Item $SkillDir -Recurse -Force
    Write-Host "v Removed $SkillDir"
} else {
    Write-Host "  $SkillDir not present"
}

if (Test-Path $ConfigFile) {
    Remove-Item $ConfigFile -Force
    Write-Host "v Removed $ConfigFile"
} else {
    Write-Host "  $ConfigFile not present"
}

if (Test-Path $UsageFile) {
    Remove-Item $UsageFile -Force
    Write-Host "v Removed $UsageFile"
}

Write-Host ""
Write-Host "v Uninstalled. Claude SEO itself is unchanged, and so is your gateway."
