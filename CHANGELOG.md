# Changelog

All notable changes to Crossplay, newest first. Each entry links to its GitHub release, which has the matching `Crossplay.exe` download attached.

## [v1.5.0](https://github.com/Dagonaira/spotify-youtube-sync/releases/tag/v1.5.0) - 2026-09-15

- The "added" and "not found" counts on each sync are now clickable - they open a panel with the full list (not just the last 25), so you can review everything even while a sync is paused or waiting.
- For tracks that weren't found, you can now listen to the original and search for an alternative on the other service, both in a small popup window - no need to leave the app.
- Fixed the sync list visibly jumping while scrolling.

## [v1.4.0](https://github.com/Dagonaira/spotify-youtube-sync/releases/tag/v1.4.0) - 2026-09-14

- Fixed a real bug where syncing a Spotify playlist (not Liked Songs) to YouTube could finish instantly showing 0 tracks found. Spotify quietly renamed a field in their playlist API, and the app was asking for the old field name - Liked Songs uses a different endpoint and was never affected.

## [v1.3.0](https://github.com/Dagonaira/spotify-youtube-sync/releases/tag/v1.3.0) - 2026-09-11

- Fixed Spotify getting stuck as "not connected" for long stretches: the app was checking your login every 5 seconds, and each check made a real call to Spotify just to confirm the token still worked - with more than one window/tab open at once, that could tip Spotify's own rate limiter and lock the app out for hours. Status checks are now cached for 20 seconds, and the "rate limited" message is honest about how long that can actually take instead of promising "within an hour."
- Early groundwork for an Android version: a new `serve_pwa.py` lets Crossplay run as a plain web app (no desktop window) that a phone can install as a home-screen app via a free HTTPS tunnel. Still experimental and not part of the packaged .exe.

## [v1.2.0](https://github.com/Dagonaira/spotify-youtube-sync/releases/tag/v1.2.0) - 2026-09-11

- Cancel now works properly on a running sync - the button was hidden while a job was active before, forcing an unintuitive pause-then-remove. It also now permanently discards that job's saved progress so it can't quietly reappear on next launch.
- A couple of background-reliability fixes found along the way (a rare crash-on-cancel race, and background errors that previously went completely unlogged).
- A real app icon, instead of the generic default.

## [v1.1.0](https://github.com/Dagonaira/spotify-youtube-sync/releases/tag/v1.1.0) - 2026-09-11

- In-app bug reporting: a "Report a bug" button (top-right) opens a pre-filled issue on GitHub, and an "Open log folder" button next to it makes it easy to attach the app's error log.

## [v1.0.0](https://github.com/Dagonaira/spotify-youtube-sync/releases/tag/v1.0.0) - 2026-09-11

First release. Download `Crossplay.exe` and run it directly - no install needed. On first run, click "Connect Spotify" and "Connect YouTube" to log into your own accounts, then pick a playlist and go.
