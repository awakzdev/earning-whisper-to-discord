#!/usr/bin/env python3
import os, sys, json, argparse, logging, time
import http.client as http_client
from typing import Optional, List, Dict

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv, find_dotenv

DISCORD_API = "https://discord.com/api/v10"
UA = "EarningWhisperBot (https://github.com/awakzdev/earning-whisper-to-discord,1.0)"

def load_env():
    # Load .env if present
    env_path = find_dotenv(usecwd=True)
    if env_path:
        load_dotenv(env_path, override=True)
        logging.getLogger().info("Loaded .env from %s", env_path)

def make_session(verbose: bool) -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=5, backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET","POST","PUT","DELETE"],
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    if verbose:
        http_client.HTTPConnection.debuglevel = 1
        logging.getLogger("urllib3").setLevel(logging.DEBUG)
        logging.getLogger("urllib3").propagate = True
    return s

def H(token: str) -> Dict[str,str]:
    return {"Authorization": f"Bot {token}", "User-Agent": UA, "Content-Type": "application/json"}

def die(msg: str, r: Optional[requests.Response]=None):
    if r is not None:
        try:
            body = r.text
        except Exception:
            body = "<no body>"
        logging.error("HTTP %s %s -> %s\nResponse headers: %s\nBody: %s",
                      getattr(r.request, 'method', '?'), r.url, r.status_code, r.headers, body)
    logging.critical(msg)
    sys.exit(1)

def get_bot(session: requests.Session, token: str) -> dict:
    r = session.get(f"{DISCORD_API}/users/@me", headers=H(token))
    if r.status_code != 200:
        die("/users/@me failed", r)
    j = r.json()
    logging.info("Bot OK: id=%s username=%s", j.get("id"), j.get("username"))
    return j

def get_guild(session: requests.Session, token: str, guild_id: str) -> dict:
    r = session.get(f"{DISCORD_API}/guilds/{guild_id}", headers=H(token))
    if r.status_code != 200:
        die(f"GET guild {guild_id} failed — is the bot installed in this server and is the ID correct?", r)
    j = r.json()
    logging.info("Guild OK: id=%s name=%s", j.get("id"), j.get("name"))
    return j

def list_commands(session: requests.Session, token: str, app_id: str, guild_id: Optional[str]=None) -> List[dict]:
    url = f"{DISCORD_API}/applications/{app_id}"
    url += f"/guilds/{guild_id}/commands" if guild_id else "/commands"
    r = session.get(url, headers=H(token))
    if r.status_code != 200:
        die("List commands failed", r)
    cmds = r.json()
    logging.info("Found %d %s commands", len(cmds), "guild" if guild_id else "global")
    print(json.dumps(cmds, indent=2))
    return cmds

def delete_all(session: requests.Session, token: str, app_id: str, guild_id: Optional[str]=None):
    cmds = list_commands(session, token, app_id, guild_id)
    for c in cmds:
        url = f"{DISCORD_API}/applications/{app_id}"
        url += f"/guilds/{guild_id}/commands/{c['id']}" if guild_id else f"/commands/{c['id']}"
        r = session.delete(url, headers=H(token))
        if r.status_code not in (200, 204):
            die(f"Delete failed for {c['name']}", r)
        logging.info("Deleted: %s (%s)", c["name"], c["id"])

def post_single(session: requests.Session, token: str, app_id: str, payload: dict, guild_id: Optional[str]=None) -> dict:
    url = f"{DISCORD_API}/applications/{app_id}"
    url += f"/guilds/{guild_id}/commands" if guild_id else "/commands"
    r = session.post(url, headers=H(token), data=json.dumps(payload))
    if r.status_code not in (200, 201):
        die(f"Create command '{payload.get('name')}' failed", r)
    j = r.json()
    logging.info("Created command: %s (id=%s scope=%s)", j.get("name"), j.get("id"), "guild" if guild_id else "global")
    return j

def put_bulk(session: requests.Session, token: str, app_id: str, payloads: List[dict], guild_id: Optional[str]=None) -> List[dict]:
    url = f"{DISCORD_API}/applications/{app_id}"
    url += f"/guilds/{guild_id}/commands" if guild_id else "/commands"
    r = session.put(url, headers=H(token), data=json.dumps(payloads))
    if r.status_code not in (200, 201):
        die("Bulk overwrite failed", r)
    j = r.json()
    logging.info("Bulk overwrite OK: %d commands", len(j))
    print(json.dumps(j, indent=2))
    return j

def default_commands() -> List[dict]:
    return [
        {"name": "check",   "description": "Run EW -> Discord now", "type": 1},
        {"name": "trigger", "description": "Alias of /check",       "type": 1},
    ]

def trigger_github(session: requests.Session):
    pat  = os.getenv("GH_PAT")
    repo = os.getenv("GH_REPO")
    wf   = os.getenv("GH_WORKFLOW")
    ref  = os.getenv("GH_REF", "main")
    if not all([pat, repo, wf]):
        die("GH_PAT, GH_REPO, GH_WORKFLOW are required in .env for --trigger.")
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/dispatches"
    hdr = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    }
    body = {"ref": ref}
    r = session.post(url, headers=hdr, data=json.dumps(body))
    if r.status_code != 204:
        die(f"GitHub dispatch failed ({r.status_code})", r)
    logging.info("GitHub workflow dispatch OK (204) -> %s@%s", wf, ref)

def main():
    ap = argparse.ArgumentParser(description="Manage Discord slash commands + trigger GH, using .env")
    ap.add_argument("--verbose", action="store_true", help="HTTP debug logs")
    ap.add_argument("--list", action="store_true", help="List commands and exit")
    ap.add_argument("--delete-all", action="store_true", help="Delete all commands then exit")
    ap.add_argument("--bulk", action="store_true", help="Use bulk overwrite instead of single posts")
    ap.add_argument("--global", dest="is_global", action="store_true", help="Operate on global (not guild) commands")
    ap.add_argument("--trigger", action="store_true", help="Trigger GitHub workflow_dispatch using GH_* envs")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")
    load_env()

    token  = os.getenv("DISCORD_BOT_TOKEN")
    app_id = os.getenv("DISCORD_APP_ID")
    guild  = None if args.is_global else os.getenv("DISCORD_GUILD_ID")

    if not token or not app_id:
        die("Missing DISCORD_BOT_TOKEN or DISCORD_APP_ID in .env")

    session = make_session(args.verbose)

    # Sanity
    get_bot(session, token)
    if guild:
        # This fails if bot isn't installed in that server or ID is wrong
        get_guild(session, token, guild)

    # Optional GitHub trigger first (for testing)
    if args.trigger:
        trigger_github(session)

    # Command mgmt
    if args.list:
        list_commands(session, token, app_id, guild)
        return

    if args.delete_all:
        delete_all(session, token, app_id, guild)
        return

    payloads = default_commands()
    if args.bulk:
        put_bulk(session, token, app_id, payloads, guild)
    else:
        for p in payloads:
            post_single(session, token, app_id, p, guild)

    time.sleep(0.3)
    list_commands(session, token, app_id, guild)

if __name__ == "__main__":
    main()
