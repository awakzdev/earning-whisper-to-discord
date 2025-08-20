import os, re, json, argparse, logging, requests, sys, time, mimetypes

STATE   = os.getenv("STATE_FILE", "last_id.json")
USER    = os.getenv("X_USER", "eWhispers")
PHRASE  = r"most\s+anticipated\s+earnings"

# accept x/twitter/nitter; allow http or https; accept /photo/1 tail
LINK_DOMAINS = r"(?:x\.com|twitter\.com|mobile\.twitter\.com|nitter\.[\w.-]+)"
STRICT_STATUS = rf"https?://{LINK_DOMAINS}/{USER}/status/\d+(?:/photo/\d+)?"
BROAD_STATUS  = r"https?://[^\)\s]+/status/\d+(?:/photo/\d+)?"

def setup_log(debug: bool):
    logging.basicConfig(level=(logging.DEBUG if debug else logging.INFO),
                        format="%(asctime)s %(levelname)s: %(message)s")

def env_present(name: str) -> str:
    return "present" if os.getenv(name) else "MISSING"

def load_last():
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            v = int(json.load(f).get("last_id", 0))
            logging.debug("Loaded last_id=%s", v)
            return v
    except Exception as e:
        logging.debug("No state yet (%s). Treating last_id=0", e)
        return 0

def save_last(sid: int):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"last_id": int(sid)}, f)
    logging.info("Saved last_id=%s -> %s", sid, STATE)

