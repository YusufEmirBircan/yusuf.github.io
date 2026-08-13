import os
import asyncio
import json
import time
import requests
import re
import feedparser
import base64
import ssl
from datetime import datetime, timezone, timedelta
TR_TZ = timezone(timedelta(hours=3))
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, ConversationHandler

import ssl
import urllib.request

# feedparser için SSL doğrulamasını devre dışı bırak (RSS siteleri için gerekli)
_rss_ssl_ctx = ssl.create_default_context()
_rss_ssl_ctx.check_hostname = False
_rss_ssl_ctx.verify_mode = ssl.CERT_NONE


# ==================== CONFIGURATION ====================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

GITHUB_REPO = "YusufEmirBircan/yusufEmirBircan.github.io"
NEWS_FILE_PATH = "news.json"
AUTH_FILE = "authorized_users.json"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123456")

if os.path.exists("config.json"):
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
            TELEGRAM_BOT_TOKEN = cfg.get("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
            TELEGRAM_CHAT_ID = cfg.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
            GEMINI_API_KEY = cfg.get("GEMINI_API_KEY", GEMINI_API_KEY)
            GITHUB_TOKEN = cfg.get("GITHUB_TOKEN", GITHUB_TOKEN)
            ADMIN_PASSWORD = cfg.get("ADMIN_PASSWORD", ADMIN_PASSWORD)
    except Exception:
        pass

RSS_FEEDS = [
    "https://webtekno.com/rss.xml",
    "https://feeds.feedburner.com/TechCrunch/",
    "https://www.theverge.com/rss/index.xml",
    "https://www.chip.com.tr/rss",
    "https://www.wired.com/feed/rss"
]

PROCESSED_FILE = "processed_urls.json"
PENDING_NEWS = {}

TITLE, SUMMARY, CONTENT, SOURCE, CATEGORY, IMAGE = range(6)
EDIT_TITLE, EDIT_SUMMARY, EDIT_CONTENT, EDIT_IMAGE = range(6, 10)

def load_processed_urls():
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def load_auth_users():
    if os.path.exists(AUTH_FILE):
        try:
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_auth_user(user_id):
    users = load_auth_users()
    users.add(str(user_id))
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(list(users), f)

def is_authorized(user_id):
    if str(user_id) == str(TELEGRAM_CHAT_ID):
        return True
    return str(user_id) in load_auth_users()

def save_processed_url(url):
    urls = load_processed_urls()
    urls.add(url)
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(list(urls), f, ensure_ascii=False, indent=2)

# NOT: Yapay zeka yeniden yazma özelliği devre dışı.
# RSS'ten gelen orijinal içerik doğrudan kullanılır.

def fetch_news_from_github():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{NEWS_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    res = requests.get(url, headers=headers)
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
    put_res = requests.put(url, headers=headers, json=payload)
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
    now_str = datetime.now(TR_TZ).strftime('%H:%M:%S')
    print(f"[{now_str}] RSS Kaynakları taranıyor...")
    
    found_count = 0  # Bu taramada bulunan yeni haber sayısı

    for feed_url in RSS_FEEDS:
        try:
            # SSL sorunu yaşayan RSS siteleri için özel handler kullan
            handler = urllib.request.HTTPSHandler(context=_rss_ssl_ctx)
            opener = urllib.request.build_opener(handler)
            opener.addheaders = [('User-Agent', 'Mozilla/5.0 (compatible; RSSBot/1.0)')]
            response = opener.open(feed_url, timeout=15)
            raw_content = response.read()
            feed = feedparser.parse(raw_content)
            feed.feed['link'] = feed_url  # kaynak linki koru

            for entry in feed.entries[:10]:  # Her feed'den en fazla 10 entry incele
                link = entry.get("link", "")
                if not link or link in processed:
                    continue

                title = entry.get("title", "Başlıksız")
                raw_summary = entry.get("summary", entry.get("description", ""))
                # HTML etiketlerini temizle
                summary = re.sub(r'<[^>]+>', '', raw_summary).strip()
                # Ek olarak &nbsp; vs varsa boşluğa çevir
                summary = summary.replace("&nbsp;", " ")

                image_url = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=800&q=80"
                if "media_content" in entry and len(entry.media_content) > 0:
                    image_url = entry.media_content[0].get("url", image_url)
                elif "enclosures" in entry and len(entry.enclosures) > 0:
                    image_url = entry.enclosures[0].get("url", image_url)

                print(f"Yeni haber bulundu: {title}")

                # İçerik kopyalama kaldırıldı, sadece özet kullanılıyor

                # Her haber için benzersiz ID: zaman + link hash ile çakışma önle
                news_id = f"news_{abs(hash(link)) % 10**9}_{int(time.time())}"
                news_data = {
                    "id": news_id,
                    "title": title,
                    "summary": (summary[:200] + '...') if summary and len(summary) > 200 else (summary or title),
                    "category": "Teknoloji",
                    "source": feed.feed.get("title", "Teknoloji"),
                    "sourceUrl": link,
                    "image": image_url,
                    "date": datetime.now(TR_TZ).strftime("%Y-%m-%d %H:%M")
                }

                PENDING_NEWS[news_id] = news_data
                save_processed_url(link)  # Hemen işlenmiş olarak işaretle (tekrar gönderme)
                found_count += 1

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

                # Her haber arasında kısa bekleme (Telegram rate limit)
                await asyncio.sleep(1)

        except Exception as e:
            print(f"RSS ayrıştırma hatası ({feed_url}): {e}")

    # Tarama özeti gönder
    if found_count == 0:
        print(f"[{datetime.now(TR_TZ).strftime('%H:%M:%S')}] Tarama tamamlandı. Yeni haber bulunamadı.")
    else:
        print(f"[{datetime.now(TR_TZ).strftime('%H:%M:%S')}] Tarama tamamlandı. {found_count} yeni haber onaya gönderildi.")
        try:
            await context.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"✅ *Tarama tamamlandı!*\n📊 Toplam *{found_count}* yeni haber onayınıza sunuldu.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if not is_authorized(query.from_user.id):
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
    if not is_authorized(update.effective_user.id): return
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
    if not is_authorized(query.from_user.id):
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
    if not is_authorized(update.effective_user.id):
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
    await update.message.reply_text("Şimdi lütfen haberin **KAYNAĞINI** yazın (Örn: Özel İçerik, Webtekno):", parse_mode="Markdown")
    return SOURCE

async def ask_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['manual_news']['source'] = update.message.text
    
    keyboard = [
        [
            InlineKeyboardButton("🌍 Dünya", callback_data="cat:Dünya"),
            InlineKeyboardButton("💻 Teknoloji", callback_data="cat:Teknoloji"),
            InlineKeyboardButton("🚨 Son dakika", callback_data="cat:Son dakika")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Lütfen haberin **KATEGORİSİNİ** seçin:", parse_mode="Markdown", reply_markup=reply_markup)
    return CATEGORY

async def ask_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    _, category = query.data.split(":", 1)
    context.user_data['manual_news']['category'] = category
    
    await query.edit_message_text(f"Seçilen Kategori: **{category}**\n\nSon olarak, haber için bir **görsel** gönderin.\n(Galeriden bir fotoğraf yükleyebilir veya resim linki yapıştırabilirsiniz. İstemiyorsanız `gec` yazın)", parse_mode="Markdown")
    return IMAGE

async def ask_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        await update.message.reply_text("⏳ Fotoğraf GitHub'a yükleniyor, lütfen bekleyin...")
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        
        filename = f"img_{int(time.time())}.jpg"
        b64_content = base64.b64encode(image_bytes).decode("utf-8")
        
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/images/{filename}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        payload = {
            "message": "🖼 Yeni resim yüklendi",
            "content": b64_content,
            "branch": "main"
        }
        res = requests.put(url, headers=headers, json=payload)
        
        if res.status_code in [200, 201]:
            image_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/images/{filename}"
        else:
            image_url = "https://images.unsplash.com/photo-1585829365295-ab7cd400c167?auto=format&fit=crop&w=800&q=80"
            await update.message.reply_text("⚠️ Resim GitHub'a yüklenemedi. Varsayılan resim kullanılacak.")
    else:
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
        "category": context.user_data['manual_news'].get('category', 'Teknoloji'),
        "source": context.user_data['manual_news']['source'],
        "image": image_url,
        "date": datetime.now(TR_TZ).strftime("%Y-%m-%d %H:%M")
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
    context.user_data.clear()
    await update.message.reply_text("🚫 Devam eden işlem iptal edildi.")
    return ConversationHandler.END

async def global_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    
    # Tüm kullanıcı verilerini ve bekleyen haber onaylarını temizle
    context.user_data.clear()
    cleared_count = len(PENDING_NEWS)
    PENDING_NEWS.clear()
    
    await update.message.reply_text(f"✅ Bütün işlemler iptal edildi ve bekleyen {cleared_count} onay temizlendi.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Üzgünüm, bu bot kişiye özeldir. Erişim yetkiniz bulunmamaktadır.")
        return
    await update.message.reply_text("👋 Haber Onay Botu Aktif! Yeni haberler düştüğünde onayınıza sunulacak.\nKomutları menüden görebilirsiniz.")

async def manual_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("🔍 RSS kaynakları taranıyor...")
    await check_rss_and_notify(context)
    await update.message.reply_text("✅ Tarama tamamlandı.")

async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Yetkiniz yok!")
        return
    await update.message.reply_text("🛑 Bot tamamen durduruluyor ve kapatılıyor... (Tekrar açmak için sunucudan/terminalden başlatmalısınız)")
    # Bot sürecini tamamen sonlandır
    os._exit(0)

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if is_authorized(user_id):
        await update.message.reply_text("✅ Zaten yetkilisiniz.")
        return
        
    if not context.args:
        await update.message.reply_text("⚠️ Kullanım: `/giris <şifre>`", parse_mode="Markdown")
        return
        
    password = context.args[0]
    if password == ADMIN_PASSWORD:
        save_auth_user(user_id)
        await update.message.reply_text("🎉 Başarıyla giriş yaptınız! Artık botu yönetebilir ve haber onaylayabilirsiniz.")
    else:
        await update.message.reply_text("❌ Hatalı şifre!")

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id == str(TELEGRAM_CHAT_ID):
        await update.message.reply_text("👑 Siz ana yöneticisiniz, yetkinizi kaldıramazsınız.")
        return
    
    users = load_auth_users()
    if user_id in users:
        users.remove(user_id)
        with open(AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump(list(users), f)
        await update.message.reply_text("🚪 Başarıyla çıkış yaptınız. Yetkileriniz alındı.")
    else:
        await update.message.reply_text("⚠️ Zaten giriş yapmamışsınız.")

async def reset_auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != str(TELEGRAM_CHAT_ID):
        await update.message.reply_text("⛔ Bu komutu sadece ANA YÖNETİCİ kullanabilir!")
        return
        
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump([], f)
    await update.message.reply_text("🧹 Tüm ek yetkililerin erişimi başarıyla sıfırlandı! Artık siteyi sadece siz yönetebilirsiniz.")

async def change_password_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != str(TELEGRAM_CHAT_ID):
        await update.message.reply_text("⛔ Bu komutu sadece ANA YÖNETİCİ kullanabilir!")
        return
        
    if not context.args:
        await update.message.reply_text("⚠️ Kullanım: `/sifredegistir <yeni_şifre>`", parse_mode="Markdown")
        return
        
    new_password = context.args[0]
    global ADMIN_PASSWORD
    ADMIN_PASSWORD = new_password
    
    cfg = {}
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except:
            pass
            
    cfg["ADMIN_PASSWORD"] = new_password
    try:
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
        await update.message.reply_text(f"✅ Giriş şifresi başarıyla `{new_password}` olarak değiştirildi!", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text("⚠️ Şifre değiştirildi ancak ayar dosyasına kaydedilemedi.")

async def duyuru_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in admin_users:
        await update.message.reply_text("Bu komutu kullanmaya yetkiniz yok. Önce /giris yapın.")
        return
        
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Lütfen bir duyuru metni girin veya silmek için '/duyuru sil' yazın.\nÖrn: /duyuru Yeni sitemiz yayında!")
        return
        
    if text.lower() == "sil":
        data = {"announcement": ""}
        msg = "✅ Duyuru çubuğu başarıyla kaldırıldı."
    else:
        data = {"announcement": text}
        msg = f"✅ Kayan duyuru eklendi:\n{text}"
        
    try:
        announcement_content = json.dumps(data, ensure_ascii=False, indent=4)
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/announcement.json"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        get_resp = requests.get(url, headers=headers)
        sha = None
        if get_resp.status_code == 200:
            sha = get_resp.json()["sha"]
            
        push_data = {
            "message": "Update announcement",
            "content": base64.b64encode(announcement_content.encode("utf-8")).decode("utf-8")
        }
        if sha:
            push_data["sha"] = sha
            
        put_resp = requests.put(url, headers=headers, json=push_data)
        if put_resp.status_code in [200, 201]:
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text(f"⚠️ GitHub'a yüklenirken hata oluştu: {put_resp.text}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Hata: {str(e)}")

async def post_init(application: Application):
    commands = [
        BotCommand("start", "Botu başlatır"),
        BotCommand("giris", "Şifre ile yetki al (Örn: /giris 123)"),
        BotCommand("cikis", "Yetkini bırak ve çıkış yap"),
        BotCommand("sifredegistir", "(Admin) Şifreyi değiştir"),
        BotCommand("yetkilerisifirla", "(Admin) Herkesi at"),
        BotCommand("haberler", "Sitedeki haberleri listele ve yönet"),
        BotCommand("haberekle", "Adım adım manuel haber ekle"),
        BotCommand("tara", "RSS kaynaklarını şimdi tara"),
        BotCommand("duyuru", "Siteye kayan yazı ekle (Örn: /duyuru Metin)"),
        BotCommand("iptal", "Devam eden işlemi iptal et"),
        BotCommand("kapat", "Botu tamamen durdur ve kapat")
    ]
    await application.bot.set_my_commands(commands)

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("giris", login_command))
    app.add_handler(CommandHandler("cikis", logout_command))
    app.add_handler(CommandHandler("sifredegistir", change_password_command))
    app.add_handler(CommandHandler("yetkilerisifirla", reset_auth_command))
    app.add_handler(CommandHandler("haberler", list_news))
    app.add_handler(CommandHandler("tara", manual_scan))
    app.add_handler(CommandHandler("duyuru", duyuru_command))
    app.add_handler(CommandHandler("kapat", stop_bot))
    app.add_handler(CommandHandler("iptal", global_cancel))
    
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
            SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_source)],
            CATEGORY: [CallbackQueryHandler(ask_category, pattern="^cat:")],
            IMAGE: [MessageHandler((filters.PHOTO | filters.TEXT) & ~filters.COMMAND, ask_image)],
        },
        fallbacks=[CommandHandler("iptal", cancel_news)]
    )
    app.add_handler(add_conv_handler)
    
    app.add_handler(CallbackQueryHandler(button_handler))
    
    job_queue = app.job_queue

    # Saatin başında (XX:00) çalışacak şekilde bir sonraki tam saate kadar bekle,
    # sonra her 3600 saniyede (1 saat) tekrar et — kullanıcının son tarama zamanına bağımlı değil.
    now = datetime.now(TR_TZ)
    seconds_to_next_hour = 3600 - (now.minute * 60 + now.second)
    print(f"[INFO] İlk otomatik tarama {seconds_to_next_hour} saniye sonra (bir sonraki tam saatte) başlayacak.")
    job_queue.run_repeating(check_rss_and_notify, interval=3600, first=seconds_to_next_hour)
    
    print("[INFO] Haber Botu baslatildi... Dinleniyor...")
    app.run_polling()

if __name__ == "__main__":
    main()
