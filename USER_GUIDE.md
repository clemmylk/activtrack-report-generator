# User guide

## 1. Install and open

Use a 64-bit Windows PC with Microsoft Edge. Extract the whole release ZIP into Documents or another folder your Windows account can write to. Keep all folders together. Run 02_START_APP.bat. You do not need administrator rights, Python or Node for normal use. The app opens a local desktop window powered by Edge. This window is separate from the Edge page used to sign in to ActivTrack.

You can create a desktop shortcut using 01_CREATE_DESKTOP_SHORTCUT.vbs. Moving the extracted folder later requires recreating the shortcut. To uninstall, close the app and delete its extracted folder and shortcut; your reports remain in the output folder.

## 2. Capture bookings and generate reports

1. On Overview, check the report date and output folder. The report date controls checks such as missing actual departure/arrival dates.
2. Keep Save source snapshot selected for tracking and future rebuilding.
3. Select Open ActivTrack. Sign in yourself. Do not share your password or session files.
4. Go to Sea Import → Bookings. Apply the required booking date range and filters on this same browser page. Wait for the table to finish loading.
5. Return to the app. Click Capture & Generate Excel once. Follow System Activity; allow capture to finish without changing the source filters or closing its browser.
6. Open the workbook from the completion action or Open Reports Folder. The source JSON is saved beside the workbook when selected.

Import Saved JSON rebuilds a report from a previously captured source; it does not fetch a fresh live booking list. All Vessels creates a vessel/schedule view using the selected booking scope. Check the source timestamp before treating a report as current.

### Workbook reading

- Vessel Summary groups schedules and preserves route legs.
- Console & Co-load lists LCL bookings under their classification. Purple indicates console; light blue indicates co-load. Classification follows this deployment's configured agent rules; it is not a universal carrier definition.
- Booking Vessel Details provides the booking/route breakdown.
- ATD Missing and ATA Missing highlight missing actual dates for the report's checks.
- In ATD Missing, FCL rows use bold text. Olive/pink container-recorded/missing accents are limited to the VS ID cell so they do not overwrite classification colours.
- The all-vessels report supports Excel carrier filters. MSC names use purple and transshipment text uses dark green.
- Frozen vessel-identification columns keep the VS ID and vessel visible while scrolling relevant tracking workbooks.

## 3. Automated Tracking

Capture first, then select Automated Tracking. The run locks a source snapshot, groups eligible vessels, assigns available carrier tools, skips repeated requests and runs websites sequentially. Legs with recorded ATA are not a new in-transit tracking queue. Source schedule identity, voyage, POL/POD and leg remain important even when a vessel name repeats.

Carrier sites can open Edge windows and may require login or human verification. Complete those steps yourself when prompted. Verification-required tools allow a waiting period; do not close their browser while a run is active. CMA CGM is excluded from automatic runs in this version. Use Stop to request cancellation; allow the current browser operation to return.

Read the generated workbook:

| Sheet | What to do |
| --- | --- |
| ACTION REQUIRED | Review detected date differences and missing actual information |
| PRIORITY WATCH | Start with console/co-load and important connection checks |
| ALL RESULTS | Inspect the full comparison and available source evidence |
| MANUAL TRACKING | Check vessels/sites that could not be verified, grouped by carrier |
| NO CHANGE | Matched results without a detected change |
| RUN INFO | Check locked source, run details and coverage |

New dates later than baseline are bold red; earlier dates are bold green; unchanged dates use normal black. No-data/unmatched rows require manual review. These tools prepare comparisons and notices; they do not automatically save vessel date changes or send customer messages.

## 4. Manual and long-term tracking

Manual Carrier Tracking: choose a carrier, paste vessel names on separate lines (or comma/semicolon-separated) and select Track. Check the output folder shown at completion. Requests for the same vessel are deduplicated.

Long Term Auto: paste a future sailing schedule containing vessel, voyage, ports and dates. Review the parsed input, run tracking and compare with the captured source. It is an on-demand future-schedule check, not a recurring weekly scheduler. Review detected changes and undetected vessels separately.

## 5. Live Dashboard and FCL

Switch between FCL Container Tracking and Vessel Changes. Use search and available carrier/status filters. Click a VS ID or vessel text to copy it. Vessel changes prioritise consoles. Right-click a change row for copyable notice header and remarks.

The FCL workflow uses captured container details, compares carrier and owner sources where supported and prioritises MSC risk. Checks run only when you press Check Now in FCL Container Tracking. Automatic timed FCL rounds are disabled. The computer must be awake and connected. It is not an always-on hosted service.

| Status | Meaning |
| --- | --- |
| IN TRANSIT | Available evidence indicates movement toward the relevant destination; red status |
| DEPARTED | Supported departure evidence; dark-green row/green status |
| ARRIVED | Supported relevant destination arrival evidence; light-green row |
| N/A | No verified usable result; never assume arrival or departure |

Review recorded/first-seen date evidence where available. A snapshot's first observation is not always the exact time a user entered a container. Vessel arrival alone does not prove an individual container was discharged. Verify evidence before changing shipment records.

## 6. Notice preparation

Notice Headers & Remarks prepares copyable text. Supply a reason where known; confirm original/revised dates, the changed leg, vessel and voyage. Copy the result into the approved communication workflow. Copying is not sending. The dashboard's right-click copy actions use the detected change for that row.

## 7. Files and routine use

Main output: Documents/ActivTrack Reports unless changed in settings. Carrier comparisons are organised under Carrier Tracking Reports; tracking audit files may also be created inside the extracted carrier tool folders. Keep JSON snapshots private because they contain operational booking information.

Each user signs in independently. Never give colleagues a copy of your already-used application folder with browser profiles, runtime sessions, credentials or reports. Share only the clean release ZIP from GitHub. Close the app when finished and recapture fresh data before the next day's review.

## App updates

Open **Update app** at the bottom of the side menu. Select **Check for updates**, then **Download & install** if a newer stable release is available. When installation is ready, select **Restart updated app**. The update checks the official GitHub release checksum, installs into a separate per-user folder, retargets desktop shortcuts and restores the captured-source/output folder. Finish active tracking/capture work first.

Reports stay in Documents/ActivTrack Reports (or your selected output folder). The previous app copy is retained and can be launched from its original folder if needed. No administrator access is required for the normal per-user installation. Windows/company policies may still block unsigned software.

Versions before 1.8.2 do not have this button. Download and extract 1.8.2 once to enable future in-app updates. The publisher must attach both the matching Windows portable ZIP and SHA256SUMS.txt to each published stable GitHub release. Drafts, prereleases, older versions and incomplete packages are not installed.

FCL checks are now manual only through the FCL dashboard **Check Now** button. Vessel tracking does not start container checks. All vessel-arrival reporting uses sea-vessel ETA, excluding truck legs. Missing sea ETA stays missing. The notice form accepts optional fields and generates a matching header and remarks.
