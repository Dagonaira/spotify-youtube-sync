"""
Build a 'Running Only' Spotify playlist from an existing playlist's tracks,
ordered by increasing tempo (BPM).

Spotify's own audio-features endpoint (which used to expose BPM) has been
restricted for most apps since Nov 2024, so tempo is looked up instead from
ReccoBeats (https://reccobeats.com), a free public API that mirrors Spotify's
track catalog by Spotify track ID and exposes the same audio-feature fields
(including tempo) that Spotify's API used to.
"""

import sys
import time
import urllib.request
import urllib.parse
import json

from common import get_spotify_client

SOURCE_PLAYLIST_ID = "6Z0eygnoYR2BdeuUVU6iyA"  # "Dancing/running" - only used the first time
NEW_PLAYLIST_NAME = "Running Only"
EXISTING_PLAYLIST_ID = "1dR8BTGYKfIHUG60oHl6cK"  # already created

# Once "Running Only" exists, it's the source of truth: re-sort whatever is
# currently in IT, not the original Dancing/running playlist. Rebuilding from
# the original source every time would silently undo any manual removals
# made directly on Running Only.
REORDER_EXISTING = True

RECCOBEATS_BASE = "https://api.reccobeats.com/v1"
CHUNK = 40

# ReccoBeats' tempo detection (like Spotify's before it) sometimes locks onto
# half or double the tempo a listener actually hears, and generic band-folding
# (normalized_tempo) can't tell which octave is correct on its own - it just
# picks whichever raw value happens to already land in-band. These are known,
# confirmed-by-ear corrections for specific tracks where that guess was wrong.
# Keyed by Spotify track ID; add more here as they're spotted.
TEMPO_OVERRIDES = {
    "1fLlRApgzxWweF1JTf8yM5": 199.0,  # Given Up - Linkin Park (reported 100.1, actually ~199)
}


def http_get_json(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; running-playlist-script/1.0)",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def normalized_tempo(bpm):
    """Fold a detected tempo into the 90-180 bpm band.

    Automated tempo detection commonly reports half or double the tempo a
    listener actually perceives - especially for phonk/dubstep/trap, where
    the detector can lock onto the wrong beat subdivision. Sorting on the
    raw value then produces a track list that "measures" ascending but
    doesn't "feel" ascending. Folding everything into one octave-equivalent
    band before sorting fixes that; DJ software does the same normalization
    for beatmatching across genres.
    """
    while bpm < 90:
        bpm *= 2
    while bpm >= 180:
        bpm /= 2
    return bpm


def fetch_playlist_tracks(sp, playlist_id):
    tracks = []
    results = sp.playlist_items(playlist_id)
    while results:
        for it in results["items"]:
            item = it.get("item") or it.get("track")
            if not item or item.get("type") != "track" or not item.get("id"):
                continue
            tracks.append({
                "id": item["id"],
                "name": item["name"],
                "artists": ", ".join(a["name"] for a in item["artists"]),
            })
        results = sp.next(results) if results.get("next") else None
    return tracks


def resolve_reccobeats_ids(spotify_ids):
    """spotify_id -> reccobeats_id, for whichever ones ReccoBeats has."""
    mapping = {}
    for chunk in chunked(spotify_ids, CHUNK):
        url = f"{RECCOBEATS_BASE}/track?ids={','.join(chunk)}"
        try:
            data = http_get_json(url)
        except Exception as e:
            print(f"  [warn] track lookup chunk failed: {e}", file=sys.stderr)
            continue
        for t in data.get("content", []):
            spotify_id = t["href"].rstrip("/").split("/")[-1]
            mapping[spotify_id] = t["id"]
        time.sleep(0.2)
    return mapping