def _touch_last_post_ts():
    try:
        with open("last_post.ts", "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
    except Exception as e:
        logging.debug("Could not write last_post.ts: %s", e)

def _normalize_to_x(url: str) -> str:
    u = re.sub(r"^https?://[^/]+", "https://x.com", url.strip())
    u = re.sub(r"/photo/\d+.*$", "", u)
    u = re.sub(r"\?.*$", "", u)
    return u

def _status_id(url: str) -> int | None:
    m = re.search(r"/status/(\d+)", url)
    return int(m.group(1)) if m else None

def _all_status_urls(text: str):
    urls = []
    for m in re.finditer(STRICT_STATUS, text):
        urls.append(_normalize_to_x(m.group(0)))
    if not urls:
        for m in re.finditer(BROAD_STATUS, text):
            urls.append(_normalize_to_x(m.group(0)))
    seen=set(); out=[]
    for u in urls:
        if u not in seen:
            seen.add(u); out.append(u)
    logging.debug("Found %d status urls", len(out))
    if out:
        logging.debug("First url sample: %s", out[0])
    return out

def _phrase_near_link(text: str):
    link_or = f"(?:{STRICT_STATUS}|{BROAD_STATUS})"
    p1 = re.compile(rf"({link_or})[\s\S]{{0,600}}{PHRASE}", re.I)
    p2 = re.compile(rf"{PHRASE}[\s\S]{{0,600}}({link_or})", re.I)
    m = p1.search(text) or p2.search(text)
    return (_normalize_to_x(m.group(1)) if m else None)

def _fetch(user=USER):
    sources = [
        f"https://r.jina.ai/http://nitter.net/{user}",
        f"https://r.jina.ai/https://nitter.net/{user}",
        f"https://r.jina.ai/http://x.com/{user}",
        f"https://r.jina.ai/http://twitter.com/{user}",
    ]
    for u in sources:
        try:
            logging.info("Fetch: %s", u)
            r = requests.get(u, timeout=25, headers={"User-Agent":"Mozilla/5.0"})
            logging.debug("Mirror status: %s", r.status_code)
            r.raise_for_status()
            yield r.text
        except Exception as e:
            logging.warning("Mirror failed: %s", e)

def latest_collage_or_latest_url():
    best_phrase_url = None
    latest_any_url  = None
    for text in _fetch(USER):
        if not best_phrase_url:
            u = _phrase_near_link(text)
            if u: best_phrase_url = u
        if not latest_any_url:
            urls = _all_status_urls(text)
            if urls: latest_any_url = urls[0]
    return best_phrase_url, latest_any_url

# ----- media lookup via free proxy APIs -----
def _media_from_proxy(status_id: int):
    endpoints = [
        f"https://api.vxtwitter.com/Twitter/status/{status_id}",
        f"https://api.fxtwitter.com/Twitter/status/{status_id}",
        f"https://api.fixupx.com/Twitter/status/{status_id}",
    ]
    for ep in endpoints:
        try:
            logging.info("Probe media API: %s", ep)
            rr = requests.get(ep, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
            if rr.status_code >= 400:
                logging.debug("API status=%s body=%s", rr.status_code, rr.text[:200])
                continue
            data = rr.json()
            candidates = []
            def collect(obj):
                if isinstance(obj, dict):
                    for v in obj.values(): collect(v)
                elif isinstance(obj, list):
                    for x in obj: collect(x)
                elif isinstance(obj, str):
                    if re.match(r"^https://.*\.(?:jpg|jpeg|png|webp)$", obj, re.I):
                        candidates.append(obj)
            collect(data)
            photos = [u for u in candidates if "pbs.twimg.com" in u or u.lower().endswith((".jpg",".jpeg",".png",".webp"))]
            if photos:
                logging.info("Found media: %s", photos[0])
                return photos[0]
        except Exception as e:
            logging.debug("Media API error: %s", e)
    logging.info("No media found via proxies.")
    return None

def _download(url: str):
    logging.info("Download image: %s", url)
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    ext = ".jpg"
    for e in (".jpg",".jpeg",".png",".webp"):
        if url.lower().endswith(e): ext = e; break
    fname = "collage" + ext
    ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    return fname, ctype, r.content

def post_discord_text(text: str):
    wh = os.getenv("DISCORD_WEBHOOK_URL")
    if not wh:
        logging.error("DISCORD_WEBHOOK_URL missing."); sys.exit(2)
    logging.info("Posting text to Discord")
    r = requests.post(wh, json={"content": text}, timeout=20)
    try: r.raise_for_status()
    except Exception as e:
        logging.error("Discord post failed: %s | body=%s", e, r.text); raise
    logging.info("Discord post OK (%s)", r.status_code)

def post_discord_photo(text: str, image_url: str):
    wh = os.getenv("DISCORD_WEBHOOK_URL")
    if not wh:
        logging.error("DISCORD_WEBHOOK_URL missing."); sys.exit(2)
    try:
        fname, ctype, blob = _download(image_url)
        logging.info("Uploading image to Discord: %s", fname)
        r = requests.post(wh, data={"content": text}, files={"file": (fname, blob, ctype)}, timeout=30)
        r.raise_for_status()
        logging.info("Discord upload OK (%s)", r.status_code)
    except Exception as e:
        logging.warning("Upload failed (%s), falling back to link.", e)
        post_discord_text(f"{text}\n{image_url}")

def main():
    ap = argparse.ArgumentParser(description="Post EW collage link/photo to Discord and exit.")
    ap.add_argument("--test", action="store_true", help="Send a test message and exit.")
    ap.add_argument("--force", action="store_true", help="Post even if already posted (requires a URL found).")
    ap.add_argument("--force-latest", action="store_true", help="Post the newest tweet even if phrase not found.")
    ap.add_argument("--force-url", type=str, help="Post this exact tweet URL.")
    ap.add_argument("--photo", action="store_true", help="Try to send the collage image instead of just the URL.")
    ap.add_argument("--notify-noop", action="store_true", help="Send a Discord message if nothing new was posted.")
    ap.add_argument("--debug", action="store_true", help="Verbose logging.")
    args = ap.parse_args()

    setup_log(args.debug)
    logging.info("Startup | USER=%s | STATE=%s", USER, STATE)
    logging.info("Env check: DISCORD_WEBHOOK_URL=%s", env_present("DISCORD_WEBHOOK_URL"))

    if args.test:
        post_discord_text(f"Test ✅ {time.strftime('%Y-%m-%dT%H:%M:%S')}")
        _touch_last_post_ts()
        return

    if args.force_url and "1234567890123456789" in args.force_url:
        logging.error("Refusing to post placeholder example status id. Provide a real tweet URL."); return

    # Resolve target URL
    if args.force_url:
        url = _normalize_to_x(args.force_url)
        logging.info("Using --force-url => %s", url)
    else:
        phrase_url, latest_url = latest_collage_or_latest_url()
        logging.info("Detected: phrase_url=%s | latest_url=%s", phrase_url, latest_url)
        url = phrase_url or (latest_url if args.force_latest else None)

    if not url:
        msg = "EW bot: No eligible @eWhispers tweet found (phrase not seen and --force-latest not set)."
        logging.info("Nothing to post (no URL resolved).")
        if args.notify_noop:
            post_discord_text(msg)
            _touch_last_post_ts()
        return

    sid = _status_id(url)
    logging.info("Chosen URL: %s | parsed status_id=%s", url, sid)
    if not sid:
        logging.info("Could not parse status id: %s", url)
        if args.notify_noop:
            post_discord_text(f"EW bot: Skipped (couldn't parse status id) -> {url}")
            _touch_last_post_ts()
        return

    last_id = load_last()
    logging.info("last_id=%s", last_id)
    if not args.force and sid <= last_id:
        logging.info("Already posted this one. Use --force to resend. Exiting 0.")
        if args.notify_noop:
            post_discord_text(f"EW bot: No new updates (latest already posted). id={sid} url={url}")
            _touch_last_post_ts()
        return

    if args.photo:
        media = _media_from_proxy(sid)
        if media:
            post_discord_photo("New Earnings Whispers collage:", media)
            save_last(sid)
            _touch_last_post_ts()
            return
        else:
            logging.info("No media found; sending link instead.")

    post_discord_text(f"New Earnings Whispers collage:\n{url}")
    save_last(sid)
    _touch_last_post_ts()

if __name__ == "__main__":
    import argparse, mimetypes
    main()
