#!/usr/bin/env python3
"""Crate Digger: retry the songs that have no cover art or 30 second clip, with typo-tolerant matching.

For every Spotify song in spotify.json with neither a clip nor art it asks Apple again:
  1. title + first artist, with the title cleaned up ("- Remix", "(feat. ...)", "Part two" = "Pt. 2")
  2. everything by that artist, fuzzy-matching the title (handles typos in the title)
  3. the title alone, fuzzy-matching the artist (handles typos in the artist)
Sure matches are saved. Unsure ones go in a review list. Artists with a known typo are corrected.

Run (through run-fixmissing.command, or):  python3 fix_missing.py        (all missing songs)
                                           python3 fix_missing.py 100    (just the next 100)
Apple allows about 20 lookups a minute, so the full run takes roughly an hour and a half.
Ctrl-C is safe. Answers are cached in ~/.crate-digger-fixes.json and it picks up where it left off.
When it finishes it writes crate-fixes.json to your Downloads folder.
"""
import difflib, json, os, re, sys, time, unicodedata
import urllib.error, urllib.parse, urllib.request

HOME = os.path.expanduser("~")
CACHE = os.path.join(HOME, ".crate-digger-fixes.json")
OUT = os.path.join(HOME, "Downloads", "crate-fixes.json")
SRC = "https://thisisntaron.github.io/crate-digger/spotify.json"
PAUSE = 3.2

# Artist spellings that are certainly wrong. Matched ignoring case.
ARTIST_FIXES = {
    "thid eye blind": "Third Eye Blind",
    "elvis depressdly": "Elvis Depressedly",
    "mischeif brew": "Mischief Brew",
}

NUMS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}


def fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("&", " and ")
    s = re.sub(r"\bpart\b", "pt", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(NUMS.get(w, w) for w in s.split())


def sim(a, b):
    a, b = fold(a), fold(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def clean_title(t):
    t = re.sub(r"\s*[\(\[][^\)\]]*(feat|with|remaster|live|demo|version|mix|edit|remix)[^\)\]]*[\)\]]", "", t, flags=re.I)
    return t.strip()


def strip_suffix(t):
    return re.sub(r"\s+-\s+.*$", "", clean_title(t)).strip()


def artist_parts(a):
    parts = [p.strip() for p in re.split(r",|&| feat\.? | x ", a) if p.strip()]
    return parts or [a]


def get_json(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crate-digger/1.0"})
            return json.load(urllib.request.urlopen(req, timeout=30))
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                time.sleep(20 * (attempt + 1))
            else:
                time.sleep(2)
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


class Throttled(Exception):
    pass


def search(term, limit):
    q = urllib.parse.urlencode({"term": term, "entity": "song", "limit": limit})
    data = get_json("https://itunes.apple.com/search?" + q)
    time.sleep(PAUSE)
    if data is None:
        raise Throttled()
    return data.get("results", [])


def score(song_title, song_artist, r):
    tt = max(sim(clean_title(song_title), clean_title(r.get("trackName", ""))),
             sim(strip_suffix(song_title), strip_suffix(r.get("trackName", ""))))
    mine = [song_artist] + artist_parts(song_artist)
    theirs = [r.get("artistName", "")] + artist_parts(r.get("artistName", ""))
    aa = max(sim(m, t) for m in mine for t in theirs)
    return tt, aa


def best_of(results, title, artist):
    best = None
    for r in results:
        if not r.get("trackName"):
            continue
        ta, aa = score(title, artist, r)
        key = (min(ta, aa), 1 if r.get("previewUrl") else 0)
        if best is None or key > best[0]:
            best = (key, ta, aa, r)
    return best


def verdict(ta, aa):
    if ta >= 0.92 and aa >= 0.85:
        return "ok"
    if ta >= 0.8 and aa >= 0.7:
        return "review"
    return None


def solve(song):
    title, artist = song["t"], song["a"]
    fixed_artist = ARTIST_FIXES.get(artist.strip().lower())
    q_artist = fixed_artist or artist
    first = artist_parts(q_artist)[0]
    tried = []
    for term, limit in ((clean_title(title) + " " + first, 10), (first, 50), (strip_suffix(title), 25)):
        if term in tried or len(term.strip()) < 3:
            continue
        tried.append(term)
        b = best_of(search(term, limit), title, q_artist)
        if not b:
            continue
        _, ta, aa, r = b
        v = verdict(ta, aa)
        if v:
            return build(song, v, ta, aa, r, fixed_artist)
    if fixed_artist:
        return {"status": "ok", "a": fixed_artist}
    return {"status": "none"}


def build(song, v, ta, aa, r, fixed_artist):
    out = {"status": v, "p": r.get("previewUrl", ""), "u": r.get("artworkUrl100", "").replace("100x100bb", "300x300bb"),
           "match": "%s - %s" % (r.get("artistName", ""), r.get("trackName", "")), "ta": round(ta, 2), "aa": round(aa, 2)}
    if fixed_artist:
        out["a"] = fixed_artist
    elif "," not in song["a"] and fold(song["a"]) != fold(r.get("artistName", "")) and aa >= 0.85:
        out["a"] = r.get("artistName", "")          # artist typo corrected to Apple's spelling
    name = r.get("trackName", "")
    if fold(strip_suffix(song["t"])) != fold(strip_suffix(name)) and ta >= 0.85 and " - " not in name and "(" not in name:
        out["t"] = name                               # title typo corrected
    return out


def write_out(songs, cache):
    ok = {i: {k: v for k, v in c.items() if k in ("p", "u", "a", "t")} for i, c in cache.items() if c["status"] == "ok"}
    review = {}
    for i, c in cache.items():
        if c["status"] == "review":
            s = songs[i]
            review[i] = {"was": "%s - %s" % (s["a"], s["t"]), "apple": c.get("match"), "ta": c["ta"], "aa": c["aa"],
                         "p": c.get("p", ""), "u": c.get("u", "")}
    renames = {i: {"was": "%s - %s" % (songs[i]["a"], songs[i]["t"]), **{k: c[k] for k in ("a", "t") if k in c}}
               for i, c in cache.items() if c["status"] == "ok" and ("a" in c or "t" in c)}
    none = [i for i, c in cache.items() if c["status"] == "none"]
    json.dump({"ok": ok, "review": review, "renames": renames, "none": none}, open(OUT, "w"), indent=1, ensure_ascii=False)
    return len(ok), len(review), len(renames), len(none)


def main(argv):
    limit = int(argv[1]) if len(argv) > 1 else 10 ** 9
    req = urllib.request.Request(SRC + "?cb=" + str(int(time.time())), headers={"User-Agent": "crate-digger/1.0"})
    sp = json.load(urllib.request.urlopen(req, timeout=60))
    songs = {s["i"]: s for s in sp}
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    todo = [s for s in sp if not s.get("p") and not s.get("u") and s["i"] not in cache][:limit]
    print("%d songs with no clip or art, %d already retried, doing %d" % (
        sum(1 for s in sp if not s.get("p") and not s.get("u")), len(cache), len(todo)), flush=True)
    fails = 0
    for n, s in enumerate(todo, 1):
        try:
            cache[s["i"]] = solve(s)
            fails = 0
        except Throttled:
            fails += 1
            print("  Apple did not answer (%d in a row)" % fails, flush=True)
            if fails >= 5:
                print("Apple is refusing requests right now. Progress is saved. Run again in a little while.")
                break
            time.sleep(30 * fails)
            continue
        except KeyboardInterrupt:
            break
        if n % 25 == 0:
            json.dump(cache, open(CACHE, "w"))
            ok, rv, rn, nn = write_out(songs, cache)
            print("  %d done: %d matched, %d to review, %d name fixes, %d no match" % (n, ok, rv, rn, nn), flush=True)
    json.dump(cache, open(CACHE, "w"))
    ok, rv, rn, nn = write_out(songs, cache)
    print("Finished: %d matched, %d to review, %d name fixes, %d no match. Wrote %s" % (ok, rv, rn, nn, OUT))


if __name__ == "__main__":
    main(sys.argv)
