"""
Watches official Claude / Anthropic social accounts for posts about usage-limit
resets and forwards matches to a Discord channel via webhook.

Standard library only. Configure in config.json (see config.example.json).

Usage:
  python reset_watcher.py              # check once (good for Task Scheduler)
  python reset_watcher.py --loop 10    # keep running, check every 10 minutes
  python reset_watcher.py --test       # send a test message to the webhook
  python reset_watcher.py --dry-run    # print matches, don't post or save state
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"
USER_AGENT = "Mozilla/5.0 (reset-watcher; +discord webhook relay)"
MAX_SEEN = 2000


# ---------------------------------------------------------------- helpers

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))  # -sig: tolerate Notepad's BOM
    except FileNotFoundError:
        return default


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def strip_html(text):
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


STATUS_RE = re.compile(r"https?://[^/]+/([A-Za-z0-9_]+)/status(?:es)?/(\d+)")


def canonical_link(link):
    """Turn nitter/xcancel/twitter links into a clean x.com status URL."""
    m = STATUS_RE.search(link or "")
    if m:
        return f"https://x.com/{m.group(1)}/status/{m.group(2)}"
    return link


# ---------------------------------------------------------------- sources

def fetch_rss(source):
    """Any RSS 2.0, Atom, or JSON Feed (rss.app, RSSHub, nitter/xcancel, etc.)."""
    raw = http_get(source["url"])
    label = source.get("label") or source["url"]
    posts = []

    if raw.lstrip()[:1] == b"{":  # JSON Feed (jsonfeed.org)
        for item in json.loads(raw).get("items", []):
            link = canonical_link(item.get("url", ""))
            text = item.get("content_text") or strip_html(item.get("content_html", "")) or item.get("title", "")
            posts.append({"id": link or item.get("id"), "link": link, "text": text})
        root = ET.Element("empty")
    else:
        root = ET.fromstring(raw)
    atom = "{http://www.w3.org/2005/Atom}"

    for item in root.iter("item"):  # RSS 2.0
        link = (item.findtext("link") or "").strip()
        title = strip_html(item.findtext("title") or "")
        text = strip_html(item.findtext("description") or "")
        if title and title not in text:  # e.g. claude-resets.com: short title + summary
            text = f"{title}\n{text}".strip()
        guid = (item.findtext("guid") or link).strip()
        posts.append({"id": canonical_link(guid), "link": canonical_link(link), "text": text})

    for entry in root.iter(f"{atom}entry"):  # Atom
        link_el = entry.find(f"{atom}link")
        link = link_el.get("href", "") if link_el is not None else ""
        text = strip_html(entry.findtext(f"{atom}content") or entry.findtext(f"{atom}title") or "")
        guid = (entry.findtext(f"{atom}id") or link).strip()
        posts.append({"id": canonical_link(guid), "link": canonical_link(link), "text": text})

    for p in posts:
        m = STATUS_RE.search(p["link"])
        p["author"] = f"@{m.group(1)}" if m else label
    return posts


def fetch_x(source, cfg, state):
    """Official X API v2. Needs x_bearer_token in config.json (paid API access)."""
    token = cfg.get("x_bearer_token")
    if not token:
        raise RuntimeError("x source needs 'x_bearer_token' in config.json")
    headers = {"Authorization": f"Bearer {token}"}
    username = source["username"].lstrip("@")

    ids = state.setdefault("x_user_ids", {})
    if username not in ids:
        data = json.loads(http_get(f"https://api.x.com/2/users/by/username/{username}", headers))
        ids[username] = data["data"]["id"]

    qs = urllib.parse.urlencode({"max_results": 10, "exclude": "replies,retweets"})
    data = json.loads(http_get(f"https://api.x.com/2/users/{ids[username]}/tweets?{qs}", headers))
    return [
        {
            "id": f"https://x.com/{username}/status/{t['id']}",
            "link": f"https://x.com/{username}/status/{t['id']}",
            "text": t.get("text", ""),
            "author": f"@{username}",
        }
        for t in data.get("data", [])
    ]


def fetch_source(source, cfg, state):
    kind = source.get("type", "rss")
    if kind == "rss":
        return fetch_rss(source)
    if kind == "x":
        return fetch_x(source, cfg, state)
    raise RuntimeError(f"unknown source type: {kind}")


# ---------------------------------------------------------------- discord

def post_discord(webhook, content):
    body = json.dumps({
        "content": content[:2000],
        "username": "Claude Reset Watch",
        "allowed_mentions": {"parse": ["roles", "everyone"]},
    }).encode()
    req = urllib.request.Request(
        webhook, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    for _ in range(3):
        try:
            urllib.request.urlopen(req, timeout=30).close()
            return
        except urllib.error.HTTPError as e:
            if e.code == 429:  # rate limited: wait as told, then retry
                retry = json.loads(e.read() or b"{}").get("retry_after", 2)
                time.sleep(float(retry) + 0.5)
                continue
            raise


def format_message(post, cfg):
    link = post["link"]
    if cfg.get("link_style") == "fxtwitter":  # nicer Discord embeds
        link = re.sub(r"^https://x\.com/", "https://fxtwitter.com/", link)
    snippet = post["text"] if len(post["text"]) <= 600 else post["text"][:597] + "..."
    mention = cfg.get("mention", "")
    lines = [f"{mention} **Reset announcement from {post['author']}**".strip()]
    if snippet:
        lines.append("> " + snippet.replace("\n", "\n> "))
    lines.append(link)
    return "\n".join(lines)


# ---------------------------------------------------------------- main loop

def check_once(cfg, dry_run=False):
    state = load_json(STATE_PATH, {"seen": [], "initialized_sources": []})
    seen = set(state["seen"])
    patterns = [re.compile(k, re.I) for k in cfg.get("keywords", [r"\breset"])]

    for source in cfg["sources"]:
        key = source.get("url") or f"x:{source.get('username')}"
        try:
            posts = fetch_source(source, cfg, state)
        except Exception as e:
            print(f"[warn] {key}: {e}", file=sys.stderr)
            continue

        # First time we see a source, just record what's there so we don't
        # flood the channel with old posts.
        first_run = key not in state["initialized_sources"]

        for post in reversed(posts):  # oldest first
            if post["id"] in seen:
                continue
            seen.add(post["id"])
            state["seen"].append(post["id"])
            if first_run and not cfg.get("post_existing_on_first_run", False):
                continue
            # Sources that only contain resets (e.g. claude-resets.com) skip the keyword check.
            if not source.get("all_posts_are_resets") and not any(p.search(post["text"]) for p in patterns):
                continue
            msg = format_message(post, cfg)
            print(f"[match] {post['link']}")
            if dry_run:
                print(msg + "\n")
            else:
                post_discord(cfg["discord_webhook_url"], msg)
                time.sleep(1)

        if first_run:
            state["initialized_sources"].append(key)
            print(f"[info] {key}: initialized ({len(posts)} existing posts recorded)")

    state["seen"] = state["seen"][-MAX_SEEN:]
    if not dry_run:
        save_json(STATE_PATH, state)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop", type=float, metavar="MIN", help="keep running, checking every MIN minutes")
    ap.add_argument("--test", action="store_true", help="send a test message to the webhook")
    ap.add_argument("--dry-run", action="store_true", help="print matches without posting or saving state")
    args = ap.parse_args()

    if not CONFIG_PATH.exists():
        sys.exit("config.json not found. Copy config.example.json to config.json and fill it in.")
    cfg = load_json(CONFIG_PATH, {})
    # Secrets can come from environment variables (e.g. GitHub Actions secrets)
    # so they never have to be written into config.json.
    for env, key in (("DISCORD_WEBHOOK_URL", "discord_webhook_url"), ("X_BEARER_TOKEN", "x_bearer_token")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    if not cfg.get("discord_webhook_url", "").startswith("https://"):
        sys.exit("No Discord webhook set (config.json 'discord_webhook_url' or DISCORD_WEBHOOK_URL).")

    if args.test:
        post_discord(cfg["discord_webhook_url"], "Claude Reset Watch is connected to this channel.")
        print("Test message sent.")
        return

    while True:
        check_once(cfg, dry_run=args.dry_run)
        if not args.loop:
            break
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    main()
