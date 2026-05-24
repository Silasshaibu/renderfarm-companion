# How to Ship a New Version of Renderfarm Companion

## Prerequisites
- Node.js installed
- A GitHub Personal Access Token with `repo` scope
  - Generate one at: https://github.com/settings/tokens/new

---

## Steps

### 1. Make your code changes

### 2. Bump the version in `package.json`
```json
"version": "1.0.2"
```
> Increment the last number for patches/bug fixes (1.0.2)  
> Increment the middle number for new features (1.1.0)  
> Increment the first number for major rewrites (2.0.0)

### 3. Commit and tag
```powershell
cd "C:\Users\Administrator\Downloads\OptimumDevelopment2026Final\New folder\htdocs\renderfarm-companion"

git add .
git commit -m "Release v1.0.2"
git tag v1.0.2
git push origin main --tags
```

### 4. Build and publish to GitHub Releases
```powershell
$env:GH_TOKEN = "ghp_YOUR_TOKEN_HERE"
npm run dist
```

> ⚠️ Never save your GH_TOKEN in any file. Only type it directly in the terminal.

---

## What happens after `npm run dist`

| File | What it does |
|------|-------------|
| `Renderfarm-Setup-x.x.x.exe` | The Windows installer uploaded to GitHub Releases |
| `latest.yml` | Manifest the in-app updater reads to detect new versions |
| `*.blockmap` | Enables delta (partial) updates |

Users with an older version installed will see **"Download x.x.x"** appear  
in the sidebar the next time they click **Check for updates**.

---

## GitHub Releases page
https://github.com/Silasshaibu/renderfarm-companion/releases

## Download link for latest installer
https://github.com/Silasshaibu/renderfarm-companion/releases/latest
