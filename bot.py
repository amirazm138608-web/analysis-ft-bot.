"""Analysis FT bot: live football results + nightly digest of the channel's headlines."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ---------------- Settings (change here if needed) ----------------
LEAGUES = {
    39: "لیگ برتر انگلیس",
    140: "لالیگا",
    2: "لیگ قهرمانان اروپا",
    135: "سری آ",
}
TIMEZONE = "Asia/Tehran"   # time zone used for the nightly digest
DIGEST_HOUR = 23           # nightly digest time (hour)
DIGEST_MINUTE = 0          # nightly digest time (minute)
DAILY_LIMIT = 95           # max API-Football requests per day (free plan: 100)
STATE_FILE = "state.json"
# -------------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
CHANNEL = os.environ.get("CHANNEL", "").strip()

FINISHED = {"FT", "AET", "PEN"}
DEAD = {"PST", "CANC", "ABD", "AWD", "WO"}

DEFAULT_STATE = {
    "offset": 0,
    "posts": [],
    "sent_ids": [],
    "schedule": {},
    "live": {},
    "digest_date": "",
    "usage": {},
    "last_poll": 0,
}
STATE = {}


# ---------------- Helpers ----------------
def http_json(url, data=None, headers=None):
    h = {"User-Agent": "analysis-ft-bot"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"HTTP {e.code}: {body}") from None


def tg(method, **params):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    return http_json(url, data=urllib.parse.urlencode(params).encode())


def send(text):
    res = tg("sendMessage", chat_id=CHANNEL, text=text)
    mid = res["result"]["message_id"]
    STATE["sent_ids"] = (STATE["sent_ids"] + [mid])[-500:]


def api(path):
    """Call API-Football. Returns None if the daily limit is reached or on error."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage = STATE["usage"]
    if usage.get("date") != today:
        usage["date"] = today
        usage["n"] = 0
    if usage["n"] >= DAILY_LIMIT:
        print("Daily API limit reached, skipping.")
        return None
    usage["n"] += 1
    data = http_json(
        "https://v3.football.api-sports.io" + path,
        headers={"x-apisports-key": API_KEY},
    )
    if data.get("errors"):
        print("API-Football error:", data["errors"])
        return None
    return data


def split_text(text, limit=4000):
    chunks, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur.rstrip("\n"))
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur.rstrip("\n"))
    return chunks


# ---------------- State ----------------
def load_state():
    state = json.loads(json.dumps(DEFAULT_STATE))
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            state.update(json.load(f))
    return state


def save_state(before):
    after = json.dumps(STATE, ensure_ascii=False, indent=1, sort_keys=True)
    if after != before:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(after)


# ---------------- Channel posts + nightly digest ----------------
def same_channel(chat):
    if CHANNEL.startswith("@"):
        return (chat.get("username") or "").lower() == CHANNEL[1:].lower()
    return str(chat.get("id")) == CHANNEL


def collect_posts(now_utc, now_local):
    res = tg(
        "getUpdates",
        offset=STATE["offset"],
        timeout=0,
        allowed_updates=json.dumps(["channel_post"]),
    )
    for u in res.get("result", []):
        STATE["offset"] = u["update_id"] + 1
        m = u.get("channel_post")
        if not m or not same_channel(m.get("chat", {})):
            continue
        if m.get("message_id") in STATE["sent_ids"]:
            continue
        text = (m.get("text") or m.get("caption") or "").strip()
        if text:
            STATE["posts"].append(text)


def headline(text):
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:120] + ("…" if len(line) > 120 else "")
    return ""


def digest(now_utc, now_local):
    today = now_local.strftime("%Y-%m-%d")
    due = (now_local.hour, now_local.minute) >= (DIGEST_HOUR, DIGEST_MINUTE)
    if not due or STATE["digest_date"] == today:
        return
    heads = [h for h in (headline(p) for p in STATE["posts"]) if h]
    if heads:
        lines = ["📰 خلاصه‌ی خبرهای امروز", ""]
        lines += [f"{i}. {h}" for i, h in enumerate(heads, 1)]
        for chunk in split_text("\n".join(lines)):
            send(chunk)
    STATE["posts"] = []
    STATE["digest_date"] = today


# ---------------- Live results ----------------
def refresh_schedule(now_utc):
    today = now_utc.strftime("%Y-%m-%d")
    if STATE["schedule"].get("date") == today:
        return
    data = api(f"/fixtures?date={today}")
    if data is None:
        return
    matches = []
    for f in data.get("response", []):
        if f["league"]["id"] in LEAGUES:
            status = f["fixture"]["status"]["short"]
            matches.append({
                "id": f["fixture"]["id"],
                "kickoff": f["fixture"]["timestamp"],
                "done": status in FINISHED or status in DEAD,
            })
    STATE["schedule"] = {"date": today, "matches": matches}


