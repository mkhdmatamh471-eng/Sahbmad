import asyncio
import threading
import sys
import os
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyrogram import Client, filters 
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
import google.generativeai as genai
from pyrogram.enums import ChatType

# --- إعداد السجلات ---
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

# --- استيراد الإعدادات ---
try:
    from config import normalize_text, CITIES_DISTRICTS, BOT_TOKEN
    print("✅ تم تحميل الإعدادات بنجاح")
except Exception as e:
    print(f"❌ خطأ في تحميل ملف config.py: {e}")
    # لن نوقف البرنامج، سنعتمد على المتغيرات البيئية إن وجدت
    # sys.exit(1) 

# --- متغيرات البيئة ---
API_ID = os.environ.get("API_ID", "36360458")
API_HASH = os.environ.get("API_HASH", "daae4628b4b4aac1f0ebfce23c4fa272")
SESSION_STRING = os.environ.get("SESSION_STRING", "BAIq0QoAhqQ7maNFOf6AUKx6sP1-w-GnmTM4GCyqL0INirrOO99rgvLN38CRda5n7P4vstDSL8lBamXl5i8urauRc3Zpq54NJsBdJyNy8pqhp9KzAGDoE1Lveo78y_81h81QYcn_7NQeMQIJLM5uw3S2XPnzYif7y_LYewcx15ZY_kgKWOE4mx0YZvt4V_8h3_zSSVsAWvY3rz_H0TmknpCgczsXx6XfhW90CekcU0-nH39h9ocdtYy6uJ9cXDqsHFf45wSwL5A9tuQNRTzbwe6uIrNTWwNzz86O7jysD53YEeV2zCx625iXuoDYy3b6YJnHzgGmKRpdts7LzrGEoOanUDLYSgAAAAH-ZrzOAA")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyDvEF8WDhGt6nDWjqxgix0Rb8qaAmtEPbk")
BOT_USERNAME = "Mishwariibot" 
CHANNEL_ID = -1003763324430 

TARGET_USERS = [
    8563113166, 7897973056, 8123777916, 8181237063, 8246402319, 
    6493378017, 7068172120, 1658903455, 1506018292, 1193267455, 
    627214092, 336092598, 302374285, 987654321
]

# --- إعداد Gemini ---
genai.configure(api_key=GEMINI_API_KEY)
# استخدام الموديل المستقر لتجنب أخطاء 404
ai_model = genai.GenerativeModel(
  model_name="gemini-1.5-flash", 
  generation_config={"temperature": 0.0, "max_output_tokens": 5}
)

