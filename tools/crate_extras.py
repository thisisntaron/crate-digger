#!/usr/bin/env python3
"""Crate Digger art and tempo lookup (macOS).

For every song in your "Song list" CSV this script
  1. finds album art: the YouTube thumbnail for "Topic" uploads, iTunes covers
     for music videos and anything YouTube cannot supply, and
  2. looks up the tempo (BPM) on Deezer when it has one.
It shrinks each cover to a small square and writes one file, crate-art.json,
into Downloads. Upload that file to the Claude chat.

Setup: nothing to install. It uses the python3 that ships with macOS and the
built-in `sips` image tool. No keys are needed.

Run:
  cd ~/Downloads
  python3 crate_extras.py                 (newest "Song list" CSV in Downloads)
  python3 crate_extras.py path/to/file.csv

Everything it learns is cached in ~/.crate-digger-art, so a second run only
looks up new songs. It is polite to iTunes (about 20 requests a minute), so the
first run takes around ten minutes. You can stop it with Ctrl-C and run it
again; it picks up where it left off.
"""
import base64
import csv
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

HOME = os.path.expanduser("~")
DOWNLOADS = os.path.join(HOME, "Downloads")
CACHE_DIR = os.path.join(HOME, ".crate-digger-art")
META_FILE = os.path.join(CACHE_DIR, "meta.json")
OUT_FILE = os.path.join(DOWNLOADS, "crate-art.json")
SIZE = 160            # pixels, each cover is shrunk to a square this big
ITUNES_PAUSE = 3.2    # seconds between iTunes calls
DEEZER_PAUSE = 0.15
UA = "crate-digger/1.0"


# ---------- small helpers ----------
def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def video_id(link):
    m = re.search(r"(?:embed/|v=|youtu\.be/)([A-Za-z0-9_-]{11})", link or "")
    return m.group(1) if m else ""


def clean_pair(song, artist):
    """Returns (artist, track, is_topic). YouTube Music auto uploads are tidy;
    music video uploads carry 'Artist - Track (Official Video)' in the title."""
    raw_artist = (artist or "").strip()
    topic = bool(re.search(r"-\s*Topic\s*$", raw_artist, flags=re.I))
    a = re.sub(r"\s*-\s*Topic\s*$", "", raw_artist, flags=re.I).strip()
    t = (song or "").strip()
    if not topic:
        a = re.sub(r"VEVO$", "", a, flags=re.I).strip()
        t = re.sub(r"\s*[\(\[][^\)\]]*(official|video|audio|lyric|hd|remaster|live|demo|visuali[sz]er)[^\)\]]*[\)\]]", "", t, flags=re.I)
        t = re.sub(r"\s*\|\s*", " - ", t)
        m = re.match(r"^(.+?)\s+[-–—]\s+(.+)$", t)
        if m:
            a, t = m.group(1).strip(), m.group(2).strip()
    t = re.sub(r"\s*\(feat\.[^)]*\)", "", t, flags=re.I).strip()
    return a, t, topic


def matches(want_artist, want_track, got_artist, got_track):
    wa, wt, ga, gt = norm(want_artist), norm(want_track), norm(got_artist), norm(got_track)
    if not (wa and wt and ga and gt):
        return False
    artist_ok = wa in ga or ga in wa
    track_ok = wt in gt or gt in wt
    return artist_ok and track_ok