def should_poll(now):
    if STATE["live"]:
        return True
    for m in STATE["schedule"].get("matches", []):
        if not m["done"] and m["kickoff"] - 300 <= now <= m["kickoff"] + 4 * 3600:
            return True
    return False


def mark_done(fid):
    for m in STATE["schedule"].get("matches", []):
        if str(m["id"]) == str(fid):
            m["done"] = True


def score_line(m):
    return f"{m['home']} {m['h']} - {m['a']} {m['away']}"


def finish(fid, old):
    data = api(f"/fixtures?id={fid}")
    if data is None or not data.get("response"):
        return
    fx = data["response"][0]
    status = fx["fixture"]["status"]["short"]
    if status in FINISHED:
        old["h"] = fx["goals"]["home"] or 0
        old["a"] = fx["goals"]["away"] or 0
        text = f"🏁 پایان بازی\n\n{score_line(old)}"
        if status == "PEN":
            pen = fx["score"]["penalty"]
            text += f"\nضربات پنالتی: {pen['home']} - {pen['away']}"
        text += f"\n🏆 {old['league']}"
        send(text)
        del STATE["live"][fid]
        mark_done(fid)
    elif status in DEAD:
        del STATE["live"][fid]
        mark_done(fid)
    # otherwise still in progress: keep it and check again later


def poll_live(now):
    # Drop matches stuck in "live" for too long (safety net)
    for fid in [k for k, v in STATE["live"].items() if now - v.get("since", now) > 5 * 3600]:
        del STATE["live"][fid]

    ids = "-".join(str(i) for i in LEAGUES)
    data = api(f"/fixtures?live={ids}")
    if data is None:
        return
    current = {}
    for f in data.get("response", []):
        current[str(f["fixture"]["id"])] = {
            "home": f["teams"]["home"]["name"],
            "away": f["teams"]["away"]["name"],
            "h": f["goals"]["home"] or 0,
            "a": f["goals"]["away"] or 0,
            "league": LEAGUES.get(f["league"]["id"], f["league"]["name"]),
            "minute": f["fixture"]["status"]["elapsed"],
        }

    for fid, m in current.items():
        old = STATE["live"].get(fid)
        if old is None:
            if m["h"] == 0 and m["a"] == 0 and (m["minute"] or 0) <= 10:
                send(f"🟢 شروع بازی\n\n{m['home']} - {m['away']}\n🏆 {m['league']}")
            else:
                send(f"🔴 بازی در جریان\n\n{score_line(m)}\n⏱ دقیقه {m['minute']}\n🏆 {m['league']}")
            m["since"] = now
        else:
            m["since"] = old.get("since", now)
            if (m["h"], m["a"]) != (old["h"], old["a"]):
                title = "⚽ گل!" if m["h"] + m["a"] > old["h"] + old["a"] else "🔄 اصلاح نتیجه"
                minute = f"\n⏱ دقیقه {m['minute']}" if m["minute"] else ""
                send(f"{title}\n\n{score_line(m)}{minute}\n🏆 {m['league']}")
        STATE["live"][fid] = m

    for fid in [k for k in STATE["live"] if k not in current]:
        finish(fid, STATE["live"][fid])


def football(now_utc, now_local):
    refresh_schedule(now_utc)
    now = now_utc.timestamp()
    if not should_poll(now):
        return
    n_matches = len(STATE["schedule"].get("matches", []))
    gap = 300 if n_matches <= 6 else 540   # busy days: check less often to save requests
    if now - STATE["last_poll"] < gap - 60:
        return
    STATE["last_poll"] = now
    poll_live(now)


# ---------------- Main ----------------
def main():
    global STATE
    STATE = load_state()
    before = json.dumps(STATE, ensure_ascii=False, indent=1, sort_keys=True)

    if os.environ.get("TEST") == "true":
        send("✅ بات فعال شد و می‌تونه توی چنل پست بذاره.")
        save_state(before)
        return

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(ZoneInfo(TIMEZONE))
    for step in (collect_posts, digest, football):
        try:
            step(now_utc, now_local)
        except Exception as e:  # keep going so state is still saved
            print(f"{step.__name__} failed: {e}")
    save_state(before)


if __name__ == "__main__":
    main()
