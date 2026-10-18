param(
  [string]$Repository = "Arhan-w/MosaicRoute",
  [string]$Visibility = "public",
  [string]$Version = "v0.6.0"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git is required. Install it from https://git-scm.com/downloads" }
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "GitHub CLI is required. Install it from https://cli.github.com/ then run: gh auth login" }
& gh auth status
if ($LASTEXITCODE -ne 0) { throw "GitHub CLI is not authenticated. Run: gh auth login" }
if ($Visibility -notin @("public", "private")) { throw "Visibility must be 'public' or 'private'." }
if ($Version -notmatch '^v\d+\.\d+\.\d+$') { throw "Version must look like v0.1.0." }

if (-not (Test-Path ".git")) {
  & git init -b main
  if ($LASTEXITCODE -ne 0) { throw "git init failed." }
}
& git add -A
if ($LASTEXITCODE -ne 0) { throw "git add failed." }
& git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
  & git commit -m "Release $Version: MosaicRoute implementation"
  if ($LASTEXITCODE -ne 0) { throw "git commit failed. Configure git user.name and user.email if needed." }
}

& gh repo view $Repository *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Creating GitHub repository $Repository ($Visibility)…" -ForegroundColor Cyan
  & gh repo create $Repository "--$Visibility" --source . --remote origin --push
  if ($LASTEXITCODE -ne 0) { throw "Repository creation/push failed. Confirm you have permission to create repositories." }
} else {
  $remote = (& git remote get-url origin 2>$null)
  if (-not $remote) {
    & git remote add origin "https://github.com/$Repository.git"
    if ($LASTEXITCODE -ne 0) { throw "Could not add origin remote." }
  }
  & git push -u origin main
  if ($LASTEXITCODE -ne 0) { throw "Push failed. Check repository URL and branch history." }
}

& git tag -a $Version -m "MosaicRoute $Version" 2>$null
if ($LASTEXITCODE -ne 0) {
  $existing = (& git tag --list $Version)
  if (-not $existing) { throw "Could not create release tag $Version." }
}
& git push origin $Version
if ($LASTEXITCODE -ne 0) { throw "Tag push failed." }
Write-Host "Pushed $Version. GitHub Actions will run tests, package MosaicRoute-source.zip, and publish the release assets." -ForegroundColor Green
Write-Host "Repository: https://github.com/$Repository"
Write-Host "Releases:   https://github.com/$Repository/releases"
