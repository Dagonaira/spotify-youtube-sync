"""Copy a YouTube playlist into a new Spotify playlist."""

import re

import requests
from spotipy.exceptions import SpotifyException

from common import get_spotify_client, get_youtube_client, load_progress, save_progress
from sync_core import ErrorKind, StepOutcome, StopSignal, run_sync_loop

NOISE_PATTERN = re.compile(
    r"[\(\[][^\)\]]*\b("
    r"official|video|audio|lyrics?|lyric video|visualizer|hd|4k|m/v|mv|"
    r"explicit|remaster(ed)?|live|full album|music video|hq"
    r")\b[^\)\]]*[\)\]]",
    re.IGNORECASE,
)


def extract_youtube_playlist_id(playlist_arg: str) -> str:
    match = re.search(r"[?&]list=([a-zA-Z0-9_-]+)", playlist_arg)
    if match:
        return match.group(1)
    return playlist_arg


def get_youtube_playlist_tracks(youtube, playlist_id):
    videos = []
    page_token = None
    while True:
        response = (
            youtube.playlistItems()
            .list(part="snippet", playlistId=playlist_id, maxResults=50, pageToken=page_token)
            .execute()
        )
        for item in response.get("items", []):
            snippet = item["snippet"]
            title = snippet.get("title", "")
            if title in ("Deleted video", "Private video"):
                continue
            videos.append(
                {
                    "title": title,
                    "channel": snippet.get("videoOwnerChannelTitle", ""),
                    "video_id": snippet.get("resourceId", {}).get("videoId"),
                }
            )
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return videos


