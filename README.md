# ActivTrack Report Generator

**Windows desktop app for authorised sea-import operations teams.** Capture filtered ActivTrack bookings, generate Excel reports, compare carrier schedules and review FCL/container and vessel changes in one workspace.

## Download and start

1. Open [Releases](https://github.com/clemmylk/activtrack-report-generator/releases/latest) and download **ActivTrack-Report-Generator-1.8.1-Windows-x64.zip** under **Assets**. Do not use GitHub's automatically generated source-code ZIP as the installed application.
2. Right-click the downloaded ZIP → **Extract All**. Put the extracted folder somewhere writable, such as Documents. Do not run the app inside the ZIP.
3. Open the extracted folder and double-click **02_START_APP.bat**.
4. Optional: run **01_CREATE_DESKTOP_SHORTCUT.vbs** once to create a desktop shortcut.

**Python, its dependencies and the finished interface are included. No Python or Node installation is required.** Microsoft Edge must be installed. Internet access and your own authorised ActivTrack account are required. Excel or another compatible spreadsheet app is needed to view workbooks.

## Your first report

1. Click **Open ActivTrack**, then sign in using your own account.
2. On the **Bookings** page, choose the date range and any other filters. Wait for the bookings table to load.
3. Return to the app and click **Capture & Generate Excel**. Keep the same Edge page open while capturing.
4. Open the saved report. The source JSON snapshot supports comparison, rebuilding and the tracking tools.
5. Run **Automated Tracking** after capture to check available carrier sites one at a time. Review the resulting workbook and **Live Dashboard**.

Default reports folder: **Documents → ActivTrack Reports**. Change it in Report settings if needed.

## Included tools

| Tool | Purpose |
| --- | --- |
| Reports | Vessel summary, console/co-load booking detail, booking/leg detail, missing ATD/ATA, approaching arrivals and container checks |
| All Vessels | Grouped vessel/schedule report with carrier filters and last notification information where available |
| Automated Tracking | Serial carrier checks against the captured baseline; action-required, priority, manual-check and no-change sheets |
| Manual Carrier Tracking | Paste a vessel list for one carrier; repeated names are skipped |
| Long Term Auto | Paste future sailings, check carrier schedules and compare with captured ActivTrack data |
| Live Dashboard | Separate views for FCL containers and vessel date changes; console changes first |
| Notice Headers & Remarks | Prepare and copy notice text; right-click a dashboard change to copy its header or remarks |
| Arrival / Departure Alerts | Optional alert workflow; some external services require the user's own credentials |

The application reads the configured ActivTrack deployment. It is not a replacement login/account service and is not automatically compatible with every freight system. Carrier availability and site changes affect tracking coverage. **CMA CGM is temporarily excluded from automated runs**; manual tracking remains available. Unsupported or unverified results go to manual review. A missing result is not evidence of arrival.

## Instructions and help

- [User guide](USER_GUIDE.md): report workflow, colour meanings, tracking, dashboard and notices.
- [Troubleshooting](TROUBLESHOOTING.md): startup, Edge sign-in/capture, blocked downloads and no-data results.
- [Privacy and security](PRIVACY_SECURITY.md): local storage and account handling.
- [Release notes](CHANGELOG.md) and [licensing](LICENSE.txt).

This is an **unsigned portable release**, not a signed Windows installer. Windows or company policy may block it. Use your IT team's approval process; do not disable security controls to install it. This package contains no shared login, saved browser session, API key or shipment report.

Copyright Globactive Logistics Sri Lanka | developed by Clement R.
