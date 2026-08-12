import os
import json
import time
import requests
import feedparser
import base64
import ssl
from datetime import datetime
from google import genai
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, ConversationHandler

ssl._create_default_https_context = ssl._create_unverified_context


# ==================== CONFIGURATION ====================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

GITHUB_REPO = "YusufEmirBircan/yusufEmirBircan.github.io"
NEWS_FILE_PATH = "news.json"

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

RSS_FEEDS = [
    "https://webtekno.com/rss.xml",
    "https://shiftdelete.net/feed",
    "https://www.donanimhaber.com/rss/tum/teknoloji.xml",
    "https://feeds.feedburner.com/TechCrunch/",
    "https://www.theverge.com/rss/index.xml"
]

PROCESSED_FILE = "processed_urls.json"
PENDING_NEWS = {}

TITLE, SUMMARY, CONTENT, IMAGE = range(4)
EDIT_TITLE, EDIT_SUMMARY, EDIT_CONTENT, EDIT_IMAGE = range(4, 8)

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

def fetch_news_from_github():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{NEWS_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    res = requests.get(url, headers=headers, verify=False)
    if res.status_code == 200:
        data = res.json()
        sha = data.get("sha")
        content_decoded = base64.b64decode(data.get("content", "")).decode("utf-8")
        try:
            return json.loads(content_decoded), sha
        except Exception:
            return [], sha
    return [], None

def push_news_list_to_github(news_list, sha, commit_msg):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{NEWS_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    # En güncel 30 haberi tut
    news_list = news_list[:30]
    updated_content = json.dumps(news_list, ensure_ascii=False, indent=2)
    b64_content = base64.b64encode(updated_content.encode("utf-8")).decode("utf-8")
    payload = {
        "message": commit_msg,
        "content": b64_content,
        "branch": "main"
    }
    if sha:
        payload["sha"] = sha
    put_res = requests.put(url, headers=headers, json=payload, verify=False)
    return put_res.status_code in [200, 201]

def push_to_github(news_item):
    news_list, sha = fetch_news_from_github()
    news_list.insert(0, news_item)
    return push_news_list_to_github(news_list, sha, f"📰 Yeni haber eklendi: {news_item['title'][:30]}...")

def delete_news_by_id(news_id):
    news_list, sha = fetch_news_from_github()
    if not sha: return False
    initial_len = len(news_list)
    news_list = [n for n in news_list if n.get("id") != news_id]
    if len(news_list) == initial_len: return False
    return push_news_list_to_github(news_list, sha, f"🗑 Haber silindi")

def update_news_by_id(news_item):
    news_list, sha = fetch_news_from_github()
    if not sha: return False
    for i, n in enumerate(news_list):
        if n.get("id") == news_item.get("id"):
            news_list[i] = news_item
            return push_news_list_to_github(news_list, sha, f"✏️ Haber güncellendi: {news_item['title'][:30]}...")
    return False

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
                
                image_url = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=800&q=80"
                if "media_content" in entry and len(entry.media_content) > 0:
                    image_url = entry.media_content[0].get("url", image_url)
                elif "enclosures" in entry and len(entry.enclosures) > 0:
                    image_url = entry.enclosures[0].get("url", image_url)
                
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
        
    elif action == "delete":
        await query.edit_message_text(text=f"⏳ Haber siliniyor...")
        success = delete_news_by_id(news_id)
        if success:
            await query.edit_message_text(text=f"✅ Haber başarıyla silindi.")
        else:
            await query.edit_message_text(text=f"⚠️ Silme işlemi başarısız veya haber bulunamadı.")

async def list_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(TELEGRAM_CHAT_ID): return
    await update.message.reply_text("⏳ Sitenizdeki haberler getiriliyor...")
    news_list, _ = fetch_news_from_github()
    if not news_list:
        await update.message.reply_text("Sitenizde henüz haber bulunmuyor.")
        return
        
    for item in news_list[:5]:
        keyboard = [
            [
                InlineKeyboardButton("✏️ Düzenle", callback_data=f"edit:{item['id']}"),
                InlineKeyboardButton("🗑 Sil", callback_data=f"delete:{item['id']}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = f"📌 *{item['title']}*\n_{item['date']}_\n\n{item['summary'][:100]}..."
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

async def start_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if str(query.from_user.id) != str(TELEGRAM_CHAT_ID):
        await query.answer("⛔ Yetkiniz yok!", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    
    action, news_id = query.data.split(":", 1)
    news_list, _ = fetch_news_from_github()
    news_item = next((n for n in news_list if n.get("id") == news_id), None)
    
    if not news_item:
        await query.edit_message_text(text="⚠️ Bu haber bulunamadı.")
        return ConversationHandler.END
        
    context.user_data['edit_news'] = news_item
    
    await context.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"✏️ *Haber Düzenleme Modu*\n\nMevcut Başlık: {news_item['title']}\n\nLütfen yeni başlığı yazın (Değiştirmemek için sadece `gec` yazın):",
        parse_mode="Markdown"
    )
    return EDIT_TITLE

async def edit_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.lower() != 'gec':
        context.user_data['edit_news']['title'] = text
    
    await update.message.reply_text(
        f"Mevcut Özet: {context.user_data['edit_news']['summary']}\n\nLütfen yeni özeti yazın (Değiştirmemek için sadece `gec` yazın):",
        parse_mode="Markdown"
    )
    return EDIT_SUMMARY

async def edit_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.lower() != 'gec':
        context.user_data['edit_news']['summary'] = text
    
    await update.message.reply_text(
        f"Lütfen yeni içeriği yazın (Değiştirmemek için sadece `gec` yazın):",
        parse_mode="Markdown"
    )
    return EDIT_CONTENT

async def edit_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.lower() != 'gec':
        context.user_data['edit_news']['content'] = text
    
    await update.message.reply_text(
        f"Lütfen yeni görsel linkini yazın (Değiştirmemek için sadece `gec` yazın):",
        parse_mode="Markdown"
    )
    return EDIT_IMAGE

async def edit_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.lower() != 'gec':
        context.user_data['edit_news']['image'] = text
        
    news_item = context.user_data['edit_news']
    await update.message.reply_text("⏳ Haber güncelleniyor...")
    success = update_news_by_id(news_item)
    
    if success:
        await update.message.reply_text("✅ Haber başarıyla güncellendi!")
    else:
        await update.message.reply_text("⚠️ Güncelleme işlemi başarısız oldu.")
        
    return ConversationHandler.END

async def add_news_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(TELEGRAM_CHAT_ID):
        return ConversationHandler.END
    context.user_data['manual_news'] = {}
    await update.message.reply_text("Yeni haber ekleme işlemine başladık.\n\nLütfen haberin **BAŞLIĞINI** yazın (İptal için /iptal yazın):", parse_mode="Markdown")
    return TITLE

async def ask_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['manual_news']['title'] = update.message.text
    await update.message.reply_text("Harika. Şimdi lütfen haberin **ÖZETİNİ** yazın:", parse_mode="Markdown")
    return SUMMARY

async def ask_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['manual_news']['summary'] = update.message.text
    await update.message.reply_text("Güzel. Şimdi lütfen haberin **İÇERİĞİNİ** yazın:", parse_mode="Markdown")
    return CONTENT

async def ask_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['manual_news']['content'] = update.message.text
    await update.message.reply_text("Son olarak, haber için bir **görsel linki** gönderin.\nEğer görsel eklemek istemiyorsanız sadece `gec` yazabilirsiniz.", parse_mode="Markdown")
    return IMAGE

async def ask_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text and text.lower() == 'gec':
        image_url = "https://images.unsplash.com/photo-1585829365295-ab7cd400c167?auto=format&fit=crop&w=800&q=80"
    else:
        image_url = text

    context.user_data['manual_news']['image'] = image_url
    
    news_id = f"news_manual_{int(time.time())}"
    news_data = {
        "id": news_id,
        "title": context.user_data['manual_news']['title'],
        "summary": context.user_data['manual_news']['summary'],
        "content": context.user_data['manual_news']['content'],
        "source": "Özel İçerik",
        "image": image_url,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    
    PENDING_NEWS[news_id] = news_data
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Yayınla", callback_data=f"publish:{news_id}"),
            InlineKeyboardButton("❌ Reddet", callback_data=f"reject:{news_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    caption = (
        f"📰 *YENİ MANUEL HABER ONAYI*\n\n"
        f"📌 *Başlık:* {news_data['title']}\n\n"
        f"📝 *Özet:* {news_data['summary']}\n\n"
        f"🌐 *Kaynak:* {news_data['source']}"
    )
    
    try:
        await context.bot.send_photo(
            chat_id=TELEGRAM_CHAT_ID,
            photo=news_data["image"],
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
        
    return ConversationHandler.END

async def cancel_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("İşlem iptal edildi.")
    return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(TELEGRAM_CHAT_ID):
        await update.message.reply_text("⛔ Üzgünüm, bu bot kişiye özeldir. Erişim yetkiniz bulunmamaktadır.")
        return
    await update.message.reply_text("👋 Haber Onay Botu Aktif! Yeni haberler düştüğünde onayınıza sunulacak.\nKomutları menüden görebilirsiniz.")

async def post_init(application: Application):
    commands = [
        BotCommand("start", "Botu başlatır"),
        BotCommand("haberler", "Sitedeki haberleri listele ve yönet"),
        BotCommand("haberekle", "Adım adım manuel haber ekle"),
        BotCommand("iptal", "Devam eden işlemi iptal et")
    ]
    await application.bot.set_my_commands(commands)

import httpx
_old_async_init = httpx.AsyncClient.__init__
def _new_async_init(self, *args, **kwargs):
    kwargs['verify'] = False
    _old_async_init(self, *args, **kwargs)
httpx.AsyncClient.__init__ = _new_async_init

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("haberler", list_news))
    
    edit_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_edit, pattern="^edit:")],
        states={
            EDIT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_title)],
            EDIT_SUMMARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_summary)],
            EDIT_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_content)],
            EDIT_IMAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_image)],
        },
        fallbacks=[CommandHandler("iptal", cancel_news)]
    )
    app.add_handler(edit_conv_handler)
    
    add_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("haberekle", add_news_start)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_title)],
            SUMMARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_summary)],
            CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_content)],
            IMAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_image)],
        },
        fallbacks=[CommandHandler("iptal", cancel_news)]
    )
    app.add_handler(add_conv_handler)
    
    app.add_handler(CallbackQueryHandler(button_handler))
    
    job_queue = app.job_queue
    job_queue.run_repeating(check_rss_and_notify, interval=3600, first=5)
    
    print("[INFO] Haber Botu baslatildi... Dinleniyor...")
    app.run_polling()

if __name__ == "__main__":
    main()
