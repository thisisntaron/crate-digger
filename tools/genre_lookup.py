#!/usr/bin/env python3
"""Crate Digger genre lookup.

Reads the "Song list" CSV you download from your Google Sheet, asks Last.fm for
tags (once per artist, then per song only when the artist gives no answer),
maps those tags onto Crate Digger's genres, and writes song-genres.csv.

Setup, once:
  1. Copy your Last.fm API key, then run:  pbpaste > ~/.lastfm_key
  2. In Google Sheets, on the "Song list" tab: File > Download > CSV.

Run:
  cd ~/Downloads
  python3 genre_lookup.py                 (uses the newest "Song list" CSV in Downloads)
  python3 genre_lookup.py path/to/file.csv

Answers are cached in ~/.crate-digger-lastfm.json, so later runs only ask about
new artists. Works with the python3 that ships with macOS (3.9).
"""
import csv
import glob
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

HOME = os.path.expanduser("~")
KEY_FILE = os.path.join(HOME, ".lastfm_key")
CACHE_FILE = os.path.join(HOME, ".crate-digger-lastfm.json")
DOWNLOADS = os.path.join(HOME, "Downloads")
OUT_FILE = os.path.join(DOWNLOADS, "song-genres.csv")
API = "https://ws.audioscrobbler.com/2.0/"
PAUSE = 0.25          # seconds between Last.fm calls, to stay polite
MIN_SCORE = 25        # a genre needs at least this much tag weight to count

# Tag to genre rules. Order matters: the first rule that matches a tag wins,
# so specific genres come before broad ones. Edit freely, then rerun: the
# cache means a rerun costs nothing.
RULES = [
    ("Hip hop", ["hip hop", "hip-hop", "hiphop", "rap", "trap", "grime", "boom bap", "drill"]),
    ("Synthpop", ["synthpop", "synth pop", "synth-pop", "synthwave", "new wave", "electropop", "electro pop"]),
    ("Electronic", ["electronic", "electronica", "electro", "edm", "house", "techno", "trance", "dubstep",
                    "drum and bass", "dnb", "idm", "breakbeat", "downtempo", "trip hop", "trip-hop",
                    "chillwave", "vaporwave", "future bass"]),
    ("Ambient", ["ambient", "drone", "new age", "meditation", "soundscape"]),
    ("Post-rock", ["post-rock", "post rock", "math rock", "math-rock", "instrumental rock"]),
    ("Soul", ["soul", "r&b", "rnb", "rhythm and blues", "funk", "motown", "neo soul", "neo-soul", "gospel"]),
    ("Americana", ["americana", "country", "bluegrass", "alt-country", "alt country", "southern rock",
                   "honky tonk", "outlaw"]),
    ("Folk", ["folk", "singer-songwriter", "singer songwriter", "acoustic"]),
    ("Latin pop", ["latin", "reggaeton", "salsa", "bachata", "cumbia", "latino", "spanish", "mariachi"]),
    ("Pop punk", ["pop punk", "pop-punk", "punk", "emo", "hardcore", "ska", "skate punk", "post-hardcore",
                  "screamo", "midwest emo", "easycore"]),
    ("Indie", ["indie", "alternative", "alt-rock", "alt rock", "lo-fi", "lofi", "bedroom pop", "dream pop",
               "shoegaze", "garage rock", "surf rock", "psychedelic", "art pop", "chamber pop",
               "jangle pop", "slacker rock"]),
    ("Rock", ["rock", "metal", "grunge", "hard rock", "classic rock", "blues rock", "stoner", "blues"]),
    ("Pop", ["pop", "k-pop", "j-pop", "dance pop", "teen pop", "power pop", "adult contemporary"]),
]
_COMPILED = [
    (genre, [re.compile(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])") for w in words])
    for genre, words in RULES
]


def clean_artist(name):
    """YouTube Music auto channels end in ' - Topic'. Drop that."""
    return re.sub(r"\s*-\s*Topic\s*$", "", name or "", flags=re.I).strip()


def video_id(link):
    m = re.search(r"(?:embed/|v=|youtu\.be/)([A-Za-z0-9_-]{11})", link or "")
    return m.group(1) if m else ""


def genre_from_tags(tags):
    scores = Counter()
    for name, count in tags[:12]:
        n = name.lower()
        for genre, patterns in _COMPILED:
            if any(p.search(n) for p in patterns):
                scores[genre] += int(count)
                break
    if not scores:
        return "Other"
    genre, total = scores.most_common(1)[0]
    return genre if total >= MIN_SCORE else "Other"


def load_key():
    key = os.environ.get("LASTFM_API_KEY", "").strip()
    if not key and os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            key = f.read().strip()
    if not key:
        raise SystemExit(
            "No Last.fm API key found.\n"
            "Copy the key from the Last.fm page, then run:  pbpaste > ~/.lastfm_key"
        )
    if not re.fullmatch(r"[0-9a-fA-F]{32}", key):
        print("Note: that does not look like a 32 character Last.fm key. Trying anyway.")
    return key


def find_csv(argv):
    if len(argv) > 1:
        path = os.path.expanduser(argv[1])
        if not os.path.exists(path):
            raise SystemExit("Cannot find " + path)
        return path
    matches = glob.glob(os.path.join(DOWNLOADS, "*Song list*.csv"))
    if not matches:
        raise SystemExit(
            "No Song list CSV found in Downloads.\n"
            "In Google Sheets, open the Song list tab, then File > Download > Comma Separated Values."
        )
    return max(matches, key=os.path.getmtime)


def read_songs(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise SystemExit("That CSV is empty.")
    header = [h.strip().lower() for h in rows[0]]

    def col(name, fallback):
        return header.index(name) if name in header else fallback

    i_song, i_artist, i_link = col("song", 1), col("artist", 2), col("link", 4)
    songs, seen = [], set()
    for r in rows[1:]:
        def cell(i):
            return r[i].strip() if i < len(r) else ""
        vid = video_id(cell(i_link))
        if not vid or vid in seen:
            continue
        seen.add(vid)
        songs.append({"vid": vid, "song": cell(i_song), "artist": clean_artist(cell(i_artist))})
    return songs


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        data.setdefault("artist", {})
        data.setdefault("track", {})
        return data
    except (OSError, ValueError):
        return {"artist": {}, "track": {}}


def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)


def api_get(key, params):
    """One Last.fm call. Returns the decoded JSON, or None if it kept failing."""
    q = dict(params)
    q.update({"api_key": key, "format": "json", "autocorrect": "1"})
    url = API + "?" + urllib.parse.urlencode(q)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crate-digger/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            time.sleep(PAUSE)
            if "error" in data:
                code = data.get("error")
                if code == 10:
                    raise SystemExit("Last.fm rejected the API key. Check the key in ~/.lastfm_key")
                if code in (8, 11, 16, 29):      # temporary trouble or rate limit
                    time.sleep(2 * (attempt + 1))
                    continue
                return {"toptags": {"tag": []}}  # for example 6: artist or track not found
            return data
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLError):
                raise SystemExit("Secure connection to Last.fm failed: " + str(e.reason))
            time.sleep(2 * (attempt + 1))
        except (TimeoutError, ValueError, OSError):
            time.sleep(2 * (attempt + 1))
    return None


