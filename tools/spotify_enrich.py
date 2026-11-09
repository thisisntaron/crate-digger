#!/usr/bin/env python3
"""Crate Digger: genre, album art and 30 second clip for your Spotify songs.

Works through spotify.json in the same order the app unlocks packs, so the songs
you will see first get done first. For each song it asks Last.fm for the
artist's tags (genre) and Apple for the clip and cover art.

Setup: the same Last.fm key file as before (~/.lastfm_key). Nothing else.

Run:
  cd ~/Downloads
  python3 spotify_enrich.py 400      (do the next 400 songs you have not done yet)
  python3 spotify_enrich.py          (default 300)

Apple allows about 20 lookups a minute, so 300 songs take roughly 16 minutes.
Ctrl-C is safe; run it again and it continues. Answers are cached in
~/.crate-digger-spotify.json. When it finishes it writes crate-spotify.json to
Downloads. Upload that file to the Claude chat each time.
"""
import json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request
from collections import Counter

HOME = os.path.expanduser("~")
KEY_FILE = os.path.join(HOME, ".lastfm_key")
CACHE = os.path.join(HOME, ".crate-digger-spotify.json")
OUT = os.path.join(HOME, "Downloads", "crate-spotify.json")
SRC = "https://thisisntaron.github.io/crate-digger/spotify.json"
PAUSE_APPLE = 3.2
PAUSE_LASTFM = 0.25

