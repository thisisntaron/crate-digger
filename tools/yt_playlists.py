#!/usr/bin/env python3
"""Crate Digger: create real, named playlists in YouTube Music from your decks.

Uses the exact video ids (the app's ytmap.json), so every song is the right one.
Playlists are created PRIVATE. Open them in YouTube Music and change that if you like.

Usage (Mac Terminal, or through the launcher):
  python3 yt_playlists.py list           show your decks and which are ready
  python3 yt_playlists.py                make playlists for decks that are full (50 songs) and not exported
  python3 yt_playlists.py all            every deck that is not exported
  python3 yt_playlists.py "chill"        decks whose name contains "chill"
  add --again to remake a deck that was already created
  add --dry to see what would be created without touching YouTube

Songs without an exact match are skipped and listed, so you can add them by hand.
It reads the newest crate-digger-backup*.json in Downloads, Documents or Desktop
(make a fresh one in the app first: More, Back up).

One-time sign-in: see SETUP below. The sign-in stays in ~/.crate-digger-tools on this Mac.
SETUP: in Chrome, open music.youtube.com signed in, press F12 (or Option+Cmd+I), Network tab,
type "browse" in the filter, click any row (a POST that says 200), find "Request Headers",
right-click, Copy request headers. Open TextEdit, Format menu, Make Plain Text, paste,
save as ytm-headers.txt in Downloads. Run this script. It converts the file and deletes it.
"""
import json, os, sys, time, urllib.request, subprocess

HOME = os.path.expanduser("~")
TOOLS = os.path.join(HOME, ".crate-digger-tools")
VENV = os.path.join(TOOLS, "venv")
AUTH = os.path.join(TOOLS, "ytm-auth.json")
HEADERS_TXT = os.path.join(HOME, "Downloads", "ytm-headers.txt")
MADE = os.path.join(TOOLS, "yt-created.json")
MAP_URL = "https://thisisntaron.github.io/crate-digger/ytmap.json"
DECK_MAX = 50


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


def newest_backup():
    import glob
    files = []
    for folder in ("Downloads", "Documents", "Desktop"):
        files += glob.glob(os.path.join(HOME, folder, "crate-digger-backup*.json"))
    files.sort(key=os.path.getmtime)
    if not files:
        sys.exit("No crate-digger-backup*.json in Downloads, Documents or Desktop. Make a backup in the app first (More, Back up).")
    print("Using", os.path.basename(files[-1]), flush=True)
    return json.load(open(files[-1]))["state"]


def video_id(sid, ymap):
    sid = str(sid)
    if len(sid) == 11:
        return sid
    v = ymap.get(sid)
    return v[0] if v else ""


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    again, dry = "--again" in sys.argv, "--dry" in sys.argv
    mode = args[0] if args else "ready"
    st = newest_backup()
    songs = st.get("songs", {})
    req = urllib.request.Request(MAP_URL + "?cb=" + str(int(time.time())), headers={"User-Agent": "crate-digger/1.0"})
    ymap = json.load(urllib.request.urlopen(req, timeout=60))
    made = json.load(open(MADE)) if os.path.exists(MADE) else {}

    decks = [d for d in st.get("decks", []) if not d.get("ex") and d.get("ids")]
    if mode == "list":
        for d in decks:
            n = len(d["ids"]); ok = sum(1 for i in d["ids"] if video_id(i, ymap))
            print("%-34s %2d songs, %2d exact%s%s" % ((d.get("name") or "Untitled")[:34], n, ok,
                  "  READY" if n >= DECK_MAX else "", "  (already created)" if str(d["id"]) in made else ""))
        return
    if mode == "ready":
        pick = [d for d in decks if len(d["ids"]) >= DECK_MAX]
    elif mode == "all":
        pick = decks
    else:
        pick = [d for d in decks if mode.lower() in (d.get("name") or "").lower()]
    if not again:
        skipped = [d for d in pick if str(d["id"]) in made]
        pick = [d for d in pick if str(d["id"]) not in made]
        for d in skipped:
            print("Skipping %s, already created (use --again to remake)" % d.get("name"))
    if not pick:
        print("No decks to create.")
        return

    yt = None
    if not dry:
        if not os.path.exists(AUTH):
            if not os.path.exists(HEADERS_TXT):
                sys.exit("Sign-in not set up yet. See the SETUP steps at the top of yt_playlists.py (or ask Claude). Save the headers as ~/Downloads/ytm-headers.txt and run again.")
            import ytmusicapi
            ytmusicapi.setup(filepath=AUTH, headers_raw=open(HEADERS_TXT).read())
            os.chmod(AUTH, 0o600)
            os.remove(HEADERS_TXT)
            print("Sign-in saved. Deleted ytm-headers.txt.", flush=True)
        from ytmusicapi import YTMusic
        yt = YTMusic(AUTH)

    for d in pick:
        vids, miss, seen = [], [], set()
        for sid in d["ids"]:
            v = video_id(sid, ymap)
            if not v:
                s = songs.get(sid) or {}
                miss.append("%s - %s" % (s.get("a", "?"), s.get("t", sid)))
            elif v not in seen:
                seen.add(v); vids.append(v)
        name = d.get("name") or "Crate Digger deck"
        print("\n%s: %d exact, %d without a match" % (name, len(vids), len(miss)), flush=True)
        for m in miss:
            print("    missing:", m)
        if not vids:
            continue
        if dry:
            print("    (dry run, not created)")
            continue
        try:
            pid = yt.create_playlist(name, "Made with Crate Digger", "PRIVATE", vids)
        except Exception as e:
            print("    FAILED:", str(e)[:200])
            print("    If this says unauthorized, delete ~/.crate-digger-tools/ytm-auth.json and redo the sign-in.")
            break
        if not isinstance(pid, str):
            print("    FAILED:", str(pid)[:200])
            break
        made[str(d["id"])] = {"name": name, "playlist": pid, "n": len(vids), "at": time.strftime("%Y-%m-%d")}
        json.dump(made, open(MADE, "w"), indent=1)
        print("    created: https://music.youtube.com/playlist?list=" + pid, flush=True)
        time.sleep(3)
    print("\nDone. Mark these decks as exported in the app so they leave your list.")


if __name__ == "__main__":
    boot()
    main()
