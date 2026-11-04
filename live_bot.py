import os
import time
import html
import requests
from datetime import datetime, timezone
# ---------- تنظیمات ----------
API_KEY = os.environ["API_FOOTBALL_KEY"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ.get("CHANNEL_ID") or "@AnalysisFT"
# لیگ‌ها: لیگ برتر، لالیگا، سری آ، لیگ قهرمانان اروپا
LEAGUES = {
    39: "لیگ برتر انگلیس",
    140: "لالیگا",
    135: "سری آ",
    2: "لیگ قهرمانان اروپا",
}
# نام تیم‌ها به فارسی (اگه تیمی اینجا نبود، اسم انگلیسی نمایش داده می‌شه)
# اگه بات قبلی لیست اسم فارسی داشت، می‌تونی همون رو اینجا اضافه کنی
TEAMS = {
    "Bayern München": "بایرن مونیخ",
    "Borussia Dortmund": "دورتموند",
    "Real Madrid": "رئال مادرید",
    "Barcelona": "بارسلونا",
    "Atletico Madrid": "اتلتیکو مادرید",
    "Sevilla": "سویا",
    "Liverpool": "لیورپول",
    "Arsenal": "آرسنال",
    "Chelsea": "چلسی",
    "Tottenham": "تاتنهام",
    "Newcastle": "نیوکاسل",
    "Aston Villa": "استون‌ویلا",
    "Paris Saint Germain": "پاری‌سن‌ژرمن",
    "Inter": "اینتر",
    "AC Milan": "میلان",
    "Juventus": "یوونتوس",
    "Napoli": "ناپولی",
    "Roma": "رم",
    "Lazio": "لاتزیو",
    "Atalanta": "آتالانتا",
    "Bayer Leverkusen": "لورکوزن",
    "Benfica": "بنفیکا",
    "Porto": "پورتو",
    "Sporting CP": "اسپورتینگ",
    "Ajax": "آژاکس",
}
FOOTER = "\n\nبا ما همواره فوتبالی باش👇😉\n🆔 : @AnalysisFT  |  #BOT ✔️"
POLL_SECONDS = 240          # فاصله چک کردن در زمان بازی (به‌خاطر سهمیه ۱۰۰ درخواست در روز)
MAX_RUN_SECONDS = 5 * 3600 + 30 * 60   # گیت‌هاب بعد از ۶ ساعت قطع می‌کنه
# -----------------------------
BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
DONE = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
FINISHED_OK = {"FT", "AET", "PEN"}
START = time.time()
remaining = None   # سهمیه باقی‌مانده امروز
state = {}         # وضعیت هر بازی
kick = {}          # ساعت شروع هر بازی (timestamp)
def fa(x):
    return str(x).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
def api(path, **params):
    global remaining
    r = requests.get(BASE + path, headers=HEADERS, params=params, timeout=30)
    rem = r.headers.get("x-ratelimit-requests-remaining")
    if rem is not None:
        try:
            remaining = int(rem)
        except ValueError:
            pass
    r.raise_for_status()
    data = r.json().get("response", [])
    return data if isinstance(data, list) else []
def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHANNEL,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
        timeout=30,
    )
    if not r.ok:
        print("Telegram error:", r.text)
def goal_lines(events):
    lines = []
    for e in events or []:
        detail = e.get("detail") or ""
        if e.get("type") != "Goal" or detail == "Missed Penalty":
            continue
        t = e.get("time") or {}
        minute = str(t.get("elapsed", ""))
        if t.get("extra"):
            minute += f"+{t['extra']}"
        tag = ""
        if detail == "Penalty":
            tag = " (پنالتی)"
        elif detail == "Own Goal":
            tag = " (به‌خودی)"
        name = html.escape((e.get("player") or {}).get("name") or "")
        lines.append(f"⚽️ {name}{tag} {fa(minute)}'")
    return lines
def minute_label(fx):
    s = fx["fixture"]["status"]
    el = s.get("elapsed")
    if el is None:
        return ""
    label = fa(el)
    if s.get("extra"):
        label += "+" + fa(s["extra"])
    return label + "'"
