# Building from source

End users should download the portable ZIP from Releases. GitHub's Code → Download ZIP is a source checkout and does not include Python or the prebuilt interface/runtime.

For development on Windows x64: install Python 3.14 with Tk and Node.js 22+, create a virtual environment, then `python -m pip install -r requirements.txt`. In web_ui run `npm ci` then `npm run build`. From the repository root run `python polished_app.py`. Carrier tools are included under carrier_bots; Trico shares the vessel-watch/watch modules. Do not point tools at a colleague's profiles or data folders.

Public releases must use an explicit allowlist. Exclude .runtime, browser profiles, credentials, reports, JSON captures, logs, local paths and node_modules. Include relevant third-party licences and a SHA-256 checksum. Test the relocated runtime and synthetic report generation before release. This repository's application licence is limited-use; publication is not an open-source resale licence.
