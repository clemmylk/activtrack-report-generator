# Troubleshooting

## Nothing opens / missing python.exe

Extract the entire portable ZIP first; runtime/pythonw.exe and web_ui/dist must remain beside the application. Run 03_DIAGNOSE.bat to check bundled dependencies. If startup fails, a message points to the per-user startup-error.log under LocalAppData/ActivTrack Report Generator. Share only a redacted error excerpt, never a browser profile or shipment snapshot.

If Windows or company security blocks this unsigned release, ask your IT administrator to review/approve it. Do not disable Smart App Control or antivirus. Code signing is not included in this release.

## Signed in, but capture cannot find bookings

Use Open ActivTrack inside this copy of the app. Sign in and filter Bookings in the exact Edge window it opens, rather than an unrelated browser window. Keep it open, wait for the table to load, then capture. If the browser was closed, reopen it from the app. A saved JSON import uses existing data; it is not a substitute for a fresh login/capture.

## Edge driver / first-run download fails

Update Microsoft Edge and allow the application's Selenium driver manager to download a compatible Edge driver. Corporate proxies/firewalls may require IT configuration. Ensure the PC is online and can access ActivTrack and the relevant carrier website. Do not install random browser drivers from unofficial sites.

## Tracking has N/A, no match or lower coverage

Check vessel spelling, voyage, route leg, POL/POD and baseline timestamp. Carrier site layouts, protected pages, missing future sailings or unavailable historical calls can prevent matches. Finish verification manually if requested. Check MANUAL TRACKING and evidence; do not fill unknown dates by guessing. CMA CGM is intentionally disabled in automatic runs. Coverage varies and is not guaranteed.

## Workbook cannot be saved or appears locked

Close the existing workbook in Excel and retry. Use a writable output folder and avoid editing a workbook during generation. Source snapshots can be imported to rebuild a report without recapturing. Use a current Excel-compatible application to view filters/formatting.

## Dashboard is empty / old

Capture a fresh source, run tracking and check the run/source timestamp. Scheduled FCL checks need the app running, the PC awake and internet access. N/A is an evidence gap. This app is not a hosted background service when the PC is off.

## Help and bugs

Open an issue in the GitHub repository with app version, Windows/Edge version, tool used, expected result and a redacted error/screenshot. Never upload passwords, API keys, cookies, customer shipment reports or unredacted JSON captures. Provide a synthetic example when possible.