def fetch_tempos(reccobeats_ids):
    """reccobeats_id -> tempo (float)"""
    tempos = {}
    ids = list(reccobeats_ids)
    for chunk in chunked(ids, CHUNK):
        url = f"{RECCOBEATS_BASE}/audio-features?ids={','.join(chunk)}"
        try:
            data = http_get_json(url)
        except Exception as e:
            print(f"  [warn] audio-features chunk failed: {e}", file=sys.stderr)
            continue
        for f in data.get("content", []):
            tempos[f["id"]] = f["tempo"]
        time.sleep(0.2)
    return tempos


def main():
    sp = get_spotify_client()

    source_id = EXISTING_PLAYLIST_ID if REORDER_EXISTING else SOURCE_PLAYLIST_ID
    print("Fetching source playlist tracks...")
    tracks = fetch_playlist_tracks(sp, source_id)
    print(f"  {len(tracks)} tracks found")

    spotify_ids = [t["id"] for t in tracks]
    print("Resolving tracks against ReccoBeats...")
    sp_to_rb = resolve_reccobeats_ids(spotify_ids)
    print(f"  {len(sp_to_rb)}/{len(spotify_ids)} matched")

    print("Fetching tempo (BPM) for matched tracks...")
    rb_to_tempo = fetch_tempos(sp_to_rb.values())
    print(f"  {len(rb_to_tempo)} tempos retrieved")

    with_tempo = []
    without_tempo = []
    for t in tracks:
        rb_id = sp_to_rb.get(t["id"])
        tempo = rb_to_tempo.get(rb_id) if rb_id else None
        if tempo:
            if t["id"] in TEMPO_OVERRIDES:
                # A confirmed-by-ear true tempo - use it as-is, skip the
                # automatic fold below (that's what let this one land wrong
                # in the first place: 199 bpm folds right back down to ~100,
                # since band-folding can't distinguish "genuinely slow" from
                # "genuinely fast but detected at half speed").
                final_tempo = TEMPO_OVERRIDES[t["id"]]
                norm_tempo = final_tempo
            else:
                final_tempo = tempo
                norm_tempo = normalized_tempo(tempo)
            with_tempo.append({**t, "tempo": final_tempo, "norm_tempo": norm_tempo})
        else:
            without_tempo.append(t)

    with_tempo.sort(key=lambda t: t["norm_tempo"])

    print(f"\n{len(with_tempo)} tracks ordered by tempo ({len(without_tempo)} dropped, no BPM data):")
    for t in with_tempo:
        print(f"  {t['norm_tempo']:6.1f} bpm (raw {t['tempo']:6.1f})  {t['name']} — {t['artists']}")
    if without_tempo:
        print("\nDropped (no BPM match found):")
        for t in without_tempo:
            print(f"  - {t['name']} — {t['artists']}")

    if "--dry-run" in sys.argv:
        print("\n[dry-run] Not touching the playlist.")
        return

    uris = [f"spotify:track:{t['id']}" for t in with_tempo]

    if EXISTING_PLAYLIST_ID:
        print(f"\nReplacing tracks in existing '{NEW_PLAYLIST_NAME}' playlist...")
        # playlist_replace_items both clears and sets the first 100 in one call.
        sp.playlist_replace_items(EXISTING_PLAYLIST_ID, uris[:100])
        for chunk in chunked(uris[100:], 100):
            sp.playlist_add_items(EXISTING_PLAYLIST_ID, chunk)
        url = f"https://open.spotify.com/playlist/{EXISTING_PLAYLIST_ID}"
    else:
        print(f"\nCreating playlist '{NEW_PLAYLIST_NAME}'...")
        # user_playlist_create hits the old per-user-id endpoint, which Spotify
        # migrated away from (Feb 2026) - it now 403s for Development Mode apps.
        # current_user_playlist_create uses POST /me/playlists instead.
        playlist = sp.current_user_playlist_create(NEW_PLAYLIST_NAME, public=False,
                                                    description="Same songs as Dancing/running, ordered by increasing tempo")
        for chunk in chunked(uris, 100):
            sp.playlist_add_items(playlist["id"], chunk)
        url = playlist["external_urls"]["spotify"]

    print(f"Done: {url}")


if __name__ == "__main__":
    main()
