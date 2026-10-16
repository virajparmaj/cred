# CredUsage icons

The app icon uses the user-supplied `CRED.png` artwork, adapted with the built-in imagegen tool to remove its white surround and center the charcoal tile on a transparent macOS icon canvas. The original input is retained in the top-level `assets/CRED-source.png`; the editing prompt is recorded in `assets/CRED-icon-prompt.txt`.

`AppIcon.png` is the authoritative 1024-pixel RGBA master. `./build_icon.sh` uses macOS `sips` and `iconutil` to generate the 16, 32, 128, 256, and 512-point representations at 1x and 2x and compile `AppIcon.icns`. The app build runs this automatically. `AppIcon.svg` is retained as the earlier allowance-ring design; it is not the source of the current app icon.

`MenuIconTemplate.png` (18px) and `MenuIconTemplate@2x.png` (36px) are derived from an 800px center crop of `assets/CRED-menu-silhouette.png`, removing export padding before downsampling. Both represent the same 18-point monochrome menu-bar emblem; macOS tints it for light and dark appearances. Runtime paths resolve relative to `credusage/assets`, including in the frozen app.

## Official provider assets

Bundled on October 16, 2026 from installed vendor app resources. These are vendor artwork, not generated imitations. No installed vendor app is required to render them at runtime.

| Export | Official source | Treatment |
| --- | --- | --- |
| `ProviderCodex.png` / `ProviderCodexDark.png` | ChatGPT.app `Contents/Resources/icon-codex-light.png` / `icon-codex-dark-color.png` | Actual Codex app icon, resized to 128px; switches with appearance. |
| `ProviderClaude.png` | Claude.app `Contents/Resources/TrayIconTemplate@3x.png` | Official sunburst silhouette, rendered in Claude orange (`#D97857`) by AppKit. |
| `ProviderCursor.png` | Cursor.app `Contents/Resources/Cursor.icns` | Official current app icon, exported to 128px PNG. |
| `ProviderAntigravity.png` | Antigravity.app `Contents/Resources/icon.icns` | Official app icon, exported to 128px PNG. |

Source app artwork remains the property of its respective owners. The OpenAI knot is not used for Codex.
