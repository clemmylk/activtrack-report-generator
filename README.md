# ActivTrack Report Generator

**A Windows desktop workspace for authorised sea-import teams.** Capture ActivTrack bookings, create Excel reports, compare carrier schedules and review vessel/FCL changes. The v1.8.3 source package adds loading-plan checks against your latest captured booking data.

## What’s new in v1.8.3

**Available now as an uploaded [v1.8.3 source package](ActivTrack-Report-Generator-1.8.3-Source.zip). The latest published ready-to-run Windows release is still v1.8.2.** The source ZIP does not include Python or the prebuilt interface and cannot be installed by the in-app updater.

- **Loading Plan Check:** upload XLSX, CSV, TSV, TXT or Markdown, or paste a table with its headings. Compare it with the latest captured booking JSON in your report folder.
- **Vessel, voyage and ETD checks:** reviews all captured sea legs for transshipment bookings; truck/inland legs are excluded.
- **Missing-details review:** highlights missing HBL, MBL, container, consignee, package count, gross weight and CBM. Missing SO alone is not flagged.
- **Clear matches and exceptions:** shows HBL, booking and VS IDs, plan values and captured values; ambiguous and not-found records stay visible for manual review.
- **Common loading-plan layouts:** supports merged/shared cells and shifted cargo columns, with interpretation notes to review.
- **Filter and export:** search results, expand details and download a comparison CSV for Excel. The input collapses after checking to give the results more space.
- **Upcoming Arrivals sections:** a 48-hour overview, today, tomorrow and two days ahead, based on date-only sea-vessel ETAs.
- **Wan Hai tracking improvements:** normal/minimized Edge, fresh schedule parsing and explicit collection-failure results.
- **Experimental Arrivals Today:** refresh/recheck and an ActivTrack vessel-search shortcut. MarineTraffic login/verification or loading problems can prevent checks; reliable live arrival detection remains unverified.

Loading Plan Check is **read only** and compares a saved snapshot. “Not Found” means absent from that capture, not proof the booking does not exist in ActivTrack. PDF, XLS, images and scanned plans are unsupported. CARGOES Flow, Terminal49 and CallMeBot desktop integrations are not included.

## Download and start

1. Open [Releases](https://github.com/clemmylk/activtrack-report-generator/releases/latest) and download **ActivTrack-Report-Generator-1.8.2-Windows-x64.zip** under **Assets**. Do not use GitHub's automatically generated source-code ZIP as the installed application.
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

## App updates

Open **Update app** at the bottom of the side menu. Select **Check for updates**, then **Download & install** if a newer stable release is available. When installation is ready, select **Restart updated app**. The update checks the official GitHub release checksum, installs into a separate per-user folder, retargets desktop shortcuts and restores the captured-source/output folder. Finish active tracking/capture work first.

Reports stay in Documents/ActivTrack Reports (or your selected output folder). The previous app copy is retained and can be launched from its original folder if needed. No administrator access is required for the normal per-user installation. Windows/company policies may still block unsigned software.

Versions before 1.8.2 do not have this button. Download and extract 1.8.2 once to enable future in-app updates. The publisher must attach both the matching Windows portable ZIP and SHA256SUMS.txt to each published stable GitHub release. Drafts, prereleases, older versions and incomplete packages are not installed.

FCL checks are now manual only through the FCL dashboard **Check Now** button. Vessel tracking does not start container checks. All vessel-arrival reporting uses sea-vessel ETA, excluding truck legs. Missing sea ETA stays missing. The notice form accepts optional fields and generates a matching header and remarks.
