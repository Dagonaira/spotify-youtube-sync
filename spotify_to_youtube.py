"""Copy a Spotify playlist (or Liked Songs) into a new YouTube playlist."""

import re
import time

from googleapiclient.errors import HttpError

from common import get_spotify_client, get_youtube_client, load_progress, save_progress


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

    tracks = progress["tracks"]
    start_index = len(progress["results"])
    processed_this_run = 0

    for i in range(start_index, len(tracks)):
        if args.limit is not None and processed_this_run >= args.limit:
            print(f"Reached --limit {args.limit} for this run. Re-run with --resume {progress_path} to continue.")
            break

        track = tracks[i]
        query = f"{track['name']} {track['artists']}"
        print(f"[{i + 1}/{len(tracks)}] Searching: {query}")

        try:
            video_id = search_youtube_video(youtube, query) if youtube is not None else None
            if youtube is None:
                print("   (dry-run, not searching YouTube)")
        except HttpError as e:
            if e.resp.status in (403, 429):
                _stop_for_quota(progress_path, progress)
            raise

        added = False
        if video_id and youtube is not None:
            try:
                add_video_to_playlist(youtube, progress["youtube_playlist_id"], video_id)
                added = True
                print(f"   -> added https://youtu.be/{video_id}")
            except HttpError as e:
                if e.resp.status in (403, 429):
                    progress["results"].append(
                        {"name": track["name"], "artists": track["artists"], "video_id": None, "added": False}
                    )
                    _stop_for_quota(progress_path, progress)
                raise
        elif not video_id:
            print("   -> no match found")

        progress["results"].append(
            {"name": track["name"], "artists": track["artists"], "video_id": video_id, "added": added}
        )
        save_progress(progress_path, progress)
        processed_this_run += 1
        time.sleep(0.2)

    done = len(progress["results"])
    matched = sum(1 for r in progress["results"] if r["video_id"])
    added = sum(1 for r in progress["results"] if r["added"])
    print(f"\nProcessed {done}/{len(tracks)} tracks. Matched {matched}, added {added} to YouTube.")
    if done < len(tracks):
        print(f"Not finished yet. Re-run with: python sync.py to-youtube --resume {progress_path}")
    elif not args.dry_run:
        print(f"Done! Playlist: https://www.youtube.com/playlist?list={progress['youtube_playlist_id']}")


def get_youtube_client_verbose():
    print("Authenticating with YouTube...")
    return get_youtube_client()


def _stop_for_quota(progress_path, progress):
    print("YouTube API quota exceeded. Saving progress and stopping.")
    save_progress(progress_path, progress)
    print(f"Re-run tomorrow with: python sync.py to-youtube --resume {progress_path}")
    raise SystemExit(1)
