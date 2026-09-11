"""Copy a Spotify playlist (or Liked Songs) into a new YouTube playlist."""

import json
import re

import requests
from googleapiclient.errors import HttpError

from common import get_spotify_client, get_youtube_client, load_progress, save_progress
from sync_core import ErrorKind, StepOutcome, StopSignal, run_sync_loop


def extract_spotify_playlist_id(playlist_arg: str) -> str:
    match = re.search(r"playlist[/:]([a-zA-Z0-9]+)", playlist_arg)
    return match.group(1) if match else playlist_arg


def get_spotify_tracks(sp, playlist_id, liked_songs: bool):
    tracks = []
    if liked_songs:
        results = sp.current_user_saved_tracks(limit=50)
    else:
        results = sp.playlist_items(
            playlist_id,
            fields="items(track(name,artists(name))),next",
            additional_types=["track"],
        )
    while results:
        for item in results["items"]:
            track = item.get("track")
            if not track:
                continue
            name = track.get("name")
            artists = ", ".join(a["name"] for a in track.get("artists", []))
            if name and artists:
                tracks.append({"name": name, "artists": artists})
        results = sp.next(results) if results.get("next") else None
    return tracks


def search_youtube_video(youtube, query: str):
    response = (
        youtube.search()
        .list(q=query, part="id", type="video", maxResults=1, videoCategoryId="10")
        .execute()
    )
    items = response.get("items", [])
    return items[0]["id"]["videoId"] if items else None


def create_youtube_playlist(youtube, title: str, description: str) -> str:
    response = (
        youtube.playlists()
        .insert(
            part="snippet,status",
            body={
                "snippet": {"title": title, "description": description},
                "status": {"privacyStatus": "private"},
            },
        )
        .execute()
    )
    return response["id"]


def add_video_to_playlist(youtube, playlist_id: str, video_id: str):
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()


def classify_youtube_error(e: HttpError) -> ErrorKind:
    """A 403 from YouTube can mean quota exhaustion (safe to auto-retry once the
    daily quota resets) or a permanent condition (blocked video, ToS
    restriction, playlist ownership) that will fail identically forever.
    Separately, YouTube surfaces plain transient server-side hiccups (a 409
    "operation was aborted" / SERVICE_UNAVAILABLE, or a raw 5xx) - those are
    safe to retry soon, not something to give up on.
    """
    status = e.resp.status if e.resp is not None else None
    reason = None
    message = ""
    try:
        content = e.content.decode("utf-8") if isinstance(e.content, bytes) else e.content
        body = json.loads(content)
        errors = body.get("error", {}).get("errors", [])
        reason = errors[0].get("reason") if errors else body.get("error", {}).get("status")
        message = (errors[0].get("message") if errors else body.get("error", {}).get("message")) or ""
    except Exception:
        reason = None

    if reason in ("quotaExceeded", "dailyLimitExceeded"):
        return ErrorKind.QUOTA
    if reason in ("userRateLimitExceeded", "rateLimitExceeded", "backendError", "internalError", "SERVICE_UNAVAILABLE"):
        # YouTube conflates "per day" quota exhaustion under this SAME generic
        # reason code as genuine short-window bursts - the only way to tell
        # them apart is the message text. A day-scoped exhaustion needs to
        # wait for the actual daily reset, not retry every 60 seconds forever.
        if "per day" in message.lower():
            return ErrorKind.QUOTA
        return ErrorKind.RATE_LIMIT
    if status == 429:
        return ErrorKind.RATE_LIMIT
    if status in (409, 500, 502, 503, 504):
        return ErrorKind.RATE_LIMIT
    return ErrorKind.FATAL


