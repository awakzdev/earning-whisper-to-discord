# ew_to_discord.py  — run-once poster
import os, re, json, requests

WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")  # <-- paste once (or set as env var)
STATE   = os.getenv("STATE_FILE", "last_id.json")
USER    = os.getenv("X_USER", "eWhispers")
PHRASE  = r"most\s+anticipated\s+earnings"
LINK_RE = r"https://x\.com/%s/status/\d+" % USER

def load_last():
    try:
        return int(json.load(open(STATE, "r", encoding="utf-8")).get("last_id", 0))
    except Exception:
        return 0

def save_last(i:int):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"last_id": int(i)}, f)

def find_link(text:str):
    p1 = re.compile(r"(%s)[\s\S]{0,600}%s" % (LINK_RE, PHRASE), re.I)
    p2 = re.compile(r"%s[\s\S]{0,600}(%s)" % (PHRASE, LINK_RE), re.I)
    m = p1.search(text) or p2.search(text) or re.search(LINK_RE, text)
    return m.group(1) if m and m.lastindex else (m.group(0) if m else None)

def latest_collage_url(user=USER):
    for u in (f"https://r.jina.ai/http://x.com/{user}",
              f"https://r.jina.ai/http://twitter.com/{user}"):
        try:
            t = requests.get(u, timeout=25, headers={"User-Agent":"Mozilla/5.0"}).text
            url = find_link(t)
            if url: return url
        except Exception:
            pass
    return None

def post_discord(text:str):
    assert WEBHOOK, "Set DISCORD_WEBHOOK_URL"
    r = requests.post(WEBHOOK, json={"content": text}, timeout=20)
    r.raise_for_status()

if __name__ == "__main__":
    url = latest_collage_url()
    if not url: raise SystemExit(0)
    m = re.search(r"/status/(\d+)", url)
    if not m:      raise SystemExit(0)
    sid = int(m.group(1))
    if sid <= load_last(): raise SystemExit(0)
    post_discord(f"New Earnings Whispers collage:\n{url}")
    save_last(sid)
