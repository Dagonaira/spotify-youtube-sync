"""
Copy playlists between Spotify and YouTube, in either direction.

Examples:
    python sync.py to-youtube --spotify-playlist <url> --name "My YouTube Playlist"
    python sync.py to-youtube --liked-songs --name "My Liked Songs"
    python sync.py to-spotify --youtube-playlist <url> --name "My Spotify Playlist"
    python sync.py to-youtube --spotify-playlist <url> --name "..." --dry-run
    python sync.py to-spotify --resume progress_tospotify_My_Playlist.json

Progress is saved incrementally to a JSON file, so if a run is interrupted
(API quota, network error, etc.) you can pick up where it left off with
--resume instead of starting over.
"""

import argparse

import spotify_to_youtube
import youtube_to_spotify


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="direction", required=True)

    to_yt = sub.add_parser("to-youtube", help="Copy a Spotify playlist (or Liked Songs) to a new YouTube playlist")
    to_yt.add_argument("--spotify-playlist", help="Spotify playlist URL or ID")
    to_yt.add_argument("--liked-songs", action="store_true", help="Use your Spotify Liked Songs instead of a playlist")
    to_yt.add_argument("--name", help="Name for the new YouTube playlist")
    to_yt.add_argument("--dry-run", action="store_true", help="Only print matches, don't touch YouTube")
    to_yt.add_argument("--resume", help="Path to a progress JSON file to resume from")
    to_yt.add_argument("--limit", type=int, default=None, help="Max tracks to process this run")
    to_yt.set_defaults(func=spotify_to_youtube.run)

    to_sp = sub.add_parser("to-spotify", help="Copy a YouTube playlist to a new Spotify playlist")
    to_sp.add_argument("--youtube-playlist", help="YouTube playlist URL or ID")
    to_sp.add_argument("--name", help="Name for the new Spotify playlist")
    to_sp.add_argument("--dry-run", action="store_true", help="Only print matches, don't touch Spotify")
    to_sp.add_argument("--resume", help="Path to a progress JSON file to resume from")
    to_sp.add_argument("--limit", type=int, default=None, help="Max videos to process this run")
    to_sp.set_defaults(func=youtube_to_spotify.run)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
