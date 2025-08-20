import os, re, json, argparse, logging, requests, sys, time, mimetypes
from typing import List, Optional, Tuple

STATE   = os.getenv("STATE_FILE", "last_id.json")
USER    = os.getenv("X_USER", "eWhispers")

# Keep for optional phrase-only mode
PHRASE_ANY = re.compile(
    r"(most\s+anticipated\s+earnings|#earnings\s+for\s+the\s+week\s+of)",
    re.I,
)

# Only x.com
STRICT_STATUS = rf"https?://x\.com/{re.escape(USER)}/status/\d+(?:/photo/\d+)?"

UA = {"User-Agent": "Mozilla/5.0 (EW Discord Bot)"}

def setup_log(debug: bool):
    logging.basicConfig(
        level=(logging.DEBUG if debug else logging.INFO),
        format="%(asctime)s %(levelname)s: %(message)s",
    )

def env_present(name: str) -> str:
    return "present" if os.getenv(name) else "MISSING"

def load_last() -> int:
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

def _status_id(url: str) -> Optional[int]:
    m = re.search(r"/status/(\d+)", url)
    return int(m.group(1)) if m else None

def _fetch_x_page(user: str) -> List[str]:
    sources = [
        f"https://r.jina.ai/https://x.com/{user}",
        f"https://r.jina.ai/http://x.com/{user}",
    ]
    texts = []
    for u in sources:
        try:
            logging.info("Fetch x.com (via r.jina.ai): %s", u)
            r = requests.get(u, timeout=25, headers=UA)
            logging.debug("Mirror status: %s, len=%s", r.status_code, len(r.text))
            r.raise_for_status()
            texts.append(r.text)
        except Exception as e:
            logging.warning("Fetch failed: %s", e)
    return texts

def _all_status_urls_x(text: str) -> List[str]:
    # Only take statuses from the target user
    urls = [ _normalize_to_x(m.group(0)) for m in re.finditer(STRICT_STATUS, text) ]
    # de-dupe, keep order
    seen = set(); out = []
    for u in urls:
        if u not in seen:
            seen.add(u); out.append(u)
    return out

def _phrase_near_link_x(text: str) -> Optional[str]:
    # Find a status URL that is within 600 chars of the phrase
    link_re = re.compile(f"({STRICT_STATUS})", re.I)
    for m in link_re.finditer(text):
        start, end = m.span(1)
        lo = max(0, start - 600)
        hi = min(len(text), end + 600)
        window = text[lo:hi]
        if PHRASE_ANY.search(window):
            return _normalize_to_x(m.group(1))
    return None

