#!/usr/bin/env python3
"""Crate Digger: one command to do all the lookups.

Save this file once (for example in Downloads). It fetches the other scripts
itself, so you never have to save those again.

Run from Terminal:
  cd ~/Downloads
  python3 crate_update.py                 everything: new YouTube songs, then the next 300 Spotify songs
  python3 crate_update.py songs           only the YouTube list (genres, art, clips)
  python3 crate_update.py spotify         only the next 300 Spotify songs
  python3 crate_update.py spotify 600     only the next 600 Spotify songs
  python3 crate_update.py all 150         everything, with 150 Spotify songs

It does the steps one after another (Apple limits how fast they can be asked).
Ctrl-C is safe: it stops, and the next run continues where this one left off.
When it finishes it lists the files to upload to the Claude chat.
"""
import os, subprocess, sys, time, urllib.request

HOME = os.path.expanduser("~")
DL = os.path.join(HOME, "Downloads")
TOOLS = os.path.join(HOME, ".crate-digger-tools")
BASE = "https://thisisntaron.github.io/crate-digger/tools/"
CSV_URL = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vTpAbQo86ASNj3YvSTYo-3nDC2epbO2iboE0D3GSpOLHQStVAIv8OGuILUe9ZHTjHl7EUjBt_JO-ENm/"
           "pub?gid=0&single=true&output=csv")
CSV_PATH = os.path.join(DL, "Song list.csv")
HELPERS = ["genre_lookup.py", "crate_extras.py", "previews_lookup.py", "spotify_enrich.py"]
OUTPUTS = ["song-genres.csv", "crate-art.json", "crate-previews.json", "crate-spotify.json"]


def fetch(url, dest):
    req = urllib.request.Request(url + ("&" if "?" in url else "?") + "cb=" + str(int(time.time())),
                                 headers={"User-Agent": "crate-digger/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    with open(dest, "wb") as f:
        f.write(data)
    return len(data)


def run(title, args):
    print("\n" + "=" * 60 + "\n" + title + "\n" + "=" * 60, flush=True)
    start = time.time()
    code = subprocess.call([sys.executable] + args, cwd=DL)
    print("-- {} ({:.0f} min)".format("done" if code == 0 else "stopped with code %d" % code, (time.time() - start) / 60))
    return code == 0


def main(argv):
    mode = argv[1].lower() if len(argv) > 1 else "all"
    if mode not in ("all", "songs", "spotify"):
        raise SystemExit(__doc__)
    n_sp = argv[2] if len(argv) > 2 else "300"
    if not n_sp.isdigit():
        raise SystemExit(__doc__)
    os.makedirs(DL, exist_ok=True)
    os.makedirs(TOOLS, exist_ok=True)
    print("Getting the latest scripts...")
    for h in HELPERS:
        try:
            fetch(BASE + h, os.path.join(TOOLS, h))
        except Exception as e:
            if not os.path.exists(os.path.join(TOOLS, h)):
                raise SystemExit("Could not download {}: {}".format(h, e))
            print("  could not refresh {} ({}); using the saved copy".format(h, e))
    results = []
    try:
        if mode in ("all", "songs"):
            print("Downloading your current Song list...")
            try:
                size = fetch(CSV_URL, CSV_PATH)
                print("  {} bytes saved to {}".format(size, CSV_PATH))
            except Exception as e:
                raise SystemExit("Could not download the Song list: {}".format(e))
            for h, label in (("genre_lookup.py", "Genres for the YouTube list"),
                             ("crate_extras.py", "Album art and tempo"),
                             ("previews_lookup.py", "30 second clips")):
                results.append((label, run(label, [os.path.join(TOOLS, h), CSV_PATH])))
        if mode in ("all", "spotify"):
            results.append(("Spotify batch", run("Spotify songs: next " + n_sp, [os.path.join(TOOLS, "spotify_enrich.py"), n_sp])))
    except KeyboardInterrupt:
        print("\nStopped. Progress is saved; run again to continue.")
    print("\n" + "=" * 60 + "\nSummary")
    for label, ok in results:
        print("  {:<32} {}".format(label, "ok" if ok else "did not finish"))
    print("\nFiles in Downloads to upload to the Claude chat (only the ones updated just now):")
    now = time.time()
    found = False
    for o in OUTPUTS:
        p = os.path.join(DL, o)
        if os.path.exists(p) and now - os.path.getmtime(p) < 6 * 3600:
            print("  " + o)
            found = True
    if not found:
        print("  (none were written)")


if __name__ == "__main__":
    main(sys.argv)