def clean_title_and_artist(title: str, channel: str):
    artist_hint = None
    if channel.endswith(" - Topic"):
        artist_hint = channel[: -len(" - Topic")].strip()

    cleaned = NOISE_PATTERN.sub("", title)
    cleaned = cleaned.split("|")[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")

    if not artist_hint and " - " in cleaned:
        left, right = cleaned.split(" - ", 1)
        artist_hint, cleaned = left.strip(), right.strip()

    return cleaned, artist_hint


def search_spotify_track(sp, title: str, artist_hint):
    if artist_hint:
        results = sp.search(q=f'track:"{title}" artist:"{artist_hint}"', type="track", limit=1)
        items = results["tracks"]["items"]
        if not items:
            results = sp.search(q=f"{artist_hint} {title}", type="track", limit=1)
            items = results["tracks"]["items"]
    else:
        results = sp.search(q=title, type="track", limit=1)
        items = results["tracks"]["items"]
    return items[0]["uri"] if items else None


def create_spotify_playlist(sp, name: str, description: str) -> str:
    # Spotify migrated playlist creation to POST /me/playlists in Feb 2026;
    # the older per-user-id endpoint (spotipy's user_playlist_create) now
    # 403s outright for Development Mode apps. current_user_playlist_create
    # is spotipy's already-updated equivalent.
    playlist = sp.current_user_playlist_create(name, public=False, description=description)
    return playlist["id"]


def add_track_to_playlist(sp, playlist_id: str, track_uri: str):
    sp.playlist_add_items(playlist_id, [track_uri])


def classify_spotify_error(e: SpotifyException) -> ErrorKind:
    """Spotify has no hard daily cap like YouTube - just short-window rate
    limiting (429) and transient server errors (5xx), both safe to auto-retry.
    Anything else (403 forbidden, 404 not found, ...) is permanent.
    """
    status = getattr(e, "http_status", None)
    if status == 429 or (status is not None and status >= 500):
        return ErrorKind.RATE_LIMIT
    return ErrorKind.FATAL


def _retry_after_seconds(e: SpotifyException):
    headers = getattr(e, "headers", None) or {}
    try:
        return float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None


def make_spotify_step(sp, playlist_id):
    """Wraps search + add as one sync_core step. sp=None (dry-run) skips every
    API call and always records "no match found".
    """

    def step(video: dict) -> StepOutcome:
        if sp is None:
            return StepOutcome(result={"title": video["title"], "track_uri": None, "added": False})

        title, artist_hint = clean_title_and_artist(video["title"], video.get("channel", ""))
        try:
            track_uri = search_spotify_track(sp, title, artist_hint)
        except SpotifyException as e:
            kind = classify_spotify_error(e)
            stop = StopSignal(kind=kind, message=str(e), retry_after_seconds=_retry_after_seconds(e))
            if kind == ErrorKind.FATAL:
                result = {"title": video["title"], "track_uri": None, "added": False}
                return StepOutcome(result=result, stop=stop)
            return StepOutcome(result=None, stop=stop)
        except requests.exceptions.RequestException as e:
            # DNS failure, dropped connection, timeout, etc. - always
            # transient, never a reason to give up on the whole sync.
            return StepOutcome(result=None, stop=StopSignal(kind=ErrorKind.RATE_LIMIT, message=f"Network error: {e}"))

        if not track_uri:
            return StepOutcome(result={"title": video["title"], "track_uri": None, "added": False})

        try:
            add_track_to_playlist(sp, playlist_id, track_uri)
        except SpotifyException as e:
            kind = classify_spotify_error(e)
            stop = StopSignal(kind=kind, message=str(e), retry_after_seconds=_retry_after_seconds(e))
            if kind == ErrorKind.FATAL:
                result = {"title": video["title"], "track_uri": track_uri, "added": False}
                return StepOutcome(result=result, stop=stop)
            # Rate-limit hit mid-add: record nothing so this track (which DID
            # match) is retried from scratch on resume.
            return StepOutcome(result=None, stop=stop)
        except requests.exceptions.RequestException as e:
            return StepOutcome(result=None, stop=StopSignal(kind=ErrorKind.RATE_LIMIT, message=f"Network error: {e}"))

        return StepOutcome(result={"title": video["title"], "track_uri": track_uri, "added": True})

    return step


def run(args):
    if args.resume:
        progress_path = args.resume
        progress = load_progress(progress_path)
        if progress is None:
            raise SystemExit(f"Progress file not found: {progress_path}")
        print(f"Resuming '{progress['name']}' ({len(progress['results'])}/{len(progress['tracks'])} videos done so far)")
    else:
        if not args.name:
            raise SystemExit("--name is required (unless using --resume)")
        if not args.youtube_playlist:
            raise SystemExit("Provide --youtube-playlist <url_or_id>")

        print("Authenticating with YouTube...")
        youtube = get_youtube_client()

        playlist_id = extract_youtube_playlist_id(args.youtube_playlist)
        print("Fetching videos from YouTube playlist...")
        videos = get_youtube_playlist_tracks(youtube, playlist_id)
        print(f"Found {len(videos)} videos.")

        safe_name = re.sub(r"[^\w\-]+", "_", args.name)
        progress_path = f"progress_tospotify_{safe_name}.json"
        progress = {
            "direction": "youtube_to_spotify",
            "name": args.name,
            "tracks": videos,
            "results": [],
            "spotify_playlist_id": None,
        }
        save_progress(progress_path, progress)

    sp = None if args.dry_run else get_spotify_client_verbose()
    if sp is not None and not progress["spotify_playlist_id"]:
        print(f"Creating Spotify playlist '{progress['name']}'...")
        progress["spotify_playlist_id"] = create_spotify_playlist(sp, progress["name"], "Imported from YouTube")
        save_progress(progress_path, progress)

    def on_progress(p):
        r = p["results"][-1]
        idx = len(p["results"])
        total = len(p["tracks"])
        print(f"[{idx}/{total}] {r['title']}")
        if r["added"]:
            print(f"   -> added {r['track_uri']}")
        elif args.dry_run:
            print("   -> (dry-run, not searching Spotify)")
        else:
            print("   -> no match found")

    step = make_spotify_step(sp, progress.get("spotify_playlist_id"))
    stop = run_sync_loop(
        progress, progress_path, progress["tracks"], step,
        on_progress=on_progress, sleep_seconds=0.1, limit=args.limit,
    )

    if stop is not None:
        if stop.kind in (ErrorKind.QUOTA, ErrorKind.RATE_LIMIT):
            print("Spotify API rate limit hit. Saving progress and stopping.")
            print(f"Re-run shortly with: python sync.py to-spotify --resume {progress_path}")
            raise SystemExit(1)
        if stop.kind == ErrorKind.FATAL:
            print(f"Stopped on an error that won't resolve by waiting: {stop.message}")
            raise SystemExit(1)
        if stop.message.startswith("limit"):
            print(f"Reached --limit {args.limit} for this run. Re-run with --resume {progress_path} to continue.")

    done = len(progress["results"])
    total = len(progress["tracks"])
    matched = sum(1 for r in progress["results"] if r["track_uri"])
    added = sum(1 for r in progress["results"] if r["added"])
    print(f"\nProcessed {done}/{total} videos. Matched {matched}, added {added} to Spotify.")
    if done < total:
        print(f"Not finished yet. Re-run with: python sync.py to-spotify --resume {progress_path}")
    elif not args.dry_run:
        print(f"Done! Playlist: https://open.spotify.com/playlist/{progress['spotify_playlist_id']}")


def get_spotify_client_verbose():
    print("Authenticating with Spotify...")
    return get_spotify_client()