def latest_from_x(user: str, phrase_only: bool) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (phrase_url, newest_url) from x.com page (via r.jina.ai).
    Newest is determined by the highest numeric status ID among found URLs.
    """
    newest_url = None
    newest_id = -1
    phrase_url = None

    for text in _fetch_x_page(user):
        # Phrase candidate (optional)
        if phrase_url is None:
            pu = _phrase_near_link_x(text)
            if pu:
                phrase_url = pu

        # Collect all statuses and pick max ID
        for u in _all_status_urls_x(text):
            sid = _status_id(u)
            if sid is not None and sid > newest_id:
                newest_id = sid
                newest_url = u

    # If phrase_only requested but not found, null-out phrase_url to force no-op unless --force-latest used
    if phrase_only and not phrase_url:
        logging.info("Phrase-only mode: no phrase match found.")
    return phrase_url, newest_url

# ----- media lookup via proxy APIs (return ALL images) -----
def _media_list_from_proxy(status_id: int) -> List[str]:
    endpoints = [
        f"https://api.vxtwitter.com/Twitter/status/{status_id}",
        f"https://api.fxtwitter.com/Twitter/status/{status_id}",
        f"https://api.fixupx.com/Twitter/status/{status_id}",
    ]
    for ep in endpoints:
        try:
            logging.info("Probe media API: %s", ep)
            rr = requests.get(ep, timeout=20, headers=UA)
            if rr.status_code >= 400:
                logging.debug("API status=%s body=%s", rr.status_code, rr.text[:200])
                continue
            data = rr.json()

            # Prefer common fields if present
            pics = []
            # Known fields in these APIs often include 'mediaURLs' or 'media_extended'
            for key in ("mediaURLs", "media_extended"):
                val = data.get(key)
                if isinstance(val, list):
                    pics.extend(
                        [x for x in val if isinstance(x, str) and re.search(r"\.(jpg|jpeg|png|webp)$", x, re.I)]
                    )

            # Fallback: recursive scrape
            if not pics:
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
                pics = candidates

            # Filter to images hosted by Twitter's CDN or plain images
            photos = [u for u in pics if ("pbs.twimg.com" in u) or re.search(r"\.(jpg|jpeg|png|webp)$", u, re.I)]
            # De-dup and keep order
            seen=set(); out=[]
            for u in photos:
                if u not in seen:
                    seen.add(u); out.append(u)
            if out:
                logging.info("Found %d image(s)", len(out))
                return out
        except Exception as e:
            logging.debug("Media API error: %s", e)
    logging.info("No media found via proxies.")
    return []

def _download_many(urls: List[str]) -> List[Tuple[str, str, bytes]]:
    blobs = []
    for i, url in enumerate(urls, 1):
        logging.info("Download image: %s", url)
        r = requests.get(url, timeout=25, headers=UA)
        r.raise_for_status()
        ext = ".jpg"
        for e in (".jpg",".jpeg",".png",".webp"):
            if url.lower().endswith(e): ext = e; break
        fname = f"media_{i}{ext}"
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        blobs.append((fname, ctype, r.content))
    return blobs

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

def post_discord_photos(text: str, image_urls: List[str]):
    wh = os.getenv("DISCORD_WEBHOOK_URL")
    if not wh:
        logging.error("DISCORD_WEBHOOK_URL missing."); sys.exit(2)
    if not image_urls:
        post_discord_text(text); return

    # Discord limit: up to 10 attachments. Batch if necessary.
    batch_size = 10
    batches = [image_urls[i:i+batch_size] for i in range(0, len(image_urls), batch_size)]
    for idx, batch in enumerate(batches, 1):
        try:
            files = {}
            blobs = _download_many(batch)
            for j, (fname, ctype, blob) in enumerate(blobs):
                files[f"file{j}"] = (fname, blob, ctype)
            prefix = text if len(batches) == 1 else f"{text} (part {idx}/{len(batches)})"
            logging.info("Uploading %d image(s) to Discord", len(files))
            r = requests.post(wh, data={"content": prefix}, files=files, timeout=60)
            r.raise_for_status()
            logging.info("Discord upload OK (%s)", r.status_code)
        except Exception as e:
            logging.warning("Upload failed (%s), falling back to link dump.", e)
            links = "\n".join(batch)
            post_discord_text(f"{text}\n{links}")

def main():
    ap = argparse.ArgumentParser(description="Post EW collage link/photo(s) to Discord and exit.")
    ap.add_argument("--test", action="store_true", help="Send a test message and exit.")
    ap.add_argument("--force", action="store_true", help="(deprecated) no-op (use --force-latest).")
    ap.add_argument("--force-latest", action="store_true", help="Post even if already posted (use newest).")
    ap.add_argument("--force-url", type=str, help="Post this exact tweet URL.")
    ap.add_argument("--photo", action="store_true", help="Try to send image(s) instead of just the URL.")
    ap.add_argument("--notify-noop", action="store_true", help="Send a Discord message if nothing new was posted.")
    ap.add_argument("--phrase-only", action="store_true", help="Require phrase match; otherwise no post (unless forced).")
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
        logging.error("Refusing to post placeholder example status id. Provide a real tweet URL.")
        return

    # Resolve target URL (x.com only)
    if args.force_url:
        url = _normalize_to_x(args.force_url)
        logging.info("Using --force-url => %s", url)
    else:
        phrase_url, newest_url = latest_from_x(USER, phrase_only=args.phrase_only)
        logging.info("Detected: phrase_url=%s | newest_url=%s", phrase_url, newest_url)
        url = phrase_url if phrase_url else newest_url if args.force_latest or not args.phrase_only else None

    if not url:
        msg = "EW bot: No eligible x.com post found (phrase-only active and not matched)." if args.phrase_only \
              else "EW bot: No eligible x.com post found."
        logging.info(msg)
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
    if not args.force_latest and sid <= last_id:
        logging.info("Already posted this one. Use --force-latest to resend. Exiting 0.")
        if args.notify_noop:
            post_discord_text(f"EW bot: No new updates (latest already posted). id={sid} url={url}")
            _touch_last_post_ts()
        return

    title = "New Earnings Whispers post:"
    if args.photo:
        photos = _media_list_from_proxy(sid)
        if photos:
            post_discord_photos(title, photos)
            save_last(sid)
            _touch_last_post_ts()
            return
        else:
            logging.info("No media found; sending link instead.")

    post_discord_text(f"{title}\n{url}")
    save_last(sid)
    _touch_last_post_ts()

if __name__ == "__main__":
    main()
