#!/usr/bin/env python3
"""Crate Digger preview clips (macOS or anything with python3).

Finds Apple's free 30 second preview clip for each song in your "Song list"
CSV and writes crate-previews.json into Downloads. No keys, nothing to install.

Run:
  cd ~/Downloads
  python3 previews_lookup.py            (newest "Song list" CSV in Downloads)
  python3 previews_lookup.py path/to/file.csv

First run takes about 13 minutes (Apple allows roughly 20 lookups a minute).
Ctrl-C is safe; run it again and it continues. Answers are cached in
~/.crate-digger-previews.json.
"""
import csv, glob, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request

HOME = os.path.expanduser("~")
DOWNLOADS = os.path.join(HOME, "Downloads")
CACHE = os.path.join(HOME, ".crate-digger-previews.json")
OUT = os.path.join(DOWNLOADS, "crate-previews.json")
PAUSE = 3.2


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def video_id(link):
    m = re.search(r"(?:embed/|v=|youtu\.be/)([A-Za-z0-9_-]{11})", link or "")
    return m.group(1) if m else ""


def clean_pair(song, artist):
    raw = (artist or "").strip()
    topic = bool(re.search(r"-\s*Topic\s*$", raw, flags=re.I))
    a = re.sub(r"\s*-\s*Topic\s*$", "", raw, flags=re.I).strip()
    t = (song or "").strip()
    if not topic:
        a = re.sub(r"VEVO$", "", a, flags=re.I).strip()
        t = re.sub(r"\s*[\(\[][^\)\]]*(official|video|audio|lyric|hd|remaster|live|demo|visuali[sz]er)[^\)\]]*[\)\]]", "", t, flags=re.I)
        t = re.sub(r"\s*\|\s*", " - ", t)
        m = re.match(r"^(.+?)\s+[-–—]\s+(.+)$", t)
        if m:
            a, t = m.group(1).strip(), m.group(2).strip()
    t = re.sub(r"\s*\(feat\.[^)]*\)", "", t, flags=re.I).strip()
    return a, t


def matches(wa, wt, ga, gt):
    wa, wt, ga, gt = norm(wa), norm(wt), norm(ga), norm(gt)
    if not (wa and wt and ga and gt):
        return False
    return (wa in ga or ga in wa) and (wt in gt or gt in wt)


def get_json(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crate-digger/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(4 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            time.sleep(2 * (attempt + 1))
    return None


def find_preview(artist, track):
    if not (artist and track):
        return ""
    q = urllib.parse.urlencode({"term": track + " " + artist, "entity": "song", "limit": 8})
    data = get_json("https://itunes.apple.com/search?" + q)
    time.sleep(PAUSE)
    for r in (data or {}).get("results", []):
        if r.get("previewUrl") and matches(artist, track, r.get("artistName"), r.get("trackName")):
            return r["previewUrl"]
    return ""


def find_csv(argv):
    if len(argv) > 1:
        p = os.path.expanduser(argv[1])
        if not os.path.exists(p):
            raise SystemExit("Cannot find " + p)
        return p
    m = glob.glob(os.path.join(DOWNLOADS, "*Song list*.csv"))
    if not m:
        raise SystemExit("No Song list CSV in Downloads. In Google Sheets: File > Download > Comma Separated Values.")
    return max(m, key=os.path.getmtime)


def read_songs(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    head = [h.strip().lower() for h in rows[0]]
    col = lambda n, fb: head.index(n) if n in head else fb
    i_s, i_a, i_l = col("song", 1), col("artist", 2), col("link", 4)
    out, seen = [], set()
    for r in rows[1:]:
        cell = lambda i: r[i].strip() if i < len(r) else ""
        vid = video_id(cell(i_l))
        if not vid or vid in seen:
            continue
        seen.add(vid)
        a, t = clean_pair(cell(i_s), cell(i_a))
        out.append((vid, a, t))
    return out


def main(argv):
    songs = read_songs(find_csv(argv))
    try:
        with open(CACHE) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    print("{} songs. First run takes about 13 minutes; Ctrl-C is safe.\n".format(len(songs)))
    try:
        for n, (vid, a, t) in enumerate(songs, 1):
            if vid not in cache:
                cache[vid] = find_preview(a, t)
            if n % 10 == 0:
                with open(CACHE, "w") as f:
                    json.dump(cache, f)
                print("[{}/{}] {} - {}".format(n, len(songs), a, t))
    except KeyboardInterrupt:
        print("\nStopped. Progress saved; run again to continue.")
    with open(CACHE, "w") as f:
        json.dump(cache, f)
    out = {v: cache[v] for v, _, _ in songs if cache.get(v)}
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("\nClips found for {} of {} songs".format(len(out), len(songs)))
    print("Wrote " + OUT)
    print("Upload that file to the Claude chat.")


if __name__ == "__main__":
    main(sys.argv)
