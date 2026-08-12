import os
import json
import time
import requests
import feedparser
import base64
import ssl
from datetime import datetime
from google import genai
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

ssl._create_default_https_context = ssl._create_unverified_context


# ==================== CONFIGURATION ====================

# Güvenlik nedeniyle token'ları çevre değişkenlerinden veya lokal config'den okuyoruz
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

GITHUB_REPO = "YusufEmirBircan/yusufEmirBircan.github.io"
NEWS_FILE_PATH = "news.json"

# Local secret override if exists
if os.path.exists("config.json"):
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
            TELEGRAM_BOT_TOKEN = cfg.get("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
            TELEGRAM_CHAT_ID = cfg.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
            GEMINI_API_KEY = cfg.get("GEMINI_API_KEY", GEMINI_API_KEY)
            GITHUB_TOKEN = cfg.get("GITHUB_TOKEN", GITHUB_TOKEN)
    except Exception:
        pass

# RSS Kaynakları (Teknoloji & Gündem)
RSS_FEEDS = [
    "https://webtekno.com/rss.xml",
    "https://shiftdelete.net/feed",
    "https://www.donanimhaber.com/rss/tum/teknoloji.xml",
    "https://feeds.feedburner.com/TechCrunch/",
    "https://www.theverge.com/rss/index.xml"
]

PROCESSED_FILE = "processed_urls.json"
PENDING_NEWS = {}

# Google Gemini Client
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

def load_processed_urls():
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_processed_url(url):
    urls = load_processed_urls()
    urls.add(url)
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(list(urls), f, ensure_ascii=False, indent=2)

def generate_ai_news(original_title, original_summary):
    prompt = f"""
Sana bir teknoloji haberi başlığı ve özeti vereceğim. Bu haberi tamamen özgün, Türkçe, ilgi çekici ve SEO uyumlu bir haber makalesine dönüştür.

Orijinal Başlık: {original_title}
Orijinal İçerik/Özet: {original_summary}

Lütfen cevabını SADECE aşağıdaki JSON formatında ver (başka açıklama veya kod bloğu ekleme):
{{
  "title": "SEO Uyumlu Özgün Türkçe Başlık",
  "summary": "1-2 cümlelik ilgi çekici Türkçe haber özeti",
  "content": "Detaylı, anlaşılır ve özgün Türkçe haber içeriği (2-3 paragraf)"
}}
"""
    try:
        response = gemini_client.interactions.create(
            model="gemini-3.6-flash",
            input=prompt
        )
        output_text = response.output_text.strip()
        if output_text.startswith("```json"):
            output_text = output_text.replace("```json", "", 1)
        if output_text.endswith("```"):
            output_text = output_text[:-3]
        return json.loads(output_text.strip())
    except Exception as e:
        print(f"Gemini API hatası: {e}")
        return {
            "title": original_title,
            "summary": original_summary[:150] + "...",
            "content": original_summary
        }

def push_to_github(news_item):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{NEWS_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    # Mevcut news.json dosyasını çek
    res = requests.get(url, headers=headers)
    sha = None
    existing_news = []
    
    if res.status_code == 200:
        data = res.json()
        sha = data.get("sha")
        content_decoded = base64.b64decode(data.get("content", "")).decode("utf-8")
        try:
            existing_news = json.loads(content_decoded)
        except Exception:
            existing_news = []
    
    # Yeni haberi başa ekle
    existing_news.insert(0, news_item)
    
    # En güncel 30 haberi tut
    existing_news = existing_news[:30]
    
    updated_content = json.dumps(existing_news, ensure_ascii=False, indent=2)
    b64_content = base64.b64encode(updated_content.encode("utf-8")).decode("utf-8")
    
    payload = {
        "message": f"📰 Yeni haber eklendi: {news_item['title'][:30]}...",
        "content": b64_content,
        "branch": "main"
    }
    if sha:
        payload["sha"] = sha
        
    put_res = requests.put(url, headers=headers, json=payload)
    return put_res.status_code in [200, 201]

async def check_rss_and_notify(context: ContextTypes.DEFAULT_TYPE):
    processed = load_processed_urls()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] RSS Kaynakları taranıyor...")
    
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:3]:
                link = entry.get("link", "")
                if not link or link in processed:
                    continue
                
                title = entry.get("title", "Başlıksız")
                summary = entry.get("summary", entry.get("description", ""))
                
                # Görsel bulma
                image_url = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=800&q=80"
                if "media_content" in entry and len(entry.media_content) > 0:
                    image_url = entry.media_content[0].get("url", image_url)
                elif "enclosures" in entry and len(entry.enclosures) > 0:
                    image_url = entry.enclosures[0].get("url", image_url)
                
                # AI ile haberi düzenle
                print(f"Yeni haber bulundu: {title}")
                ai_news = generate_ai_news(title, summary)
                
                news_id = f"news_{int(time.time())}"
                news_data = {
                    "id": news_id,
                    "title": ai_news["title"],
                    "summary": ai_news["summary"],
                    "content": ai_news["content"],
                    "source": feed.feed.get("title", "Teknoloji"),
                    "image": image_url,
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M")
                }
                
                PENDING_NEWS[news_id] = news_data
                save_processed_url(link)
                
                # Telegram mesajı gönder
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Yayınla", callback_query_data=f"publish:{news_id}"),
                        InlineKeyboardButton("❌ Reddet", callback_query_data=f"reject:{news_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                caption = (
                    f"📰 *YENİ HABER ONAYI*\n\n"
                    f"📌 *Başlık:* {news_data['title']}\n\n"
                    f"📝 *Özet:* {news_data['summary']}\n\n"
                    f"🌐 *Kaynak:* {news_data['source']}"
                )
                
                try:
                    await context.bot.send_photo(
                        chat_id=TELEGRAM_CHAT_ID,
                        photo=image_url,
                        caption=caption,
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                except Exception:
                    await context.bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=caption,
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                
                # Her çalışmada en fazla 1 yeni haber işleyelim
                return
                
        except Exception as e:
            print(f"RSS ayrıştırma hatası ({feed_url}): {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    action, news_id = data.split(":", 1)
    
    if action == "publish":
        news_item = PENDING_NEWS.get(news_id)
        if news_item:
            await query.edit_message_caption(caption=f"⏳ *{news_item['title']}*\n\nSitede yayınlanıyor...", parse_mode="Markdown")
            success = push_to_github(news_item)
            if success:
                await query.edit_message_caption(caption=f"✅ *YAYINLANDI!*\n\n*{news_item['title']}*\nSitenizde canlıya alındı.", parse_mode="Markdown")
            else:
                await query.edit_message_caption(caption=f"⚠️ *Yayınlama Hatası:* GitHub'a gönderilemedi.", parse_mode="Markdown")
            del PENDING_NEWS[news_id]
        else:
            await query.edit_message_caption(caption="⚠️ Haber süresi doldu veya bulunamadı.")
            
    elif action == "reject":
        news_item = PENDING_NEWS.get(news_id)
        title = news_item['title'] if news_item else "Haber"
        if news_id in PENDING_NEWS:
            del PENDING_NEWS[news_id]
        await query.edit_message_caption(caption=f"❌ *REDDEDİLDİ:* {title}", parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Haber Onay Botu Aktif! Yeni haberler düştüğünde onayınıza sunulacak.")

import httpx

_old_async_init = httpx.AsyncClient.__init__
def _new_async_init(self, *args, **kwargs):
    kwargs['verify'] = False
    _old_async_init(self, *args, **kwargs)
httpx.AsyncClient.__init__ = _new_async_init

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()


    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Her 60 saniyede bir RSS kontrolü yap
    job_queue = app.job_queue
    job_queue.run_repeating(check_rss_and_notify, interval=60, first=5)
    
    print("[INFO] Haber Botu baslatildi... Dinleniyor...")
    app.run_polling()



if __name__ == "__main__":
    main()

