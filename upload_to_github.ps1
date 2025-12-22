<#
=============================================================
VikingAI GitHub Auto-Uploader
=============================================================
#>

Write-Host "`n=============================================================" -ForegroundColor Cyan
Write-Host "🚀 VikingAI GitHub Auto-Uploader" -ForegroundColor Green
Write-Host "=============================================================`n" -ForegroundColor Cyan

# --- Set working directory ---
$RepoPath = "C:\VikingAI"
if (-not (Test-Path $RepoPath)) {
    Write-Host "❌ Project folder not found at $RepoPath" -ForegroundColor Red
    pause
    exit 1
}
Set-Location $RepoPath
Write-Host "📁 Working in: $RepoPath" -ForegroundColor Yellow

# --- Check for Git installation ---
Write-Host "`n🔍 Checking for Git installation..." -ForegroundColor Cyan
$gitVersion = git --version 2>$null
if (-not $gitVersion) {
    Write-Host "❌ Git not found! Please install it from https://git-scm.com/download/win" -ForegroundColor Red
    pause
    exit 1
}
Write-Host "✅ Git detected: $gitVersion" -ForegroundColor Green

# --- Initialize Git repo if not already ---
if (-not (Test-Path ".git")) {
    Write-Host "`n🧱 Initializing new Git repository..." -ForegroundColor Yellow
    git init
    git branch -M main
} else {
    Write-Host "🧠 Git repository already initialized." -ForegroundColor Green
}

# --- Configure user info if missing ---
$userName = git config user.name
$userEmail = git config user.email

if (-not $userName -or -not $userEmail) {
    Write-Host "`n⚙️ Git user identity not set." -ForegroundColor Yellow
    $Name = Read-Host "Enter your GitHub username or name"
    $Email = Read-Host "Enter your GitHub email"
    git config --global user.name "$Name"
    git config --global user.email "$Email"
    Write-Host "✅ Git identity configured: $Name <$Email>"
}

# --- Check for remote origin ---
$remoteUrl = git remote get-url origin 2>$null
if (-not $remoteUrl) {
    Write-Host "`n🌐 No remote repository detected." -ForegroundColor Yellow
    $RepoURL = Read-Host "Enter your GitHub repository URL (example: https://github.com/USERNAME/VikingAI.git)"
    git remote add origin $RepoURL
    Write-Host "✅ Remote set to: $RepoURL"
} else {
    Write-Host "🌍 Remote already set: $remoteUrl" -ForegroundColor Green
}

# --- Stage and commit all files ---
Write-Host "`n📦 Staging all changes..." -ForegroundColor Cyan
git add -A

$commitMessage = Read-Host "📝 Enter a short commit message"
if ([string]::IsNullOrWhiteSpace($commitMessage)) {
    $commitMessage = "Auto-update from VikingAI"
}
git commit -m "$commitMessage"

# --- Push to GitHub ---
Write-Host "`n🚀 Pushing changes to GitHub..." -ForegroundColor Green
git push -u origin main

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n✅ Push successful! VikingAI is now updated on GitHub." -ForegroundColor Green
} else {
    Write-Host "`n⚠️ Push failed. Please check GitHub credentials or permissions." -ForegroundColor Red
}

pause
