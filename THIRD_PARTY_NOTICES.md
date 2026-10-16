# Third-party notices and redistribution gates

## CredClaude reference

Native macOS UI/settings/action patterns were reviewed and adapted from CredClaude. Preserve this notice with any copied or substantially adapted portions.

```text
MIT License

Copyright (c) 2026 virajparmaj

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## CredCodex reference

The inspected CredCodex checkout had no license file or declared license metadata. Access to source does not establish redistribution permission. Resolve permission for any copied or substantially adapted material before external redistribution. This app does not load the reference checkout at runtime.

## Runtime and build dependencies

Python is provided under the Python Software Foundation license. rumps uses BSD licensing; PyObjC uses MIT licensing. PyInstaller uses GPLv2-or-later with an exception allowing distribution of applications it creates. Pillow uses the HPND license. pytest and its dependencies are development-only. Consult the installed distributions' license files for exact texts and transitive notices; they are not replaced by this summary. The built app contains its bundled dependencies, collected runtime distribution metadata and license files, Python's `LICENSE.txt`, and PyInstaller's bootloader licensing/exception text. Review transitive native-library notices before external release.

The current app icon is adapted from user-supplied CRED.png artwork, and the menu-bar emblem is derived from the user-supplied cred-menu.png silhouette. Supplying artwork does not establish third-party trademark or redistribution rights. The earlier allowance-ring SVG was created for CredUsage.

Provider artwork is bundled from official installed app resources: the actual Codex light/dark app icons from ChatGPT.app (`icon-codex-light.png`, `icon-codex-dark-color.png`), Claude's sunburst template from Claude.app (`TrayIconTemplate@3x.png`, tinted orange), Cursor.app's `Cursor.icns`, and Antigravity.app's `icon.icns`. OpenAI, Anthropic, Cursor, and Google retain ownership of their respective artwork and marks. These assets identify the integrations and do not imply affiliation or endorsement. Source details and export treatments are recorded in `credusage/assets/README.md`.

This repository does not declare a project-wide redistribution license yet; the owner must select one before public release. Developer ID signing and notarization are also outstanding external-distribution gates.
