import requests
import json
import time
import os
import base64
import threading
from bs4 import BeautifulSoup
from flask import Flask

# ==== تنظیمات (از متغیرهای محیطی) ====
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
AI_KEY = os.environ.get("AI_KEY")
AI_URL = os.environ.get("AI_URL", "https://api.gapgpt.app/v1/responses")
AI_MODEL = os.environ.get("AI_MODEL", "gapgpt-qwen-3.6")
IMAGE_URL = os.environ.get("IMAGE_URL", "https://api.gapgpt.app/v1/images/generations")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "flux-1-schnell")

if not TELEGRAM_TOKEN:
    raise SystemExit("❌ متغیر محیطی TELEGRAM_TOKEN تنظیم نشده است.")
if not AI_KEY:
    raise SystemExit("❌ متغیر محیطی AI_KEY تنظیم نشده است.")

API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# مسیر ذخیره‌سازی داده‌ها
DATA_DIR = os.environ.get("DATA_DIR", "/tmp")
os.makedirs(DATA_DIR, exist_ok=True)

MEMORY_FILE = os.path.join(DATA_DIR, "memory.json")
OFFSET_FILE = os.path.join(DATA_DIR, "offset.txt")
IMAGE_FILE = os.path.join(DATA_DIR, "generated-image.jpg")

# ==== حافظه ====
def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_memory(mem):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("خطا در ذخیره حافظه:", e)

def load_offset():
    if os.path.exists(OFFSET_FILE):
        try:
            with open(OFFSET_FILE) as f:
                return int(f.read().strip())
        except Exception:
            return None
    return None

def save_offset(off):
    try:
        with open(OFFSET_FILE, "w") as f:
            f.write(str(off))
    except Exception as e:
        print("خطا در ذخیره offset:", e)

memory = load_memory()
user_mode = {}

