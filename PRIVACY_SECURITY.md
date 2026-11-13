# Privacy and security

The distributed archive contains application code, static UI assets, bundled Python and third-party packages. It deliberately excludes the author's reports, captured booking JSON, account/session files, API keys, browser profiles, logs and personal machine paths.

The desktop interface binds to 127.0.0.1 (this computer) with a random per-session token. It does not publish the interface to the internet. Runtime session information is created locally under .runtime when the app runs; do not share it.

ActivTrack and carrier websites receive requests needed for the selected operations. Selenium may download an Edge driver on first use. Your sign-in is performed in the browser; no shared account is included. Browser profiles and site cookies created during use must be kept private. Optional alert services require each user's own access and may transmit the selected vessel information to that service.

Reports and source JSON contain business/customer data and are saved locally. Treat the output folder and any tracking evidence as confidential. Container history and notification history are local working records, not a complete audit log of all ActivTrack edits.

This application does not grant access to other organisations' records. Only use accounts, data and carrier services you are authorised to access. No automatic schedule write or customer-email sending is performed by these reporting/comparison actions.

Report security concerns privately to the maintainer through an existing trusted channel. Never post credentials or operational captures in public GitHub issues.
