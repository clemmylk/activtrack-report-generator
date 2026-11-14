# Release notes

## 1.8.2 — 14 November 2026

- Added in-app GitHub release checking, verified portable downloads, versioned installation and restart.
- Preserves report files, output folder and captured source; keeps previous application copy.
- Start tracking locks immediately and excludes FCL checks. FCL tracking is manual only.
- Dashboard retains observations and shows last-checked time.
- Restored optional notice fields with generated headers and remarks.
- Shared sea-vessel ETA policy excludes inland/truck dates from vessel reports and tracking.


## 1.8.1 — 13 November 2026

- Fixed long activity messages stretching the workspace beyond the window.
- Kept report/dashboard table scrolling inside the available space, with controls and footer visible.
- Show the complete latest activity message, with a larger View activity dialog for recent history.
- Verified layout at 1242x668, 1366x768 and 1920x1000; UI interaction checks passed.

### Updating

Extract this release into a new folder and launch its 02_START_APP.bat. Keep your existing report output folder. Recreate the desktop shortcut if needed.


## 1.8.0 — 13 November 2026

- Polished local React desktop interface with centred ActivTrack logo, compact Daily Operations heading, black/white layout, red primary actions and retained purple/blue tracking accents.
- Separate Reports, Tracking and Live Dashboard navigation; FCL and vessel-change views.
- Integrated notice headers and remarks with dashboard copy actions.
- Console/co-load booking report includes vessel and voyage from captured schedules and route legs.
- Portable Windows x64 release with bundled Python/dependencies, relative carrier-tool paths, desktop shortcut helper and troubleshooting guide.
- No saved login, credentials, customer reports or browser profiles in the release package.

Known limitations: unsigned release; configured ActivTrack deployment required; carrier site availability affects coverage; CMA CGM excluded from automated tracking; scheduled checks require the app/PC running; manual verification remains necessary for unverified results.