def http_get(url, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(3 * (attempt + 1))   # 403 or 429 from rate limits, wait and retry
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(2 * (attempt + 1))
    return None


def get_json(url):
    data = http_get(url)
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:
        return None


def sips(src, dst, crop=None):
    """Crop to the centered square `crop` (if given), shrink to SIZE, save as JPEG."""
    cmd = ["sips"]
    if crop:
        cmd += ["-c", str(crop), str(crop)]
    cmd += ["-z", str(SIZE), str(SIZE), "-s", "format", "jpeg", "-s", "formatOptions", "68", src, "--out", dst]
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 500


# ---------- art ----------
def yt_art(vid, dst):
    """Keeps the whole 4:3 thumbnail, shrunk to 240 x 180. The app crops out the
    album art square itself, so no cropping is done here."""
    data = http_get("https://i.ytimg.com/vi/%s/hqdefault.jpg" % vid)
    if not data or len(data) < 3000:      # YouTube's grey placeholder is tiny
        return False
    tmp = dst + ".src"
    with open(tmp, "wb") as f:
        f.write(data)
    r = subprocess.run(["sips", "-Z", "240", "-s", "format", "jpeg", "-s", "formatOptions", "70", tmp, "--out", dst],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.remove(tmp)
    return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 500


def itunes_art(artist, track, dst):
    if not (artist and track):
        return False
    q = urllib.parse.urlencode({"term": track + " " + artist, "entity": "song", "limit": 5})
    data = get_json("https://itunes.apple.com/search?" + q)
    time.sleep(ITUNES_PAUSE)
    for r in (data or {}).get("results", []):
        if matches(artist, track, r.get("artistName"), r.get("trackName")):
            url = (r.get("artworkUrl100") or "").replace("100x100bb", "300x300bb")
            img = http_get(url) if url else None
            if img and len(img) > 1500:
                tmp = dst + ".src"
                with open(tmp, "wb") as f:
                    f.write(img)
                ok = sips(tmp, dst)
                os.remove(tmp)
                if ok:
                    return True
    return False


def get_art(s, dst):
    """Returns 'yt', 'it' or None."""
    if s["topic"]:
        if yt_art(s["vid"], dst):
            return "yt"
        return "it" if itunes_art(s["artist"], s["track"], dst) else None
    if itunes_art(s["artist"], s["track"], dst):  # a video frame is not album art, so try iTunes first
        return "it"
    return "yt" if yt_art(s["vid"], dst) else None


# ---------- tempo ----------
def deezer_bpm(artist, track):
    if not (artist and track):
        return 0
    q = urllib.parse.urlencode({"q": "%s %s" % (artist, track), "limit": 5}, quote_via=urllib.parse.quote)
    data = get_json("https://api.deezer.com/search?" + q)
    time.sleep(DEEZER_PAUSE)
    for r in (data or {}).get("data", []):
        if matches(artist, track, (r.get("artist") or {}).get("name"), r.get("title")):
            d = get_json("https://api.deezer.com/track/%s" % r.get("id"))
            time.sleep(DEEZER_PAUSE)
            bpm = float((d or {}).get("bpm") or 0)
            return bpm if bpm > 0 else 0
    return 0


# ---------- plumbing ----------
def find_csv(argv):
    if len(argv) > 1:
        path = os.path.expanduser(argv[1])
        if not os.path.exists(path):
            raise SystemExit("Cannot find " + path)
        return path
    matches_ = glob.glob(os.path.join(DOWNLOADS, "*Song list*.csv"))
    if not matches_:
        raise SystemExit("No Song list CSV in Downloads. In Google Sheets: File > Download > Comma Separated Values.")
    return max(matches_, key=os.path.getmtime)


def read_songs(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise SystemExit("That CSV is empty.")
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
        a, t, topic = clean_pair(cell(i_s), cell(i_a))
        out.append({"vid": vid, "artist": a, "track": t, "topic": topic})
    return out


def load_meta():
    try:
        with open(META_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_meta(meta):
    with open(META_FILE, "w") as f:
        json.dump(meta, f)


def main(argv):
    if subprocess.run(["which", "sips"], stdout=subprocess.DEVNULL).returncode != 0:
        raise SystemExit("This script needs macOS's built-in sips tool.")
    os.makedirs(CACHE_DIR, exist_ok=True)
    songs = read_songs(find_csv(argv))
    meta = load_meta()
    print("{} songs. First run takes about ten minutes; Ctrl-C is safe.\n".format(len(songs)))
    try:
        for n, s in enumerate(songs, 1):
            m = meta.setdefault(s["vid"], {})
            dst = os.path.join(CACHE_DIR, s["vid"] + ".jpg")
            if m.get("src") == "yt" and m.get("v") != 3:     # first version cropped these badly
                m["src"] = "yt" if yt_art(s["vid"], dst) else ""
                m["v"] = 3
            elif "src" not in m or (m["src"] and not os.path.exists(dst)) or (m["src"] == "" and m.get("tries", 0) < 2):
                m["src"] = get_art(s, dst) or ""
                m["tries"] = m.get("tries", 0) + 1
                m["v"] = 3
            if "bpm" not in m or (not m["bpm"] and m.get("bpm_v", 0) < 2):
                m["bpm"] = deezer_bpm(s["artist"], s["track"])
                m["bpm_v"] = 2
            if n % 10 == 0:
                save_meta(meta)
                print("[{}/{}] {} - {}".format(n, len(songs), s["artist"], s["track"]))
    except KeyboardInterrupt:
        print("\nStopped. Progress is saved; run again to continue.")
        save_meta(meta)
        raise SystemExit(1)
    save_meta(meta)

    out, srcs = {}, Counter()
    for s in songs:
        m = meta.get(s["vid"], {})
        dst = os.path.join(CACHE_DIR, s["vid"] + ".jpg")
        entry = {}
        if m.get("src") and os.path.exists(dst):
            with open(dst, "rb") as f:
                entry["a"] = base64.b64encode(f.read()).decode("ascii")
            entry["s"] = m["src"]
            srcs[m["src"]] += 1
        else:
            srcs["none"] += 1
        if m.get("bpm"):
            entry["bpm"] = round(m["bpm"], 1)
        if entry:
            out[s["vid"]] = entry
    with open(OUT_FILE, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("\nArt from YouTube: {}, from iTunes: {}, none found: {}".format(srcs["yt"], srcs["it"], srcs["none"]))
    print("Tempo found for {} of {} songs".format(sum(1 for e in out.values() if "bpm" in e), len(songs)))
    print("Wrote {} ({:.1f} MB)".format(OUT_FILE, os.path.getsize(OUT_FILE) / 1e6))
    print("Upload that file to the Claude chat.")


if __name__ == "__main__":
    main(sys.argv)