def build(fx, label, events):
    league = LEAGUES[fx["league"]["id"]]
    home = html.escape(TEAMS.get(fx["teams"]["home"]["name"], fx["teams"]["home"]["name"]))
    away = html.escape(TEAMS.get(fx["teams"]["away"]["name"], fx["teams"]["away"]["name"]))
    gh = fx["goals"]["home"] or 0
    ga = fx["goals"]["away"] or 0
    text = f"🔴 {league}\n<blockquote>{label}</blockquote>\n🔹️{home} {fa(gh)} - {fa(ga)} {away}"
    lines = goal_lines(events)
    if lines:
        text += "\n<blockquote>" + "\n".join(lines) + "</blockquote>"
    return text + FOOTER
def process(fx):
    fid = fx["fixture"]["id"]
    st = fx["fixture"]["status"]["short"]
    score = (fx["goals"]["home"] or 0, fx["goals"]["away"] or 0)
    prev = state.get(fid)
    state[fid] = {"status": st, "score": score}
    kick.setdefault(fid, fx["fixture"]["timestamp"])
    if prev is None:
        return  # بار اول فقط ثبت می‌کنیم، پیام نمی‌دیم
    ps = prev["status"]
    events = fx.get("events")
    need_events = score != prev["score"] or st in FINISHED_OK or st == "HT"
    if events is None and need_events:
        events = api("/fixtures/events", fixture=fid)
    # شروع بازی
    if ps not in LIVE and ps not in DONE and st in LIVE:
        send(build(fx, "شروع بازی", None))
    # پایان بازی
    if st in FINISHED_OK and ps not in DONE:
        send(build(fx, "پایان بازی", events))
        return
    # پایان نیمه اول
    if st == "HT" and ps != "HT":
        send(build(fx, "پایان نیمه اول", events))
        return
    # گل
    if score != prev["score"] and st in LIVE:
        send(build(fx, minute_label(fx), events))
def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day = [f for f in api("/fixtures", date=today) if f["league"]["id"] in LEAGUES]
    for f in day:
        fid = f["fixture"]["id"]
        state[fid] = {
            "status": f["fixture"]["status"]["short"],
            "score": (f["goals"]["home"] or 0, f["goals"]["away"] or 0),
        }
        kick[fid] = f["fixture"]["timestamp"]
    print(f"بازی‌های امروز در لیگ‌های انتخابی: {len(day)}")
    while time.time() - START < MAX_RUN_SECONDS:
        now = time.time()
        pending = [
            i for i, s in state.items()
            if s["status"] not in DONE and now < kick[i] + 4 * 3600
        ]
        if not pending:
            print("بازی‌ای باقی نمونده، خاموش می‌شم.")
            return
        live_now = any(state[i]["status"] in LIVE for i in pending)
        if not live_now:
            nxt = min(kick[i] for i in pending)
            wait = nxt - now - 300  # ۵ دقیقه قبل از شروع بیدار می‌شیم
            if wait > 0:
                left = MAX_RUN_SECONDS - (time.time() - START)
                time.sleep(max(1, min(wait, 600, left)))
                continue
        try:
            seen = set()
            for fx in api("/fixtures", live="all"):
                if fx["league"]["id"] not in LEAGUES:
                    continue
                seen.add(fx["fixture"]["id"])
                process(fx)
            # بازی‌ای که زنده بود و از لیست زنده‌ها رفت یعنی تموم شده
            for i in pending:
                if state[i]["status"] in LIVE and i not in seen:
                    res = api("/fixtures", id=i)
                    if res:
                        process(res[0])
        except Exception as e:
            print("خطا:", e)
        pause = POLL_SECONDS
        if remaining is not None:
            print("سهمیه باقی‌مانده امروز:", remaining)
            if remaining <= 3:
                print("سهمیه تموم شد، خاموش می‌شم.")
                return
            if remaining < 25:
                pause = 360
        time.sleep(pause)
if __name__ == "__main__":
    main()
