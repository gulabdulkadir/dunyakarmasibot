import nest_asyncio
nest_asyncio.apply()

import os
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from supabase import create_client, Client
from datetime import datetime, time, timezone

# --- RENDER WEB SERVİSİ İÇİN MİNİ WEB SUNUCUSU ---
server_app = Flask(__name__)

@server_app.route('/')
def home():
    return "RONALDO(BOT) aktif ve çalışıyor! ⚽"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server_app.run(host="0.0.0.0", port=port)

# --- KİMLİK BİLGİLERİNİ GİZLİ HAFIZADAN ÇEKİYORUZ ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL") 
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Supabase Bağlantısını Başlat
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Otomatik maç bitirme ve hatırlatıcı mesajı için grup ID'sini hafızada tutacağız
GRUP_CHAT_ID = None

# Butonları üreten fonksiyon (Silinmemesi için mesaj güncellenirken tekrar çağıracağız)
def butonlari_getir():
    keyboard = [
        [InlineKeyboardButton("🟢 Geliyorum", callback_data="geliyor")],
        [InlineKeyboardButton("🔴 Gelemiyorum / İptal", callback_data="iptal")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Ödeme onay butonu (Yerinde kalan akıllı buton)
def odeme_butonu_getir():
    keyboard = [
        [InlineKeyboardButton("💸 Ödemeyi Yaptım / Geri Al", callback_data="odemeyapildi")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def oylama(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1. YETKİ KONTROLÜ (Sadece Yöneticiler)
    if update.effective_chat.type in ['group', 'supergroup']:
        kullanici = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if kullanici.status not in ['administrator', 'creator']:
            await update.message.reply_text("⚠️ Bu komutu sadece halı saha yöneticileri kullanabilir!")
            return

    # 2. AKTİF OYLAMA KİLİDİ
    aktif_mac_sorgusu = supabase.table("maclar").select("id").eq("aktif_mi", True).execute()
    if aktif_mac_sorgusu.data:
        await update.message.reply_text("⚠️ Şu an zaten devam eden bir oylama var!\nYeni oylama başlatmak için önce /macikapat yazarak mevcut oylamayı sonlandırmalısınız.")
        return

    global GRUP_CHAT_ID
    GRUP_CHAT_ID = update.message.chat_id 
    
    su_an = datetime.utcnow().isoformat()
    supabase.table("maclar").insert({"tarih": su_an, "aktif_mi": True}).execute()
    
    await update.message.reply_text(
        "⚽ Yeni maç oylaması başladı!\n📌 Kontenjan: 14 Kişi\n\nKatılmak için aşağıdaki butonları kullanın.", 
        reply_markup=butonlari_getir()
    )

# --- GÜNCELLENEN: MANUEL MAÇ KAPATMA VE ADİL PUAN DAĞITICI ---
async def macikapat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ['group', 'supergroup']:
        kullanici = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if kullanici.status not in ['administrator', 'creator']:
            await update.message.reply_text("⚠️ Bu komutu sadece yöneticiler kullanabilir!")
            return

    # Aktif maçı bul
    aktif_mac_sorgusu = supabase.table("maclar").select("id").eq("aktif_mi", True).execute()
    if not aktif_mac_sorgusu.data:
        await update.message.reply_text("⚠️ Kapatılacak aktif bir oylama bulunmuyor.")
        return
        
    mac_id = aktif_mac_sorgusu.data[0]['id']

    # "geliyor" diyen (asil) oyuncuları bul ve hak ettikleri +10 puanı ekle
    gelenler = supabase.table("kayitlar").select("telegram_id").eq("mac_id", mac_id).eq("durum", "geliyor").execute()
    
    eklenen_kisi_sayisi = 0
    for kisi in gelenler.data:
        tid = kisi['telegram_id']
        if tid < 0:
            continue # Dışarıdan eklenenleri atla
        oyuncu = supabase.table("oyuncular").select("toplam_puan").eq("telegram_id", tid).execute()
        eski_puan = oyuncu.data[0]['toplam_puan'] if oyuncu.data else 0
        supabase.table("oyuncular").update({"toplam_puan": eski_puan + 10}).eq("telegram_id", tid).execute()
        eklenen_kisi_sayisi += 1

    # Maçı kapat
    supabase.table("maclar").update({"aktif_mi": False}).eq("id", mac_id).execute()
    
    await update.message.reply_text(
        f"🛑 *MAÇ YÖNETİCİ TARAFINDAN KAPATILDI!* 🛑\n\n"
        f"O an asil kadroda olan {eklenen_kisi_sayisi} oyuncunun hanesine adil bir şekilde *+10 Puan* eklendi.\n"
        f"Güncel durum için `/puandurumu` yazabilirsiniz.",
        parse_mode='Markdown'
    )

# --- YENİ: SEZONU BİTİR VE BONUS PUAN DAĞIT ---
async def sezonubitir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ['group', 'supergroup']:
        kullanici = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if kullanici.status not in ['administrator', 'creator']:
            await update.message.reply_text("⚠️ Bu komutu sadece yöneticiler kullanabilir!")
            return

    oyuncular = supabase.table("oyuncular").select("telegram_id, isim, toplam_puan").order("toplam_puan", desc=True).execute()

    if not oyuncular.data:
        await update.message.reply_text("⚠️ Sıfırlanacak oyuncu bulunmuyor.")
        return

    # Herkesin puanını 0 yap
    supabase.table("oyuncular").update({"toplam_puan": 0}).neq("telegram_id", 0).execute()

    podyum_mesaji = "🏁 *SEZON BİTTİ - YENİ SEZON BAŞLADI!* 🏁\n\nÖnceki sezonun sıralamasına göre yeni sezona bonus puanlarla başlanıyor:\n\n"

    for i, oyuncu in enumerate(oyuncular.data):
        tid = oyuncu['telegram_id']
        isim = oyuncu['isim']
        bonus = 0

        if i == 0 or i == 1 or i == 2:  # İlk 3 kişi
            bonus = 30
        elif i == 3 or i == 4 or i == 5:  # 4, 5, 6. kişiler
            bonus = 20
        elif i == 6 or i == 7 or i == 8:  # 7, 8, 9. kişiler
            bonus = 10

        if bonus > 0:
            supabase.table("oyuncular").update({"toplam_puan": bonus}).eq("telegram_id", tid).execute()
            podyum_mesaji += f"{i+1}. {isim} ➔ Yeni Sezona *+{bonus} Puan* ile başladı! 🚀\n"

    podyum_mesaji += "\nTüm puanlar sıfırlandı. Yeni sezonda herkese başarılar! ⚽🏆"
    await update.message.reply_text(podyum_mesaji, parse_mode='Markdown')

async def odemebaslat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ['group', 'supergroup']:
        kullanici = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if kullanici.status not in ['administrator', 'creator']:
            await update.message.reply_text("⚠️ Bu komutu sadece yöneticiler kullanabilir!")
            return

    aktif_mac_sorgusu = supabase.table("maclar").select("id").eq("aktif_mi", True).execute()
    if not aktif_mac_sorgusu.data:
        await update.message.reply_text("⚠️ Aktif bir maç bulunmuyor.")
        return
        
    await update.message.reply_text(
        "💰 *ÖDEME VAKTİ!* 💰\n\nHalı saha ücretini IBAN'a gönderenler butona basarak onaylasın. Yanlışlıkla basarsanız tekrar basıp ödemenizi geri alabilirsiniz.",
        reply_markup=odeme_butonu_getir(),
        parse_mode='Markdown'
    )

async def odemelerikontrol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ['group', 'supergroup']:
        kullanici = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if kullanici.status not in ['administrator', 'creator']:
            await update.message.reply_text("⚠️ Bu komutu sadece yöneticiler kullanabilir!")
            return

    aktif_mac_sorgusu = supabase.table("maclar").select("id").eq("aktif_mi", True).execute()
    if not aktif_mac_sorgusu.data:
        await update.message.reply_text("⚠️ Aktif bir maç bulunmuyor.")
        return
        
    mac_id = aktif_mac_sorgusu.data[0]['id']

    asil_kayitlar = supabase.table("kayitlar").select("telegram_id, oyuncular(isim)").eq("mac_id", mac_id).eq("durum", "geliyor").execute()
    odeme_kayitlari = supabase.table("odemeler").select("telegram_id").eq("mac_id", mac_id).eq("odendi_mi", True).execute()
    oduyen_idler = [o['telegram_id'] for o in odeme_kayitlari.data]

    odemeyenler = []
    for kayit in asil_kayitlar.data:
        tid = kayit['telegram_id']
        isim = kayit['oyuncular']['isim']
        if tid not in oduyen_idler:
            odemeyenler.append((tid, isim))

    if not odemeyenler:
        await update.message.reply_text("🎉 Harika! Kadrodaki herkes ödemesini yapmış.")
        return

    mesaj = "⚠️ *ÖDEMESİNİ YAPMAYANLAR LİSTESİ* ⚠️\n\nAşağıdaki oyuncular maça yazılmış ancak henüz ödeme onay butonuna basmamış:\n\n"
    for tid, isim in odemeyenler:
        if tid < 0:
            mesaj += f"🔸 {isim} (Dışarıdan Oyuncu)\n"
        else:
            mesaj += f"🔸 [{isim}](tg://user?id={tid})\n"
    
    mesaj += "\nLütfen bir an önce ödemeyi gerçekleştirip butona basınız! 💸"
    await update.message.reply_text(mesaj, parse_mode='Markdown')

async def kadro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    aktif_mac_sorgusu = supabase.table("maclar").select("id").eq("aktif_mi", True).execute()
    if not aktif_mac_sorgusu.data:
        await update.message.reply_text("⚠️ Şu an aktif bir maç oylaması bulunmuyor.")
        return
        
    mac_id = aktif_mac_sorgusu.data[0]['id']
    kayitlar = supabase.table("kayitlar").select("durum, oyuncular(isim)").eq("mac_id", mac_id).execute()

    asil_liste, yedek_liste = [], []
    for kayit in kayitlar.data:
        isim = kayit['oyuncular']['isim'] 
        durum = kayit['durum']
        if durum == 'geliyor': asil_liste.append(isim)
        elif durum == 'yedek': yedek_liste.append(isim)

    mesaj = "📋 *BU HAFTANIN KADROSU*\n\n🟢 *ASİL KADRO (İlk 14)*\n"
    for i, isim in enumerate(asil_liste, 1): mesaj += f"{i}. {isim}\n"
    if not asil_liste: mesaj += "Henüz kimse yazılmadı.\n"

    mesaj += "\n🟡 *YEDEKLER*\n"
    for i, isim in enumerate(yedek_liste, 1): mesaj += f"{i}. {isim}\n"
    if not yedek_liste: mesaj += "Yedek oyuncu bulunmuyor.\n"

    await update.message.reply_text(mesaj, parse_mode='Markdown')

async def puandurumu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    oyuncular = supabase.table("oyuncular").select("isim, toplam_puan").order("toplam_puan", desc=True).execute()
    mesaj = "🏆 *TÜM ZAMANLARIN PUAN DURUMU*\n\n"
    if not oyuncular.data:
        mesaj += "Henüz veritabanında oyuncu yok."
    else:
        for i, oyuncu in enumerate(oyuncular.data, 1):
            mesaj += f"{i}. {oyuncu['isim']} ➔ *{oyuncu['toplam_puan']} Puan*\n"
    await update.message.reply_text(mesaj, parse_mode='Markdown')

async def puanver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ['group', 'supergroup']:
        kullanici = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if kullanici.status not in ['administrator', 'creator']:
            await update.message.reply_text("⚠️ Bu komutu sadece yöneticiler kullanabilir!")
            return

    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Lütfen puan vermek/silmek istediğiniz kişinin **mesajını yanıtlayarak** bu komutu kullanın!", parse_mode='Markdown')
        return

    if not context.args:
        await update.message.reply_text("⚠️ Eksik kullanım! Örn: `/puanver 10` veya `/puanver -20`", parse_mode='Markdown')
        return

    try:
        eklenecek_puan = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Hata: Puan kısmı sayı olmalıdır!", parse_mode='Markdown')
        return

    hedef_user = update.message.reply_to_message.from_user
    hedef_id = hedef_user.id
    hedef_isim = hedef_user.first_name

    oyuncu_sorgu = supabase.table("oyuncular").select("toplam_puan, isim").eq("telegram_id", hedef_id).execute()

    if not oyuncu_sorgu.data:
        supabase.table("oyuncular").insert({"telegram_id": hedef_id, "isim": hedef_isim, "toplam_puan": 0}).execute()
        eski_puan = 0
    else:
        eski_puan = oyuncu_sorgu.data[0]['toplam_puan']
        hedef_isim = oyuncu_sorgu.data[0]['isim']

    yeni_puan = eski_puan + eklenecek_puan
    supabase.table("oyuncular").update({"toplam_puan": yeni_puan}).eq("telegram_id", hedef_id).execute()

    isaret = "+" if eklenecek_puan > 0 else ""
    await update.message.reply_text(f"✅ *{hedef_isim}* adlı oyuncunun puanına `{isaret}{eklenecek_puan}` eklendi!\n📊 Yeni Toplam Puan: *{yeni_puan}*", parse_mode='Markdown')

async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ['group', 'supergroup']:
        kullanici = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if kullanici.status not in ['administrator', 'creator']:
            await update.message.reply_text("⚠️ Bu komutu sadece yöneticiler kullanabilir!")
            return

    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Lütfen kadrodan çıkarmak istediğiniz kişinin **mesajını yanıtlayarak** `/kick` yazın!", parse_mode='Markdown')
        return

    aktif_mac_sorgusu = supabase.table("maclar").select("id").eq("aktif_mi", True).execute()
    if not aktif_mac_sorgusu.data:
        await update.message.reply_text("⚠️ Şu an aktif bir maç oylaması bulunmuyor.")
        return
        
    mac_id = aktif_mac_sorgusu.data[0]['id']
    hedef_user = update.message.reply_to_message.from_user
    hedef_id = hedef_user.id
    hedef_isim = hedef_user.first_name

    kayit_sorgu = supabase.table("kayitlar").select("id, durum").eq("mac_id", mac_id).eq("telegram_id", hedef_id).execute()

    if not kayit_sorgu.data:
        await update.message.reply_text(f"⚠️ {hedef_isim} bu maçın oylama listesinde kayıtlı değil.")
        return

    supabase.table("kayitlar").update({"durum": "iptal"}).eq("id", kayit_sorgu.data[0]['id']).execute()
    await update.message.reply_text(f"🚨 *{hedef_isim}* yönetici tarafından maç kadrosundan çıkarıldı (kicklendi)!", parse_mode='Markdown')

async def disardanekle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ['group', 'supergroup']:
        kullanici = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if kullanici.status not in ['administrator', 'creator']:
            await update.message.reply_text("⚠️ Bu komutu sadece yöneticiler kullanabilir!")
            return

    if not context.args:
        await update.message.reply_text("⚠️ Eksik kullanım!\nDoğru format: `/disardanekle [İsim]`\nÖrnek: `/disardanekle Kerem`", parse_mode='Markdown')
        return

    disardan_isim = " ".join(context.args)

    aktif_mac_sorgusu = supabase.table("maclar").select("id").eq("aktif_mi", True).execute()
    if not aktif_mac_sorgusu.data:
        await update.message.reply_text("⚠️ Şu an aktif bir maç oylaması bulunmuyor.")
        return
        
    mac_id = aktif_mac_sorgusu.data[0]['id']

    sahte_id = -int(datetime.utcnow().timestamp())

    supabase.table("oyuncular").upsert({"telegram_id": sahte_id, "isim": disardan_isim, "toplam_puan": 0}).execute()

    gelenler = supabase.table("kayitlar").select("id", count="exact").eq("mac_id", mac_id).eq("durum", "geliyor").execute()
    durum = "geliyor" if gelenler.count < 14 else "yedek"

    supabase.table("kayitlar").insert({"mac_id": mac_id, "telegram_id": sahte_id, "durum": durum}).execute()

    if durum == "geliyor":
        await update.message.reply_text(f"✅ *{disardan_isim}* (Dışarıdan) başarıyla ASİL kadroya eklendi! ⚽", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"⚠️ {disardan_isim} (Dışarıdan) kontenjan dolu olduğu için YEDEK listesine eklendi.", parse_mode='Markdown')

async def disardancikar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ['group', 'supergroup']:
        kullanici = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if kullanici.status not in ['administrator', 'creator']:
            await update.message.reply_text("⚠️ Bu komutu sadece yöneticiler kullanabilir!")
            return

    if not context.args:
        await update.message.reply_text("⚠️ Eksik kullanım!\nDoğru format: `/disardancikar [İsim]`\nÖrnek: `/disardancikar Kerem`", parse_mode='Markdown')
        return

    hedef_isim = " ".join(context.args)

    aktif_mac_sorgusu = supabase.table("maclar").select("id").eq("aktif_mi", True).execute()
    if not aktif_mac_sorgusu.data:
        await update.message.reply_text("⚠️ Şu an aktif bir maç oylaması bulunmuyor.")
        return
        
    mac_id = aktif_mac_sorgusu.data[0]['id']

    oyuncu_sorgu = supabase.table("oyuncular").select("telegram_id, isim").ilike("isim", f"%{hedef_isim}%").execute()

    if not oyuncu_sorgu.data:
        await update.message.reply_text(f"⚠️ Veritabanında '{hedef_isim}' adında bir oyuncu bulunamadı.")
        return

    bulunan_id = oyuncu_sorgu.data[0]['telegram_id']
    gercek_isim = oyuncu_sorgu.data[0]['isim']

    kayit_sorgu = supabase.table("kayitlar").select("id").eq("mac_id", mac_id).eq("telegram_id", bulunan_id).execute()

    if not kayit_sorgu.data:
        await update.message.reply_text(f"⚠️ {gercek_isim} bu maçın kadrosunda kayıtlı değil.")
        return

    supabase.table("kayitlar").update({"durum": "iptal"}).eq("id", kayit_sorgu.data[0]['id']).execute()
    await update.message.reply_text(f"🛑 *{gercek_isim}* (Dışarıdan) maç kadrosundan çıkarıldı!", parse_mode='Markdown')

async def yardim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mesaj = (
        "📜 *DUNYA KARMASI - HALI SAHA KURALLARI & REHBER* 📜\n\n"
        "⚡ *Genel Kurallar:*\n"
        "1️⃣ Maç oylamasında **'Geliyorum'** diyen oyuncular asil kadroya yazılır (Kontenjan: 14 kişi).\n"
        "2️⃣ Asil kadroya yazıldıktan sonra **iptal edenler -20 Puan cezası** alır!\n"
        "3️⃣ Maç bitiminde (veya maç erken kapatıldığında) kadroda olan herkese **+10 Puan** eklenir.\n"
        "4️⃣ Ödemeler IBAN'a yapıldıktan sonra botun ödeme butonundan onaylanmalıdır.\n"
        "5️⃣ Her 3 ayda bir **Sezon Sıfırlaması** yapılır ve ilk 9 oyuncuya bonus puanla yeni sezon başlatılır!\n\n"
        "🤖 *Oyuncu Komutları:*\n"
        "• `/kadro` - Bu haftanın güncel kadrosunu ve yedeklerini gösterir.\n"
        "• `/puandurumu` - Tüm zamanların puan liderlik tablosunu gösterir.\n"
        "• `/yardim` (veya `/kurallar`) - Bu rehberi açar.\n\n"
        "👑 *Yönetici (Admin) Komutları:*\n"
        "• `/oylama` - Yeni maç oylama butonlarını başlatır.\n"
        "• `/macikapat` - Aktif oylamayı sonlandırır ve asil kadrodakilere +10 puanları dağıtır.\n"
        "• `/sezonubitir` - Sezonu sıfırlar, ilk 3'e +30, 4-6 arasına +20, 7-9 arasına +10 puan vererek yeni sezonu açar.\n"
        "• `/odemebaslat` - Ödeme onay butonunu gruba gönderir.\n"
        "• `/odemekontrol` - Ödeme yapmayan asil oyuncuları etiketleyerek uyarır.\n"
        "• `/puanver [Puan]` - Oyuncunun mesajını yanıtlayarak (reply) puan verir/siler.\n"
        "• `/kick` - Oyuncunun mesajını yanıtlayarak (reply) kadrodan çıkarır.\n"
        "• `/disardanekle [İsim]` - Kadroya dışarıdan oyuncu ekler.\n"
        "• `/disardancikar [İsim]` - Dışarıdan eklenen oyuncuyu kadrodan çıkarır."
    )
    await update.message.reply_text(mesaj, parse_mode='Markdown')

async def cumartesi_hatirlatici(context: ContextTypes.DEFAULT_TYPE):
    global GRUP_CHAT_ID
    if GRUP_CHAT_ID:
        await context.bot.send_message(
            chat_id=GRUP_CHAT_ID,
            text="🚨 *DUNYA KARMASI - MAÇ GÜNÜ HATIRLATMASI!* 🚨\n\nBugün akşam maç var! Kramponlar ve formalar hazır mı?\nSon kadroyu görmek için `/kadro` yazabilirsiniz. Gelemeyecek olanlar lütfen şimdiden butonlardan iptal etsin!",
            parse_mode='Markdown'
        )

async def buton_dinleyici(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    user_id = query.from_user.id
    isim = query.from_user.first_name
    secim = query.data

    if secim == "odemeyapildi":
        aktif_mac_sorgusu = supabase.table("maclar").select("id").eq("aktif_mi", True).execute()
        if not aktif_mac_sorgusu.data:
            await query.answer("⚠️ Şu an aktif bir maç oylaması bulunmuyor.", show_alert=True)
            return
        mac_id = aktif_mac_sorgusu.data[0]['id']

        supabase.table("oyuncular").upsert({"telegram_id": user_id, "isim": isim}).execute()

        mevcut_odeme = supabase.table("odemeler").select("id, odendi_mi").eq("mac_id", mac_id).eq("telegram_id", user_id).execute()
        
        if mevcut_odeme.data:
            su_anki_durum = mevcut_odeme.data[0]['odendi_mi']
            yeni_durum = not su_anki_durum
            supabase.table("odemeler").update({"odendi_mi": yeni_durum}).eq("id", mevcut_odeme.data[0]['id']).execute()
            
            if yeni_durum:
                await query.answer(f"✅ {isim}, ödemeniz onaylandı ve kasaya işlendi! 💸", show_alert=True)
            else:
                await query.answer(f"🔄 {isim}, ödeme onayınız iptal edildi (geri alındı).", show_alert=True)
        else:
            supabase.table("odemeler").insert({"mac_id": mac_id, "telegram_id": user_id, "odendi_mi": True}).execute()
            await query.answer(f"✅ {isim}, ödemeniz başarıyla onaylandı ve kasaya işlendi! 💸", show_alert=True)
        return

    await query.answer()

    if secim == "geliyorum": secim = "geliyor"
    if secim not in ["geliyor", "iptal"]: return

    supabase.table("oyuncular").upsert({"telegram_id": user_id, "isim": isim}).execute()

    aktif_mac_sorgusu = supabase.table("maclar").select("id").eq("aktif_mi", True).execute()
    if not aktif_mac_sorgusu.data:
        await query.edit_message_text(text="⚠️ Şu an aktif bir maç oylaması bulunmuyor.")
        return
        
    mac_id = aktif_mac_sorgusu.data[0]['id']
    mevcut_oy = supabase.table("kayitlar").select("id, durum").eq("mac_id", mac_id).eq("telegram_id", user_id).execute()

    mesaj = ""
    if secim == "geliyor":
        gelenler = supabase.table("kayitlar").select("id", count="exact").eq("mac_id", mac_id).eq("durum", "geliyor").execute()
        kisi_sayisi = gelenler.count

        if kisi_sayisi >= 14 and (not mevcut_oy.data or mevcut_oy.data[0]['durum'] != 'geliyor'):
            secim = "yedek"
            mesaj = f"⚠️ {isim}, kontenjan dolduğu için YEDEK listesine yazıldın!"
        else:
            guncel_sayi = kisi_sayisi if (mevcut_oy.data and mevcut_oy.data[0]['durum'] == 'geliyor') else kisi_sayisi + 1
            mesaj = f"✅ {isim} asil kadroya yazıldı! ({guncel_sayi}/14)"
            
    elif secim == "iptal":
        mesaj = f"🔴 {isim} maça gelemeyeceğini bildirdi."
        if mevcut_oy.data and mevcut_oy.data[0]['durum'] == 'geliyor':
            oyuncu = supabase.table("oyuncular").select("toplam_puan").eq("telegram_id", user_id).execute()
            eski_puan = oyuncu.data[0]['toplam_puan'] if oyuncu.data else 0
            supabase.table("oyuncular").update({"toplam_puan": eski_puan - 20}).eq("telegram_id", user_id).execute()
            mesaj += " (⚠️ Kadrodan çıktığı için -20 Puan yedi!)"

    if mevcut_oy.data:
        supabase.table("kayitlar").update({"durum": secim}).eq("id", mevcut_oy.data[0]['id']).execute()
    else:
        supabase.table("kayitlar").insert({"mac_id": mac_id, "telegram_id": user_id, "durum": secim}).execute()

    ana_metin = f"⚽ DUNYA KARMASI - Yeni maç oylaması devam ediyor!\n📌 Kontenjan: 14 Kişi\n\n📝 Son İşlem: {mesaj}"
    try:
        await query.edit_message_text(text=ana_metin, reply_markup=butonlari_getir())
    except:
        pass

async def otomatik_mac_bitir(context: ContextTypes.DEFAULT_TYPE):
    global GRUP_CHAT_ID
    aktif_mac_sorgusu = supabase.table("maclar").select("id").eq("aktif_mi", True).execute()
    if not aktif_mac_sorgusu.data: return
        
    mac_id = aktif_mac_sorgusu.data[0]['id']
    gelenler = supabase.table("kayitlar").select("telegram_id").eq("mac_id", mac_id).eq("durum", "geliyor").execute()
    
    for kisi in gelenler.data:
        tid = kisi['telegram_id']
        if tid < 0:
            continue
        oyuncu = supabase.table("oyuncular").select("toplam_puan").eq("telegram_id", tid).execute()
        eski_puan = oyuncu.data[0]['toplam_puan'] if oyuncu.data else 0
        supabase.table("oyuncular").update({"toplam_puan": eski_puan + 10}).eq("telegram_id", tid).execute()
        
    supabase.table("maclar").update({"aktif_mi": False}).eq("id", mac_id).execute()
    
    if GRUP_CHAT_ID:
        await context.bot.send_message(
            chat_id=GRUP_CHAT_ID, 
            text="🚨 *DUNYA KARMASI - MAÇ TAMAMLANDI!* 🚨\n\nMaça gelen tüm asil oyuncuların hanesine +10 Puan eklendi. Oylama kapanmıştır.\nGüncel durum için /puandurumu yazabilirsiniz.",
            parse_mode='Markdown'
        )

def main():
    print("RONALDO(BOT) başlatılıyor...")
    
    # Flask sunucusunu arka planda başlat (Render web service çökmesini önler)
    server_thread = threading.Thread(target=run_web_server)
    server_thread.daemon = True
    server_thread.start()

    app = Application.builder().token(TELEGRAM_TOKEN).get_updates_read_timeout(42).build()
    
    app.add_handler(CommandHandler("oylama", oylama))
    app.add_handler(CommandHandler("macikapat", macikapat)) 
    app.add_handler(CommandHandler("sezonubitir", sezonubitir))         
    app.add_handler(CommandHandler("kadro", kadro)) 
    app.add_handler(CommandHandler("puandurumu", puandurumu)) 
    app.add_handler(CommandHandler("puanver", puanver)) 
    app.add_handler(CommandHandler("kick", kick))
    app.add_handler(CommandHandler("disardanekle", disardanekle))     
    app.add_handler(CommandHandler("disardancikar", disardancikar)) 
    app.add_handler(CommandHandler("yardim", yardim))                 
    app.add_handler(CommandHandler("kurallar", yardim))               
    app.add_handler(CommandHandler("odemebaslat", odemebaslat))       
    app.add_handler(CommandHandler("odemekontrol", odemelerikontrol)) 
    app.add_handler(CallbackQueryHandler(buton_dinleyici))

    hatirlatici_zaman = time(hour=12, minute=0, tzinfo=timezone.utc)
    app.job_queue.run_daily(cumartesi_hatirlatici, time=hatirlatici_zaman, days=(5,))

    bitirici_zaman = time(hour=20, minute=0, tzinfo=timezone.utc)
    app.job_queue.run_daily(otomatik_mac_bitir, time=bitirici_zaman, days=(5,))
    
    print("RONALDO(BOT) başarıyla çalışıyor!")
    app.run_polling()

if __name__ == '__main__':
    main()