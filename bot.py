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

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

GITHUB_REPO = "YusufEmirBircan/yusufEmirBircan.github.io"
NEWS_FILE_PATH = "news.json"
PROCESSED_FILE = "processed_urls.json"
PENDING_FILE = "pending_news.json"

# Local secret override
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

# RSS Kaynakları
RSS_FEEDS = [
    "https://webtekno.com/rss.xml",
    "https://shiftdelete.net/feed",
    "https://www.donanimhaber.com/rss/tum/teknoloji.xml",
    "https://feeds.feedburner.com/TechCrunch/",
    "https://www.theverge.com/rss/index.xml"
]

# Google Gemini Client
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ==================== YARDIMCI FONKSİYONLAR ====================

def load_json_file(filename, default_val):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default_val
    return default_val

def save_json_file(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_processed_urls():
    return set(load_json_file(PROCESSED_FILE, []))

def save_processed_url(url):
    urls = load_processed_urls()
    urls.add(url)
    save_json_file(PROCESSED_FILE, list(urls))

def load_pending_news():
    return load_json_file(PENDING_FILE, {})

def save_pending_news(data):
    save_json_file(PENDING_FILE, data)

# ==================== GEMINI AI İÇERİK ÜRETİMİ ====================

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
        # Kesin çalışan ve kota dostu model
        response = gemini_client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )
        output_text = response.text.strip()
        
        if output_text.startswith("```json"):
            output_text = output_text.replace("```json", "", 1)
        if output_text.endswith("```"):
            output_text = output_text[:-3]
            
        return json.loads(output_text.strip())
    except Exception as e:
        print(f"[UYARI] Gemini API hatası ({e}). Orijinal haber kullanılıyor.")
        return {
            "title": original_title,
            "summary": original_summary[:150] + "..." if len(original_summary) > 150 else original_summary,
            "content": original_summary
        }

# ==================== GITHUB YAYINLAMA ====================

def push_to_github(news_item):
    url = f"[https://api.github.com/repos/](https://api.github.com/repos/){GITHUB_REPO}/contents/{NEWS_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
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
    
    existing_news.insert(0, news_item)
    existing_news = existing_news[:30] # En güncel 30 haber
    
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

# ==================== BOT İŞLEMLERİ ====================

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
                
                image_url = "[https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=800&q=80](https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=800&q=80)"
                if "media_content" in entry and len(entry.media_content) > 0:
                    image_url = entry.media_content[0].get("url", image_url)
                elif "enclosures" in entry and len(entry.enclosures) > 0:
                    image_url = entry.enclosures[0].get("url", image_url)
                
                print(f"🚀 Yeni haber bulundu: {title}")
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
                
                pending = load_pending_news()
                pending[news_id] = news_data
                save_pending_news(pending)
                
                save_processed_url(link)
                
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Yayınla", callback_data=f"publish:{news_id}"),
                        InlineKeyboardButton("❌ Reddet", callback_data=f"reject:{news_id}")
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
                
                return
                
        except Exception as e:
            print(f"RSS ayrıştırma hatası ({feed_url}): {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if str(query.from_user.id) != str(TELEGRAM_CHAT_ID):
        await query.answer("⛔ Bu botu kullanma yetkiniz yok!", show_alert=True)
        return
        
    await query.answer()
    
    data = query.data
    action, news_id = data.split(":", 1)
    
    pending = load_pending_news()
    news_item = pending.get(news_id)
    
    if action == "publish":
        if news_item:
            await query.edit_message_caption(caption=f"⏳ *{news_item['title']}*\n\nGitHub'a gönderiliyor...", parse_mode="Markdown")
            success = push_to_github(news_item)
            if success:
                await query.edit_message_caption(caption=f"✅ *YAYINLANDI!*\n\n*{news_item['title']}*\nSitenizde canlıya alındı.", parse_mode="Markdown")
            else:
                await query.edit_message_caption(caption=f"⚠️ *Yayınlama Hatası:* GitHub'a gönderilemedi.", parse_mode="Markdown")
            
            del pending[news_id]
            save_pending_news(pending)
        else:
            await query.edit_message_caption(caption="⚠️ Haber süresi doldu veya bulunamadı.")
            
    elif action == "reject":
        title = news_item['title'] if news_item else "Haber"
        if news_id in pending:
            del pending[news_id]
            save_pending_news(pending)
        await query.edit_message_caption(caption=f"❌ *REDDEDİLDİ:* {title}", parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(TELEGRAM_CHAT_ID):
        await update.message.reply_text("⛔ Üzgünüm, bu bot kişiye özeldir.")
        return
    await update.message.reply_text("👋 Haber Onay Botu Aktif! Yeni haberler düştüğünde onayınıza sunulacak.")

# SSL bypass (gerekirse)
import httpx
_old_async_init = httpx.AsyncClient.__init__
def _new_async_init(self, *args, **kwargs):
    kwargs['verify'] = False
    _old_async_init(self, *args, **kwargs)
httpx.AsyncClient.__init__ = _new_async_init


# ==================== MAIN BAŞLANGIÇ NOKTASI ====================

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # SAATLİK KONTROL AYARI: interval=3600 yapıldı (1 Saat = 3600 saniye)
    job_queue = app.job_queue
    job_queue.run_repeating(check_rss_and_notify, interval=3600, first=5)
    
    print("[INFO] Haber Botu baslatildi... Dinleniyor...")
    app.run_polling()

if __name__ == "__main__":
    main()