# ==== سرچ ====
def search_duckduckgo(query, max_results=5):
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        res = requests.post("https://lite.duckduckgo.com/lite/",
                            data={"q": query}, headers=headers, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")
        results = []
        for tr in soup.find_all("tr"):
            if "sponsored" in " ".join(tr.get("class", [])).lower():
                continue
            link_tag = tr.find("a", class_="result-link")
            if not link_tag:
                continue
            link = link_tag.get("href", "")
            if "duckduckgo.com/y.js" in link:
                continue
            title = link_tag.get_text(strip=True)
            snippet_tag = tr.find("td", class_="result-snippet")
            if not snippet_tag:
                next_tr = tr.find_next_sibling("tr")
                if next_tr:
                    snippet_tag = next_tr.find("td", class_="result-snippet")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
            results.append({"title": title, "link": link, "snippet": snippet})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        print("خطای سرچ:", e)
        return []

# ==== AI ====
def ask_ai(prompt):
    headers = {"Authorization": f"Bearer {AI_KEY}", "Content-Type": "application/json"}
    try:
        res = requests.post(AI_URL, headers=headers,
                            json={"model": AI_MODEL, "input": prompt}, timeout=60)
        data = res.json()
        return data["output"][0]["content"][0]["text"]
    except Exception as e:
        return f"خطا در AI: {e}"

def generate_image(prompt):
    headers = {"Authorization": f"Bearer {AI_KEY}", "Content-Type": "application/json"}
    try:
        res = requests.post(IMAGE_URL, headers=headers,
                            json={"model": IMAGE_MODEL, "prompt": prompt, "size": "1024x1024"},
                            timeout=120)
        item = res.json()["data"][0]
        if "b64_json" in item:
            img = base64.b64decode(item["b64_json"])
        elif "url" in item:
            img = requests.get(item["url"], timeout=60).content
        else:
            return None
        with open(IMAGE_FILE, "wb") as f:
            f.write(img)
        return IMAGE_FILE
    except Exception as e:
        print("خطای تصویر:", e)
        return None

# ==== تلگرام ====
def get_updates(offset=None):
    try:
        res = requests.get(f"{API_URL}/getUpdates",
                           params={"offset": offset, "timeout": 30}, timeout=35)
        return res.json()
    except Exception as e:
        print("خطای getUpdates:", e)
        return {"result": []}

def send_message(chat_id, text, buttons=None):
    payload = {"chat_id": chat_id, "text": text}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        r = requests.post(f"{API_URL}/sendMessage", json=payload, timeout=10)
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        print("خطا در sendMessage:", e)
        return None

def edit_message(chat_id, msg_id, text):
    try:
        requests.post(f"{API_URL}/editMessageText",
                      json={"chat_id": chat_id, "message_id": msg_id, "text": text},
                      timeout=10)
    except Exception:
        pass

def send_photo(chat_id, path, caption=""):
    try:
        with open(path, "rb") as f:
            requests.post(f"{API_URL}/sendPhoto",
                          data={"chat_id": chat_id, "caption": caption},
                          files={"photo": f}, timeout=60)
    except Exception as e:
        print("خطا در sendPhoto:", e)

def answer_callback(cb_id, text=""):
    try:
        requests.post(f"{API_URL}/answerCallbackQuery",
                      json={"callback_query_id": cb_id, "text": text}, timeout=10)
    except Exception:
        pass

# ==== ثانیه‌شمار ====
def start_timer(chat_id, msg_id, stop_event, label="⏱️"):
    seconds = 0
    while not stop_event.is_set():
        time.sleep(1)
        seconds += 1
        if stop_event.is_set():
            break
        edit_message(chat_id, msg_id, f"{label} {seconds} ثانیه گذشته...")

# ==== کیبورد ====
def main_keyboard(mode="chat"):
    s = "✅ " if mode == "search" else ""
    i = "✅ " if mode == "image" else ""
    return [
        [{"text": f"{s}🔍 سرچ با AI", "callback_data": "mode_search"}],
        [{"text": f"{i}🎨 تولید تصویر", "callback_data": "mode_image"}],
        [{"text": "❌ لغو حالت", "callback_data": "mode_chat"}],
    ]

# ==== حلقه اصلی ربات ====
def bot_loop():
    print("ربات روشن شد...")
    offset = load_offset()
    print(f"offset شروع: {offset}")

    while True:
        try:
            data = get_updates(offset)
            if "result" not in data:
                time.sleep(2)
                continue

            for update in data["result"]:
                offset = update["update_id"] + 1
                save_offset(offset)

                # --- دکمه ---
                if "callback_query" in update:
                    cq = update["callback_query"]
                    chat_id = cq["message"]["chat"]["id"]
                    cb_id = cq["id"]
                    cb_data = cq["data"]

                    if cb_data == "mode_search":
                        user_mode[chat_id] = "search"
                        answer_callback(cb_id, "🔍 حالت سرچ فعال شد")
                        send_message(chat_id, "🔍 حالت سرچ فعال شد.", main_keyboard("search"))
                    elif cb_data == "mode_image":
                        user_mode[chat_id] = "image"
                        answer_callback(cb_id, "🎨 حالت تصویر فعال شد")
                        send_message(chat_id, "🎨 توصیف تصویر رو بنویس.", main_keyboard("image"))
                    elif cb_data == "mode_chat":
                        user_mode[chat_id] = "chat"
                        answer_callback(cb_id, "❌ لغو شد")
                        send_message(chat_id, "❌ حالت چت عادی.", main_keyboard("chat"))
                    continue

                # --- پیام متنی ---
                if "message" not in update:
                    continue
                chat_id = update["message"]["chat"]["id"]
                text = update["message"].get("text", "").strip()
                if not text:
                    continue

                mode = user_mode.get(chat_id, "chat")

                # ===== تصویر =====
                if mode == "image":
                    msg_id = send_message(chat_id, "🎨 0 ثانیه گذشته...")
                    stop = threading.Event()
                    if msg_id:
                        threading.Thread(target=start_timer,
                                         args=(chat_id, msg_id, stop, "🎨"),
                                         daemon=True).start()

                    path = generate_image(text)
                    stop.set()

                    if path:
                        edit_message(chat_id, msg_id, "✅ آماده شد!")
                        send_photo(chat_id, path, caption=f"🎨 {text}")
                    else:
                        edit_message(chat_id, msg_id, "❌ نتونستم تصویر بسازم.")
                    continue

                # ===== سرچ =====
                if mode == "search":
                    msg_id = send_message(chat_id, "🔍 0 ثانیه گذشته...")
                    stop = threading.Event()
                    if msg_id:
                        threading.Thread(target=start_timer,
                                         args=(chat_id, msg_id, stop, "🔍"),
                                         daemon=True).start()

                    results = search_duckduckgo(text, max_results=5)
                    if not results:
                        stop.set()
                        edit_message(chat_id, msg_id, "❌ چیزی پیدا نکردم.")
                        continue

                    summary = ""
                    for i, r in enumerate(results, 1):
                        summary += f"{i}. {r['title']}\n{r['snippet']}\n\n"

                    prompt = f"""بر اساس این نتایج جستجو، به سوال کاربر جواب بده.
اگه نتایج کافی نبود، بگو اطلاعات کافی نیست.

نتایج:
{summary}

سوال: {text}

پاسخ فارسی و خلاصه:"""

                    answer = ask_ai(prompt)
                    stop.set()
                    edit_message(chat_id, msg_id, "✅ آماده شد!")
                    send_message(chat_id, answer, main_keyboard("search"))
                    continue

                # ===== چت =====
                key = text.lower()
                if key in memory:
                    send_message(chat_id, memory[key] + "\n\n📚 (از حافظه)",
                                 main_keyboard("chat"))
                else:
                    send_message(chat_id, "🤔 دارم فکر می‌کنم...")
                    answer = ask_ai(text)
                    memory[key] = answer
                    save_memory(memory)
                    send_message(chat_id, answer, main_keyboard("chat"))

        except Exception as e:
            print("خطای کلی:", e)
            time.sleep(2)

# ==== وب‌سرور Flask برای Render ====
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return "Bot is running ✅", 200

# ==== اجرا ====
if __name__ == "__main__":
    # ربات در thread جداگانه
    threading.Thread(target=bot_loop, daemon=True).start()

    # وب‌سرور روی پورت Render
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)
