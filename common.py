"""Shared auth clients and progress-file helpers for the sync scripts."""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")

SPOTIFY_SCOPE = (
    "playlist-read-private playlist-read-collaborative user-library-read "
    "playlist-modify-private playlist-modify-public"
)
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]
YOUTUBE_CLIENT_SECRETS_FILE = SCRIPT_DIR / "client_secret.json"
YOUTUBE_TOKEN_FILE = SCRIPT_DIR / "youtube_token.json"
SPOTIFY_CACHE_FILE = SCRIPT_DIR / ".spotify_cache"


def get_spotify_client() -> spotipy.Spotify:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    if not client_id or not client_secret:
        sys.exit(
            "Missing SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET.\n"
            "Copy .env.example to .env and fill in your Spotify app credentials."
        )
    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SPOTIFY_SCOPE,
        cache_path=str(SPOTIFY_CACHE_FILE),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth_manager)


def get_youtube_client():
    creds = None
    if YOUTUBE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(YOUTUBE_TOKEN_FILE), YOUTUBE_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not YOUTUBE_CLIENT_SECRETS_FILE.exists():
                sys.exit(
                    f"Missing {YOUTUBE_CLIENT_SECRETS_FILE.name}.\n"
                    "Download your OAuth client secret JSON from Google Cloud Console "
                    "and save it next to this script as client_secret.json."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(YOUTUBE_CLIENT_SECRETS_FILE), YOUTUBE_SCOPES
            )
            creds = flow.run_local_server(port=0)
        YOUTUBE_TOKEN_FILE.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def load_progress(path):
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_progress(path, progress: dict):
    Path(path).write_text(json.dumps(progress, indent=2, ensure_ascii=False), encoding="utf-8")