# Last.fm tag -> Crate Digger genre. First rule that matches a tag wins.
RULES = [
    ("Punk & hardcore", ["hardcore", "punk", "screamo", "oi", "crust", "post-hardcore", "skate punk"]),
    ("Pop punk", ["pop punk", "pop-punk", "emo", "midwest emo", "easycore", "ska punk", "ska", "emo pop"]),
    ("Hip hop", ["hip hop", "hip-hop", "hiphop", "rap", "trap", "grime", "boom bap", "drill", "lo-fi hip hop"]),
    ("Synthpop", ["synthpop", "synth pop", "synth-pop", "synthwave", "new wave", "electropop", "electro pop", "new romantic"]),
    ("Electronic", ["electronic", "electronica", "electro", "edm", "house", "techno", "trance", "dubstep",
                    "drum and bass", "dnb", "idm", "breakbeat", "downtempo", "trip hop", "trip-hop",
                    "chillwave", "vaporwave", "future bass", "dance"]),
    ("Ambient", ["ambient", "drone", "new age", "meditation", "soundscape"]),
    ("Post-rock", ["post-rock", "post rock", "math rock", "math-rock", "instrumental rock", "midwest emo instrumental"]),
    ("Soul", ["soul", "r&b", "rnb", "rhythm and blues", "funk", "motown", "neo soul", "neo-soul", "gospel", "disco", "jazz"]),
    ("Americana", ["americana", "country", "bluegrass", "alt-country", "alt country", "southern rock", "honky tonk", "outlaw"]),
    ("Folk", ["folk", "singer-songwriter", "singer songwriter", "acoustic", "indie folk"]),
    ("Latin pop", ["latin", "reggaeton", "salsa", "bachata", "cumbia", "latino", "spanish", "mariachi", "rock en espanol"]),
    ("Alt & post-punk", ["post-punk", "post punk", "darkwave", "goth", "gothic rock", "cold wave", "coldwave", "noise rock", "no wave", "art punk"]),
    ("Psych & garage", ["psychedelic", "psych", "garage rock", "garage", "surf rock", "surf", "stoner rock", "neo-psychedelia", "krautrock", "acid rock"]),
    ("Dream & lo-fi", ["dream pop", "dreampop", "shoegaze", "lo-fi", "lofi", "bedroom pop", "chillout", "slowcore", "hypnagogic pop", "bedroom"]),
    ("Indie pop", ["indie pop", "indie-pop", "jangle pop", "chamber pop", "twee", "art pop", "baroque pop", "synth-pop indie", "dance-punk"]),
    ("Indie rock", ["indie rock", "indie-rock", "alternative rock", "alt-rock", "alt rock", "indie", "alternative", "britpop", "slacker rock", "college rock"]),
    ("Rock", ["rock", "metal", "grunge", "hard rock", "classic rock", "blues rock", "blues", "heavy metal", "progressive rock", "prog"]),
    ("Pop", ["pop", "k-pop", "j-pop", "dance pop", "teen pop", "power pop", "adult contemporary"]),
]
_C = [(g, [re.compile(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])") for w in ws]) for g, ws in RULES]
MIN_SCORE = 25


def genre_from_tags(tags):
    sc = Counter()
    for name, count in tags[:12]:
        n = name.lower()
        for g, ps in _C:
            if any(p.search(n) for p in ps):
                sc[g] += int(count)
                break
    if not sc:
        return ""
    g, total = sc.most_common(1)[0]
    return g if total >= MIN_SCORE else ""


def get_json(url, tries=3):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crate-digger/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(4 * (a + 1))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            time.sleep(2 * (a + 1))
    return None


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match(wa, wt, ga, gt):
    wa, wt, ga, gt = norm(wa), norm(wt), norm(ga), norm(gt)
    if not (wa and wt and ga and gt):
        return False
    return (wa in ga or ga in wa) and (wt in gt or gt in wt)


def clean_title(t):
    t = re.sub(r"\s*[\(\[][^\)\]]*(feat|with|remaster|live|demo|version|mix|edit)[^\)\]]*[\)\]]", "", t, flags=re.I)
    t = re.sub(r"\s+-\s+(remaster|live|single|album|radio|sped|slowed|acoustic).*$", "", t, flags=re.I)
    return t.strip()


def apple(artist, title):
    first = re.split(r",|&| feat\.? ", artist)[0].strip()
    q = urllib.parse.urlencode({"term": clean_title(title) + " " + first, "entity": "song", "limit": 8})
    data = get_json("https://itunes.apple.com/search?" + q, 1)
    time.sleep(PAUSE_APPLE)
    if data is None:
        return None  # Apple refused or timed out: not a real miss, so do not remember it
    best = None
    for r in (data or {}).get("results", []):
        if match(first, clean_title(title), r.get("artistName"), r.get("trackName")):
            if best is None:
                best = r
            if r.get("previewUrl"):
                best = r
                break
    if not best:
        return "", ""
    art = best.get("artworkUrl100", "").replace("100x100bb", "300x300bb")
    return best.get("previewUrl", ""), art


def load_key():
    key = os.environ.get("LASTFM_API_KEY", "").strip()
    if not key and os.path.exists(KEY_FILE):
        key = open(KEY_FILE).read().strip()
    if not key:
        raise SystemExit("No Last.fm key. Run:  pbpaste > ~/.lastfm_key")
    return key


def lastfm_tags(key, artist):
    q = urllib.parse.urlencode({"method": "artist.gettoptags", "artist": artist, "api_key": key,
                                "format": "json", "autocorrect": "1"})
    d = get_json("https://ws.audioscrobbler.com/2.0/?" + q)
    time.sleep(PAUSE_LASTFM)
    if d is None:
        return None
    tag = (d.get("toptags") or {}).get("tag") or []
    if isinstance(tag, dict):
        tag = [tag]
    return [[t.get("name", ""), int(t.get("count", 0) or 0)] for t in tag if t.get("name")]


def main(argv):
    n_do = int(argv[1]) if len(argv) > 1 else 300
    key = load_key()
    songs = None
    for a in range(3):
        songs = get_json(SRC, 1)
        if songs:
            break
    if not songs:
        raise SystemExit("Could not download " + SRC)
    try:
        cache = json.load(open(CACHE))
    except (OSError, ValueError):
        cache = {"songs": {}, "artists": {}}
    todo = [s for s in songs if s["i"] not in cache["songs"]][:n_do]
    done_before = len(cache["songs"])
    print("{} of {} songs done already. Doing the next {}.\n".format(done_before, len(songs), len(todo)))
    fails = 0
    try:
        for n, s in enumerate(todo, 1):
            ak = s["a"].lower()
            first = re.split(r",", s["a"])[0].strip()
            if ak not in cache["artists"]:
                tags = lastfm_tags(key, first)
                cache["artists"][ak] = tags if tags is not None else []
            g = genre_from_tags(cache["artists"][ak])
            res = apple(s["a"], s["t"])
            if res is None:
                fails += 1
                print("  Apple did not answer for {} - {} (will retry next run)".format(s["a"], s["t"]), flush=True)
                if fails >= 5:
                    print("\nApple is limiting requests right now. Stopping. Wait 30 minutes, then run again.", flush=True)
                    break
                time.sleep(20)
                continue
            fails = 0
            p, u = res
            cache["songs"][s["i"]] = {"g": g, "p": p, "u": u}
            if n % 10 == 0:
                json.dump(cache, open(CACHE, "w"))
                print("[{}/{}] {} - {}  ->  {}{}".format(n, len(todo), s["a"], s["t"], g or "no genre", "" if p else " (no clip)"))
    except KeyboardInterrupt:
        print("\nStopped. Progress saved; run again to continue.")
    json.dump(cache, open(CACHE, "w"))
    out = {i: v for i, v in cache["songs"].items()}
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    clips = sum(1 for v in out.values() if v["p"])
    gens = sum(1 for v in out.values() if v["g"])
    print("\n{} songs done in total: {} with clips, {} with genres.".format(len(out), clips, gens))
    print("Wrote " + OUT)
    print("Upload that file to the Claude chat.")


if __name__ == "__main__":
    main(sys.argv)
