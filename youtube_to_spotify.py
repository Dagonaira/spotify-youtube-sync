"""Copy a YouTube playlist into a new Spotify playlist."""

import re
import time

from googleapiclient.errors import HttpError
from spotipy.exceptions import SpotifyException

from common import get_spotify_client, get_youtube_client, load_progress, save_progress

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
    user_id = sp.current_user()["id"]
    playlist = sp.user_playlist_create(user_id, name, public=False, description=description)
    return playlist["id"]


def add_track_to_playlist(sp, playlist_id: str, track_uri: str):
    sp.playlist_add_items(playlist_id, [track_uri])


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

    videos = progress["tracks"]
    start_index = len(progress["results"])
    processed_this_run = 0

    for i in range(start_index, len(videos)):
        if args.limit is not None and processed_this_run >= args.limit:
            print(f"Reached --limit {args.limit} for this run. Re-run with --resume {progress_path} to continue.")
            break

        video = videos[i]
        title, artist_hint = clean_title_and_artist(video["title"], video.get("channel", ""))
        print(f"[{i + 1}/{len(videos)}] Searching: {title}" + (f" (artist: {artist_hint})" if artist_hint else ""))

        try:
            track_uri = search_spotify_track(sp, title, artist_hint) if sp is not None else None
            if sp is None:
                print("   (dry-run, not searching Spotify)")
        except SpotifyException as e:
            if e.http_status == 429:
                _stop_for_rate_limit(progress_path, progress)
            raise

        added = False
        if track_uri and sp is not None:
            try:
                add_track_to_playlist(sp, progress["spotify_playlist_id"], track_uri)
                added = True
                print(f"   -> added {track_uri}")
            except SpotifyException as e:
                if e.http_status == 429:
                    progress["results"].append(
                        {"title": video["title"], "track_uri": None, "added": False}
                    )
                    _stop_for_rate_limit(progress_path, progress)
                raise
        elif not track_uri:
            print("   -> no match found")

        progress["results"].append({"title": video["title"], "track_uri": track_uri, "added": added})
        save_progress(progress_path, progress)
        processed_this_run += 1
        time.sleep(0.1)

    done = len(progress["results"])
    matched = sum(1 for r in progress["results"] if r["track_uri"])
    added = sum(1 for r in progress["results"] if r["added"])
    print(f"\nProcessed {done}/{len(videos)} videos. Matched {matched}, added {added} to Spotify.")
    if done < len(videos):
        print(f"Not finished yet. Re-run with: python sync.py to-spotify --resume {progress_path}")
    elif not args.dry_run:
        print(f"Done! Playlist: https://open.spotify.com/playlist/{progress['spotify_playlist_id']}")


def get_spotify_client_verbose():
    print("Authenticating with Spotify...")
    return get_spotify_client()


def _stop_for_rate_limit(progress_path, progress):
    print("Spotify API rate limit hit. Saving progress and stopping.")
    save_progress(progress_path, progress)
    print(f"Re-run shortly with: python sync.py to-spotify --resume {progress_path}")
    raise SystemExit(1)
