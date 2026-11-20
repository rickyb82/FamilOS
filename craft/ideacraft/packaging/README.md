# Packaging

Release builds for Windows and macOS. `.github/workflows/release.yml` runs these scripts on a
tag push (`v*`), on a manual run (`workflow_dispatch`, optional version override) and on every
pull request that touches `packaging/`, the workflow or the app icon; a tag or manual run ends
with a **draft** GitHub release holding the artifacts and `SHA256SUMS.txt`. Linux users build
from source (`cargo build --release -p ideacraft`) or use the web app.

| File | What |
|---|---|
| `env.sh` | Shared shell setup: `ROOT`, `VERSION` (from `[workspace.package]`, override `IDEACRAFT_VERSION`), `DIST`, build sha/date, `warn`, `copy_docs`, `sha256`. |
| `windows/package.ps1` | Builds `ideacraft.exe` and `ideacraft-cli.exe` (static CRT, no VC++ redistributable needed), checks their PE headers, signs, builds the MSI and the portable zip, writes the checksums. |
| `windows/sign.ps1` | Authenticode signing; prints a warning and leaves files unsigned when no signing material is configured. |
| `windows/ideacraft.wxs` | WiX v5 source: per-machine install under Program Files, Start menu shortcut, `.ideacraft` "Open with" association, licence dialog. Its `UpgradeCode` is IdeaCraft's alone. |
| `macos/package.sh` | Builds both Apple targets, `lipo`s a universal binary, builds the `.icns` with `sips`/`iconutil`, assembles `IdeaCraft.app`, signs (ad hoc by default), optionally notarizes, builds the DMG and the CLI zip, writes the checksums. |
| `macos/Info.plist.in` | Bundle template (`it.siriuslabs.ideacraft`); `@VERSION@`, `@SHORT_VERSION@`, `@BUILD_SHA@` are substituted. |
| `macos/entitlements.plist` | Hardened-runtime entitlements (empty on purpose). |

The app icon is `assets/app-icon/ideacraft.svg`; `cargo xtask icons` regenerates the committed
PNG and ICO files next to it (the `.icns` is built on the Mac at package time).

## Build locally

Windows (PowerShell 7, Rust MSVC toolchain, Windows SDK, .NET SDK):

```powershell
dotnet tool install --global wix --version 5.0.2
wix extension add -g WixToolset.UI.wixext/5.0.2
rustup target add x86_64-pc-windows-msvc
pwsh packaging/windows/package.ps1 -Arch x64      # → dist/release/*.msi, *-portable.zip
```

macOS (Xcode command-line tools):

```sh
rustup target add aarch64-apple-darwin x86_64-apple-darwin
packaging/macos/package.sh --arch universal       # → dist/release/*.dmg, ideacraft-cli-*.zip
packaging/macos/package.sh --arch aarch64 --skip-build   # repackage an existing build
```

Both write `SHA256SUMS-<os>-<arch>.txt` next to the artifacts. `IDEACRAFT_VERSION=0.2.0-rc.1`
overrides the version from `Cargo.toml`; `DIST` the output directory.

## Signing

Nothing is required: without these variables the builds succeed, unsigned, with a warning.

| Variable | Used by |
|---|---|
| `WINDOWS_CERTIFICATE` (base64 `.pfx`), `WINDOWS_CERTIFICATE_PASSWORD` | `sign.ps1`, certificate file |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_SIGNING_ENDPOINT`, `AZURE_SIGNING_ACCOUNT`, `AZURE_CERT_PROFILE` | `sign.ps1`, Azure Trusted Signing (all six) |
| `WINDOWS_TIMESTAMP_URL`, `SIGNTOOL` | `sign.ps1`, optional overrides |
| `MACOS_SIGN_IDENTITY`, `MACOS_KEYCHAIN` | `package.sh`, a Developer ID identity in a keychain on the build machine (default `-`, ad hoc) |
| `APPLE_ID`, `APPLE_PASSWORD` (app-specific), `APPLE_TEAM_ID` | `package.sh`, notarization (needs a real identity) |

In GitHub Actions the same names are read from repository secrets. A Developer ID certificate
would also need a step that imports it into a keychain on the runner before `package.sh` runs;
that step is not part of the workflow yet.
