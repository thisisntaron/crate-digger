#!/usr/bin/env python3
"""Crate Digger: find the exact YouTube Music video for each Spotify song.

Why: exporting by title and artist text can pick a different band's song with a
similar title. A video id is exact, so the app can build the playlist from ids.

Run (Mac Terminal, or through the launcher):
  python3 yt_match.py            next 400 songs that are not matched yet
  python3 yt_match.py 1000       next 1000
  python3 yt_match.py all        everything left
  python3 yt_match.py decks      only songs that are in your decks (uses the newest
                                 crate-digger-backup*.json in Downloads, so make a fresh
                                 backup in the app first: More, Back up)

First run makes a small private Python environment in ~/.crate-digger-tools/venv
and installs ytmusicapi there (no key, no login). Ctrl-C is safe: answers are
cached in ~/.crate-digger-ytmap.json and the next run continues.
Writes ~/Downloads/crate-ytmap.json  ({spotifyId: [videoId, score]}).
Songs where the artist or title does not clearly match are left out on purpose.
"""
import json, os, re, subprocess, sys, time, urllib.request, difflib

HOME = os.path.expanduser("~")
TOOLS = os.path.join(HOME, ".crate-digger-tools")
VENV = os.path.join(TOOLS, "venv")
CACHE = os.path.join(HOME, ".crate-digger-ytmap.json")
OUT = os.path.join(HOME, "Downloads", "crate-ytmap.json")
SRC = "https://thisisntaron.github.io/crate-digger/spotify.json"
PAUSE = 0.6


def boot():
    py = os.path.join(VENV, "bin", "python3")
    if os.path.abspath(sys.executable) == os.path.abspath(py):
        return
    if not os.path.exists(py):
        print("Setting up ytmusicapi (one time)...", flush=True)
        os.makedirs(TOOLS, exist_ok=True)
        subprocess.check_call([sys.executable, "-m", "venv", VENV])
        subprocess.check_call([py, "-m", "pip", "install", "-q", "ytmusicapi"])
    os.execv(py, [py] + sys.argv)


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", " ", s)       # (feat. x) [remaster]
    s = re.sub(r"\s+-\s+(remaster|live|single|radio|mono|stereo|demo|\d{4}).*$", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def parts(a):
    return [norm(x) for x in re.split(r"\s*(?:,|&|\+|\bfeat\.?|\bft\.?|\bwith\b|\bx\b)\s*", a or "") if norm(x)]


def score(want_t, want_a, hit):
    t_ok = difflib.SequenceMatcher(None, norm(want_t), norm(hit.get("title"))).ratio()
    names = [norm(x.get("name")) for x in (hit.get("artists") or [])]
    ws = parts(want_a)
    a_ok = 1.0 if any(w and any(w == n or w in n or n in w for n in names if n) for w in ws) else 0.0
    return t_ok, a_ok


def main():
    boot()
    from ytmusicapi import YTMusic
    arg = sys.argv[1] if len(sys.argv) > 1 else "400"
    limit = 10 ** 9 if arg in ("all", "decks") else int(arg)
    want = None
    if arg == "decks":
        import glob
        files = sorted(glob.glob(os.path.join(HOME, "Downloads", "crate-digger-backup*.json")), key=os.path.getmtime)
        if not files:
            sys.exit("No crate-digger-backup*.json in Downloads. Make a backup in the app first.")
        st = json.load(open(files[-1]))["state"]
        want = {i for d in st.get("decks", []) for i in d.get("ids", [])}
        print("Using %s: %d songs in decks" % (os.path.basename(files[-1]), len(want)), flush=True)
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    req = urllib.request.Request(SRC + "?cb=" + str(int(time.time())), headers={"User-Agent": "crate-digger/1.0"})
    songs = [s for s in json.load(urllib.request.urlopen(req, timeout=60)) if s.get("d")]
    if want is not None:
        songs = [s for s in songs if s["i"] in want]
    todo = [s for s in songs if s["i"] not in cache]
    print("%d Spotify songs, %d already checked, doing the next %d" % (len(songs), len(songs) - len(todo), min(limit, len(todo))), flush=True)
    yt = YTMusic()
    done = fails = 0
    for s in todo[:limit]:
        try:
            hits = yt.search("%s %s" % (s["a"], s["t"]), filter="songs", limit=6)
        except Exception as e:
            fails += 1
            print("  search error:", str(e)[:80], flush=True)
            if fails >= 6:
                print("Too many errors in a row, stopping. Run again later.")
                break
            time.sleep(8)
            continue
        fails = 0
        best = None
        for h in hits or []:
            if not h.get("videoId"):
                continue
            t_ok, a_ok = score(s["t"], s["a"], h)
            sc = round(a_ok * 0.5 + t_ok * 0.5, 3)
            if a_ok and t_ok >= 0.8 and (not best or sc > best[1]):
                best = [h["videoId"], sc]
        cache[s["i"]] = best or 0
        done += 1
        if done % 25 == 0:
            json.dump(cache, open(CACHE, "w"))
            ok = sum(1 for v in cache.values() if v)
            print("  %d done this run, %d matched of %d checked overall" % (done, ok, len(cache)), flush=True)
        time.sleep(PAUSE)
    json.dump(cache, open(CACHE, "w"))
    out = {k: v for k, v in cache.items() if v}
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print("Matched %d of %d checked so far (%d left). Wrote %s" % (len(out), len(cache), len(songs) - len(cache), OUT))


if __name__ == "__main__":
    main()
