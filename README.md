# Spotify ⇄ YouTube playlist sync

A command-line tool (no GUI — you run it from a terminal) that copies a playlist
from Spotify to YouTube, or from YouTube to Spotify, into a new **private** playlist
on the destination service.

Requires Python 3.10+ (this machine has 3.13 installed).

## 1. Install dependencies

```bash
pip install -r requirements.txt
```

## 2. Set up Spotify API access

1. Go to https://developer.spotify.com/dashboard and log in.
2. Click **Create app**. Name/description can be anything.
3. Set **Redirect URI** to `http://127.0.0.1:8888/callback` and save.
4. Open the app, copy the **Client ID** and **Client Secret**.
5. Copy `.env.example` to `.env` and fill in:
   ```
   SPOTIFY_CLIENT_ID=...
   SPOTIFY_CLIENT_SECRET=...
   SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
   ```

## 3. Set up YouTube API access

1. Go to https://console.cloud.google.com/ and create a new project (or pick an existing one).
2. In **APIs & Services > Library**, search for **YouTube Data API v3** and enable it.
3. In **APIs & Services > OAuth consent screen**, choose **External**, fill in the required
   fields, and add your own Google account email under **Test users** (this keeps the app
   unpublished, which is fine for personal use).
4. In **APIs & Services > Credentials**, click **Create Credentials > OAuth client ID**.
   - Application type: **Desktop app**.
   - Download the JSON file, rename it to `client_secret.json`, and put it in this folder
     (next to `sync.py`).

The first time you run a command it opens a browser window for you to log into Spotify
and/or Google and approve access. Tokens are cached locally (`.spotify_cache`,
`youtube_token.json`) so you won't have to log in again on later runs.

## 4. Run it

**Spotify → YouTube**, a specific playlist:

```bash
python sync.py to-youtube --spotify-playlist "https://open.spotify.com/playlist/XXXXXXXX" --name "My Playlist on YouTube"
```

**Spotify → YouTube**, your Liked Songs:

```bash
python sync.py to-youtube --liked-songs --name "My Liked Songs"
```

**YouTube → Spotify**, a specific playlist:

```bash
python sync.py to-spotify --youtube-playlist "https://www.youtube.com/playlist?list=XXXXXXXX" --name "My Playlist on Spotify"
```

Preview matches first without touching the destination service:

```bash
python sync.py to-youtube --spotify-playlist "..." --name "My Playlist" --dry-run
python sync.py to-spotify --youtube-playlist "..." --name "My Playlist" --dry-run
```

## Quota / rate limits

- **YouTube** gives every project a free quota of 10,000 units/day. Copying *to* YouTube
  costs ~150 units/track (100 for the search + 50 to add it), so you can add roughly
  **60-65 tracks per day**. Copying *from* YouTube (reading a playlist) only costs ~1 unit
  per 50 videos, so that direction is essentially unlimited.
- **Spotify** doesn't have a hard daily cap, just short-term rate limiting, so copying *to*
  Spotify normally finishes in one run even for large playlists.

Either direction saves progress after every track/video to a `progress_*.json` file. If a
run stops early (quota hit, network blip, closed terminal), re-run with the printed
`--resume <file>` command to continue — it won't add duplicates. You can also pass
`--limit N` on any run to deliberately stop after N items.

## Matching quality

- **Spotify → YouTube**: searches YouTube for `"<track name> <artist names>"`, restricted to
  the Music category, and takes the top result.
- **YouTube → Spotify**: cleans up the video title (strips things like "(Official Video)",
  "(Lyrics)", etc.), and uses the uploading channel as an artist hint when it's a
  "\<Artist\> - Topic" auto-generated channel (common for music uploaded via YouTube Music),
  then searches Spotify's catalog for the best match.

Neither is perfect — covers, remixes, and obscure tracks can mismatch. Check the printed
per-track output, or spot-check the created playlist afterwards.

New playlists are created as **private**; change visibility from Spotify's/YouTube's own UI
if you want them public.

## Found a bug?

Open an issue: https://github.com/Dagonaira/spotify-youtube-sync/issues/new

If you're using the desktop app, click **Report a bug** in the top-right corner of the
window - it opens a pre-filled issue in your browser. Please attach `crossplay.log` if you
can (there's an **Open log folder** button right next to it); it's the app's error log and
usually the fastest way to figure out what went wrong.