def make_youtube_step(youtube, playlist_id):
    """Wraps search + add as one sync_core step. youtube=None (dry-run) skips
    every API call and always records "no match found".
    """

    def step(track: dict) -> StepOutcome:
        if youtube is None:
            return StepOutcome(
                result={"name": track["name"], "artists": track["artists"], "video_id": None, "added": False}
            )

        query = f"{track['name']} {track['artists']}"
        try:
            video_id = search_youtube_video(youtube, query)
        except HttpError as e:
            kind = classify_youtube_error(e)
            stop = StopSignal(kind=kind, message=str(e))
            if kind == ErrorKind.FATAL:
                result = {"name": track["name"], "artists": track["artists"], "video_id": None, "added": False}
                return StepOutcome(result=result, stop=stop)
            return StepOutcome(result=None, stop=stop)
        except requests.exceptions.RequestException as e:
            # DNS failure, dropped connection, timeout, etc. - always
            # transient, never a reason to give up on the whole sync.
            return StepOutcome(result=None, stop=StopSignal(kind=ErrorKind.RATE_LIMIT, message=f"Network error: {e}"))

        if not video_id:
            return StepOutcome(
                result={"name": track["name"], "artists": track["artists"], "video_id": None, "added": False}
            )

        try:
            add_video_to_playlist(youtube, playlist_id, video_id)
        except HttpError as e:
            kind = classify_youtube_error(e)
            stop = StopSignal(kind=kind, message=str(e))
            if kind == ErrorKind.FATAL:
                result = {"name": track["name"], "artists": track["artists"], "video_id": video_id, "added": False}
                return StepOutcome(result=result, stop=stop)
            # Quota/rate-limit hit mid-add: record nothing so this track (which
            # DID match) is retried from scratch on resume, instead of being
            # wrongly locked in as "no match found" forever.
            return StepOutcome(result=None, stop=stop)
        except requests.exceptions.RequestException as e:
            return StepOutcome(result=None, stop=StopSignal(kind=ErrorKind.RATE_LIMIT, message=f"Network error: {e}"))

        return StepOutcome(
            result={"name": track["name"], "artists": track["artists"], "video_id": video_id, "added": True}
        )

    return step


def run(args):
    if args.resume:
        progress_path = args.resume
        progress = load_progress(progress_path)
        if progress is None:
            raise SystemExit(f"Progress file not found: {progress_path}")
        print(f"Resuming '{progress['name']}' ({len(progress['results'])}/{len(progress['tracks'])} tracks done so far)")
    else:
        if not args.name:
            raise SystemExit("--name is required (unless using --resume)")
        if not args.spotify_playlist and not args.liked_songs:
            raise SystemExit("Provide --spotify-playlist <url_or_id> or --liked-songs")

        print("Authenticating with Spotify...")
        sp = get_spotify_client()

        playlist_id = extract_spotify_playlist_id(args.spotify_playlist) if args.spotify_playlist else None
        print("Fetching tracks from Spotify...")
        tracks = get_spotify_tracks(sp, playlist_id, args.liked_songs)
        print(f"Found {len(tracks)} tracks.")

        safe_name = re.sub(r"[^\w\-]+", "_", args.name)
        progress_path = f"progress_toyoutube_{safe_name}.json"
        progress = {
            "direction": "spotify_to_youtube",
            "name": args.name,
            "tracks": tracks,
            "results": [],
            "youtube_playlist_id": None,
        }
        save_progress(progress_path, progress)

    youtube = None if args.dry_run else get_youtube_client_verbose()
    if youtube is not None and not progress["youtube_playlist_id"]:
        print(f"Creating YouTube playlist '{progress['name']}'...")
        progress["youtube_playlist_id"] = create_youtube_playlist(youtube, progress["name"], "Imported from Spotify")
        save_progress(progress_path, progress)

    def on_progress(p):
        r = p["results"][-1]
        idx = len(p["results"])
        total = len(p["tracks"])
        print(f"[{idx}/{total}] {r['name']} {r['artists']}")
        if r["added"]:
            print(f"   -> added https://youtu.be/{r['video_id']}")
        elif args.dry_run:
            print("   -> (dry-run, not searching YouTube)")
        else:
            print("   -> no match found")

    step = make_youtube_step(youtube, progress.get("youtube_playlist_id"))
    stop = run_sync_loop(
        progress, progress_path, progress["tracks"], step,
        on_progress=on_progress, sleep_seconds=0.2, limit=args.limit,
    )

    if stop is not None:
        if stop.kind in (ErrorKind.QUOTA, ErrorKind.RATE_LIMIT):
            print("YouTube API quota exceeded. Saving progress and stopping.")
            print(f"Re-run tomorrow with: python sync.py to-youtube --resume {progress_path}")
            raise SystemExit(1)
        if stop.kind == ErrorKind.FATAL:
            print(f"Stopped on an error that won't resolve by waiting: {stop.message}")
            raise SystemExit(1)
        if stop.message.startswith("limit"):
            print(f"Reached --limit {args.limit} for this run. Re-run with --resume {progress_path} to continue.")

    done = len(progress["results"])
    total = len(progress["tracks"])
    matched = sum(1 for r in progress["results"] if r["video_id"])
    added = sum(1 for r in progress["results"] if r["added"])
    print(f"\nProcessed {done}/{total} tracks. Matched {matched}, added {added} to YouTube.")
    if done < total:
        print(f"Not finished yet. Re-run with: python sync.py to-youtube --resume {progress_path}")
    elif not args.dry_run:
        print(f"Done! Playlist: https://www.youtube.com/playlist?list={progress['youtube_playlist_id']}")


def get_youtube_client_verbose():
    print("Authenticating with YouTube...")
    return get_youtube_client()