def parse_tags(data):
    if not data:
        return None
    tag = (data.get("toptags") or {}).get("tag") or []
    if isinstance(tag, dict):
        tag = [tag]
    return [[t.get("name", ""), int(t.get("count", 0) or 0)] for t in tag if t.get("name")]


def artist_tags(key, cache, artist):
    k = artist.lower()
    if k not in cache["artist"]:
        tags = parse_tags(api_get(key, {"method": "artist.gettoptags", "artist": artist}))
        if tags is None:
            return None
        cache["artist"][k] = tags
    return cache["artist"][k]


def track_tags(key, cache, artist, song):
    k = artist.lower() + "|||" + song.lower()
    if k not in cache["track"]:
        tags = parse_tags(api_get(key, {"method": "track.gettoptags", "artist": artist, "track": song}))
        if tags is None:
            return None
        cache["track"][k] = tags
    return cache["track"][k]


def main(argv):
    key = load_key()
    path = find_csv(argv)
    songs = read_songs(path)
    print("Reading " + os.path.basename(path))
    print("{} songs, {} artists\n".format(len(songs), len({s["artist"].lower() for s in songs})))

    cache = load_cache()
    results, failed = [], 0
    unmatched = Counter()
    last_artist = None
    for n, s in enumerate(songs, 1):
        artist, song = s["artist"], s["song"]
        tags_a = artist_tags(key, cache, artist) if artist else []
        if tags_a is None:
            failed += 1
            tags_a = []
        genre = genre_from_tags(tags_a)
        tags_used = tags_a
        if genre == "Other" and artist and song:
            tags_t = track_tags(key, cache, artist, song)
            if tags_t is None:
                failed += 1
            elif genre_from_tags(tags_t) != "Other":
                genre, tags_used = genre_from_tags(tags_t), tags_t
        if genre == "Other":
            for name, _ in tags_used[:6]:
                unmatched[name.lower()] += 1
        if artist != last_artist:
            print("[{}/{}] {} -> {}".format(n, len(songs), artist or "(no artist)", genre))
            last_artist = artist
            if n % 25 == 0:
                save_cache(cache)
        results.append([s["vid"], song, artist, genre, "; ".join(t[0] for t in tags_used[:3])])
    save_cache(cache)

    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["video_id", "song", "artist", "genre", "tags"])
        w.writerows(results)

    print("\nGenre counts")
    for g, c in Counter(r[3] for r in results).most_common():
        print("  {:<11} {}".format(g, c))
    if unmatched:
        print("\nTags on songs left as Other (most common first). These help tune the rules:")
        print("  " + ", ".join("{} ({})".format(t, c) for t, c in unmatched.most_common(20)))
    if failed:
        print("\n{} lookups failed (network or rate limit). Run the script again to retry them.".format(failed))
    print("\nWrote " + OUT_FILE)
    print("Upload that file to the Claude chat.")


if __name__ == "__main__":
    main(sys.argv)
