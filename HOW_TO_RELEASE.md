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

---

## Shipping a DCC addon update (Blender, Maya, Houdini, Cinema 4D, 3ds Max)

This is a **separate, manual process** from the Electron app release above — it does
NOT go through `.github/workflows/release.yml` or `npm run dist`.

⚠️ **Do not create a new GitHub Release for a new addon version.** The Plugins page's
download URLs all point at `.../releases/latest/download/<asset-name>`, and GitHub's
"latest" is whichever release is most recently published *repo-wide* — not scoped per
addon. Publishing a fresh release moves "latest" onto it, breaking the download links
for every other addon still pointing at the old one. Instead, **attach the new zip as
an additional asset on the existing latest release** (currently `v2.1.3`).

### Steps
1. Bump the version string in the addon's own docstring/comment header
   (e.g. `dcc-addons/maya/renderfarm_submitter.py` line 2).
2. Zip just the single `.py` file at the archive root (matches how every existing
   addon zip is structured — verify with `unzip -l` on an existing one if unsure):
   ```powershell
   Compress-Archive -Path "renderfarm_submitter.py" -DestinationPath "renderfarm_submitter_<dcc>_v<version>.zip" -Force
   ```
3. Attach it to the current latest release (check with `gh release view` first):
   ```powershell
   gh release upload v2.1.3 "renderfarm_submitter_<dcc>_v<version>.zip"
   ```
4. Update the matching entry in
   `src/renderer/src/pages/Plugins.tsx` (`version`, `downloadUrl`, `available: true`).
5. Rebuild the Electron app (`npm run build`) so the Plugins page picks up the change.

If the "latest" release ever legitimately moves on (e.g. a real Electron app release),
re-check every addon's `downloadUrl` in `Plugins.tsx` still resolves — re-upload the
zips to the new latest release if not.