# --- عملاء تليجرام ---
user_app = Client("my_session", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
bot_sender = Bot(token=BOT_TOKEN)

# ---------------------------------------------------------
# قوائم الفلترة
# ---------------------------------------------------------
BLOCK_KEYWORDS = [
    "متواجد الآن", "شغال الآن", "جاهز للتوصيل", "سيارة نظيفة", "أسعارنا", 
    "دربك سمح", "بخدمتكم", "استقبل طلباتكم", "أستقبل طلباتكم", "أوصل مشاوير", 
    "بأرخص الأسعار", "ارخص الاسعار", "بأسعار مناسبة", "واتساب", "للتواصل واتس",
    "فان عائلي", "سيارة حديثة", "سواق خاص جاهز", "يوجد لدينا توصيل",
    "نقل عفش", "نقل بضائع", "سطحة", "سطحه", "دباب نقل", "تأمين", "تفويض", 
    "تجديد", "قرض", "تمويل", "تسديد مخالفات", "استقدام", "خادمات", "شغالات",
    "معقب", "انجاز", "إنجاز", "تعديل مهنة", "اسقاط", "كفيل", "نقل كفالة",
    "عقار", "عقارات", "للبيع", "للايجار", "للإيجار", "مخطط", "أرض", "ارض", 
    "فلة", "فله", "شقة", "شقه", "دور للبيع", "صك", "إفراغ", "الوساطة العقارية",
    "http", "t.me", ".com", "رابط", "انضم", "جروب", "قروب", "قناة", "اشترك",
    "استثمار", "زواج", "مسيار", "خطابه", "خطابة", "تعارف"
]

IRRELEVANT_TOPICS = [
    "عذر طبي", "سكليف", "سكليفات", "اجازة مرضية", "إجازة مرضية", 
    "تقويم اسنان", "خلع اسنان", "تنظيف اسنان", "تركيبات", "عيادة", "عياده",
    "سمكري", "قطع غيار", "تشليح", "ورشة سيارات", "ورشه سيارات", "فحص دوري",
    "استفسار عن", "تنصحوني بـ", "أفضل دكتور", "افضل دكتور", "مين جرب"
]

# ---------------------------------------------------------
# دوال الفحص (المدينة المنورة)
# ---------------------------------------------------------
def manual_fallback_check_madinah(clean_text):
    # 1. كلمات تدل على "نية الطلب"
    order_triggers = [
        "ابي", "ابغي", "أبغا", "ابغى", "محتاج", "مطلوب", "نبي", "مين", "بكم", 
        "يوديني", "يوصلني", "توديني", "توصيلة", "توصيله", "مشوار", "حق مشوار",
        "دحين", "حالا", "الآن", "مستعجل", "فينك", "في احد", "في أحد", "متوفر", 
        "موجود", "كباتن", "يا كابتن", "يا شباب", "سواق", "سائق", "مندوب", "يطلع",
        "الين", "لين", "لغاية", "رايح", "خارج", "نازل", "من", "إلى", "الى"
    ]
    
    # 2. وجهات ومعالم المدينة
    madinah_keywords = [
    # --- المنطقة المركزية والحرم ---
    "الحرم", "المسجد النبوي", "النبوي", "المركزية", "باب السلام", "البقيع", "المناخة",
    "المنطقة المركزية", "الساحات", "فندق", "محطة الصافية", "المصلى", "الغمازة",
    "العزيزية", "شوران", "الهجرة", "العوالي", "قربان", "الحزام", "الدعيثة",
    "باقدو", "الأزهري", "سلطانة", "القبلتين", "الفتح", "السيح", "الرية", 
    "الجرف", "بئر عثمان", "الخالدية", "النصر", "العاقول", "مخطط الملك فهد",
    "بني حارثة", "الشهداء", "المصانع", "العنبرية", "المستراح", "سيد الشهداء",
    "وعيرة", "الرانوناء", "تلال علي", "حمراء الأسد", "الملك فهد", "المطار القديم",
    "بني بياضة", "العصبة", "دوحة الهجرة", "الفيحاء", "السكب", "نبلاء",
    "المطار", "مطار المدينة", "مطار الأمير محمد", "قطار الحرمين", "محطة القطار",
    "مسجد قباء", "جبل احد", "جبل أحد", "ميقات", "أبيار علي", "ابيار علي", "ذو الحليفة",
    "الخندق", "مجمع الملك فهد", "طباعة المصحف", "البيضاء", "البركة", "منتزه البيضاء",
    "الراشد مول", "ميغا مول", "النور مول", "العالية مول", "المنار مول", "مجمع القارات",
    "سوق المدينة الدولي", "سوق بلال", "الداودية", "الشرقية", "البدر", "مزايا",
    "عالم توفير", "سوق الغنم", "سوق الخضار", "الحلقة", "حلقة الخضار",
    "جامعة طيبة", "الجامعة الإسلامية", "الجامعة الاسلامية", "كلية التقنية", "جامعة الأمير مقرن",
    "مستشفى أحد", "مستشفى احد", "مستشفى الملك فهد", "المواساة", "مستشفى الولادة", 
    "الميقات", "مستشفى الدار", "الحرس", "مستشفى الحرس الوطني", "العسكري", "المستشفى العسكري",
    "التأهيل الطبي", "مركز القلب", "الاستهلاكي",
    "المدينة", "المدينه", "المدينة المنورة", "طريق الهجرة", "طريق تبوك", "طريق ينبع"
]

    
    has_order = any(w in clean_text for w in order_triggers)
    has_keyword = any(w in clean_text for w in madinah_keywords)
    
    route_markers = [" الى", " إلى", " لـ", " الين", " لين", " للحرم", " للمطار", " للقطار", " لحي"]
    has_route = "من" in clean_text and any(x in clean_text for x in route_markers)
    
    is_asking_price = "بكم" in clean_text and (has_keyword or "مشوار" in clean_text)

    return (has_order and has_keyword) or has_route or is_asking_price

async def analyze_message_hybrid(text):
    if not text or len(text) < 5 or len(text) > 400: return False
    clean_text = normalize_text(text)
    
    # فلترة فورية
    if any(k in clean_text for k in BLOCK_KEYWORDS + IRRELEVANT_TOPICS): 
        return False

    # الفحص اليدوي (السريع) أولاً
    if manual_fallback_check_madinah(clean_text):
        print(f"✅ سحب يدوي (المدينة): {clean_text[:30]}")
        return True

    # ذكاء اصطناعي (للحالات الصعبة)
    prompt = f"""
    Role: Traffic Controller for Madinah Taxi.
    Task: Reply 'YES' if this is a CUSTOMER request for a ride. Reply 'NO' for drivers/ads.
    Text: "{text}"
    Reply ONLY YES or NO.
    """
    try:
        response = await asyncio.to_thread(ai_model.generate_content, prompt)
        return "YES" in response.text.upper()
    except Exception as e:
        print(f"⚠️ تجاوز AI: {e}")
        return False

# ---------------------------------------------------------
# دوال الإشعارات
# ---------------------------------------------------------
async def notify_users(detected_district, original_msg):
    content = original_msg.text or original_msg.caption
    if not content: return
    try:
        customer = original_msg.from_user
        if not customer: return

        gateway_url = f"https://t.me/{BOT_USERNAME}?start=direct_{customer.id}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💬 مراسلة العميل", url=gateway_url)]])

        alert_text = (
            f"🎯 <b>طلب جديد (المدينة)!</b>\n"
            f"📍 <b>المنطقة:</b> {detected_district}\n"
            f"👤 <b>العميل:</b> {customer.first_name}\n"
            f"📝 <b>النص:</b>\n<i>{content}</i>"
        )

        for user_id in TARGET_USERS:
            try:
                await bot_sender.send_message(user_id, alert_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            except: pass
    except Exception as e: print(f"❌ خطأ مستخدمين: {e}")

async def notify_channel(detected_district, original_msg):
    content = original_msg.text or original_msg.caption
    if not content: return
    try:
        customer = original_msg.from_user
        if not customer: return
        
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💳 للاشتراك", url="https://t.me/x3FreTx")]])
        alert_text = (
            f"🎯 <b>طلب جديد!</b>\n📍 <b>المنطقة:</b> {detected_district}\n"
            f"👤 <b>العميل:</b> {customer.first_name}\n📝 <i>{content}</i>"
        )
        await bot_sender.send_message(CHANNEL_ID, alert_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception as e: print(f"❌ خطأ قناة: {e}")

# ---------------------------------------------------------
# المعالج الرئيسي (واحد فقط!)
# ---------------------------------------------------------
@user_app.on_message(filters.group & ~filters.me)
async def message_handler(client, msg):
    try:
        text = msg.text or msg.caption
        if not text or len(text) < 5: return

        # التحليل
        if await analyze_message_hybrid(text):
            # محاولة تحديد الحي (اختياري)
            found_d = "المدينة المنورة"
            
            # إرسال الإشعارات بالتوازي
            await asyncio.gather(
                notify_users(found_d, msg),
                notify_channel(found_d, msg)
            )
            logging.info(f"✅ تم التقاط طلب من: {msg.chat.title}")

    except Exception as e:
        logging.error(f"⚠️ خطأ المعالج: {e}")

# ---------------------------------------------------------
# التشغيل والبقاء حياً
# ---------------------------------------------------------
def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.wfile.write(b"ALIVE")
        def log_message(self, format, *args): return

    try:
        server = HTTPServer(('0.0.0.0', port), HealthHandler)
        print(f"🌍 Health Server running on {port}")
        server.serve_forever()
    except Exception as e: print(f"❌ Health fail: {e}")

async def start_radar():
    print("🚀 تشغيل الرادار...")
    await user_app.start()
    print("✅ تم الاتصال!")
    from pyrogram.methods.utilities.idle import idle
    await idle()
    await user_app.stop()

if __name__ == "__main__":
    
    # 1. تشغيل خادم الصحة في الخلفية
    threading.Thread(target=run_health_server, daemon=True).start()
    
    # 2. تشغيل البوت باستخدام asyncio.run (أكثر استقراراً)
    try:
        asyncio.run(start_radar())
    except (KeyboardInterrupt, SystemExit):
        print("👋 إيقاف يدوي.")
    except Exception as e:
        print(f"⚠️ انهيار غير متوقع: {e}")
        sys.exit(1) # لإجبار Render على إعادة التشغيل
