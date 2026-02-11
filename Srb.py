import asyncio
import threading
import sys
import os
import logging
import re   
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyrogram import Client, filters 
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
import google.generativeai as genai
from datetime import datetime
from pyrogram.enums import ChatType

# --- إعداد السجلات ---
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# --- استيراد الإعدادات ---
try:
    from config import normalize_text, CITIES_DISTRICTS, BOT_TOKEN
    print("✅ تم تحميل الإعدادات بنجاح")
except Exception as e:
    print(f"❌ خطأ في تحميل ملف config.py: {e}")
    sys.exit(1)

# --- متغيرات البيئة ---
API_ID = os.environ.get("API_ID", "36360458")
API_HASH = os.environ.get("API_HASH", "daae4628b4b4aac1f0ebfce23c4fa272")
SESSION_STRING = os.environ.get("SESSION_STRING", "BAIq0QoAhqQ7maNFOf6AUKx6sP1-w-GnmTM4GCyqL0INirrOO99rgvLN38CRda5n7P4vstDSL8lBamXl5i8urauRc3Zpq54NJsBdJyNy8pqhp9KzAGDoE1Lveo78y_81h81QYcn_7NQeMQIJLM5uw3S2XPnzYif7y_LYewcx15ZY_kgKWOE4mx0YZvt4V_8h3_zSSVsAWvY3rz_H0TmknpCgczsXx6XfhW90CekcU0-nH39h9ocdtYy6uJ9cXDqsHFf45wSwL5A9tuQNRTzbwe6uIrNTWwNzz86O7jysD53YEeV2zCx625iXuoDYy3b6YJnHzgGmKRpdts7LzrGEoOanUDLYSgAAAAH-ZrzOAA")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyDvEF8WDhGt6nDWjqxgix0Rb8qaAmtEPbk")
BOT_USERNAME = "Mishwariibot" 
# ---------------------------------------------------------
# 🛠️ [تعديل 1] قائمة المستخدمين الذين سيستلمون الطلبات
# ضع الـ IDs الخاصة بهم هنا (أرقام فقط)
# ---------------------------------------------------------
# 🛠️ قائمة الـ IDs المحدثة الذين سيستلمون الطلبات في الخاص (مفتوحة)
TARGET_USERS = [
    8563113166, 7897973056, 8123777916, 8181237063, 8246402319, 
    6493378017, 7068172120, 1658903455, 1506018292, 1193267455, 
    627214092, 336092598, 302374285, 987654321
]
 # <--- ضع الآيديات الحقيقية هنا

CHANNEL_ID = -1003763324430 

# --- إعداد Gemini 1.5 Flash ---
genai.configure(api_key=GEMINI_API_KEY)
generation_config = {
  "temperature": 0.1,
  "top_p": 0.95,
  "top_k": 40,
  "max_output_tokens": 5,
}
ai_model = genai.GenerativeModel(
  model_name="gemini-1.5-flash",
  generation_config=generation_config,
)

# --- عملاء تليجرام ---
user_app = Client("my_session", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
bot_sender = Bot(token=BOT_TOKEN)

# ---------------------------------------------------------
# قوائم الفلترة (كما هي في كودك الأصلي)
# ---------------------------------------------------------
# قائمة 1: كلمات تدل أن المرسل سائق أو إعلان أو مواضيع محظورة (حظر فوري)
BLOCK_KEYWORDS = [
    "متواجد", "متاح", "شغال", "جاهز", "أسعارنا", "سيارة نظيفة", "نقل عفش", 
    "دربك سمح", "توصيل مشاوير", "أوصل", "اوصل", "اتصال", "واتساب", "للتواصل",
    "خاص", "الخاص", "بخدمتكم", "خدمتكم", "أستقبل", "استقبل", "نقل بضائع",
    "مشاويركم", "سياره نظيفه", "فان", "دباب", "سطحه", "سطحة", "كابتن", 
    "مندوب", "مناديب", "توصيل طلبات", "ارخص الأسعار", "أرخص الأسعار", "بأسعار",
    "عقار", "عقارات", "للبيع", "للإيجار", "للايجار", "دور", "شقة", "شقه",
    "رخصة فال", "رخصة", "رخصه", "مخطط", "أرض", "ارض", "فلة", "فله", 
    "عماره", "عمارة", "استثمار", "صك", "إفراغ", "الوساطة العقارية", "تجاري", "سكني",
    "اشتراك", "باقات", "تسجيل", "تأمين", "تفويض", "تجديد", "قرض", "تمويل", 
    "بنك", "تسديد", "مخالفات", "اعلان", "إعلان", "قروب", "مجموعة", "انضم", 
    "رابط", "نشر", "قوانين", "احترام", "الذوق العام", "استقدام", "خادمات",
    "تعقيب", "معقب", "انجاز", "إنجاز", "كفيل", "نقل كفالة", "اسقاط", "تعديل مهنة",
    "حياك الله", "نورتنا", "انضمامك", "أهلاً بك", "اهلا بك", "قواعد المجموعة",
    "مرحباً بك", "مرحبا بك", "تنبيه", "محظور", "يُمنع", "يمنع", "بالتوفيق للجميع",
    "http", "t.me", ".com", "رابط القناة", "اخلاء مسؤولية", "ذمة",
    # الكلمات الجديدة المضافة:
    "استثمار", "زواج", "مسيار", "خطابه", "خطابة"
]

# قائمة 2: كلمات خارج السياق (طبي، أعذار، استفسارات عامة) - حظر فوري
IRRELEVANT_TOPICS = [
    "عيادة", "عياده", "اسنان", "أسنان", "دكتور", "طبيب", "مستشفى", "مستوصف",
    "علاج", "تركيب", "تقويم", "خلع", "حشو", "تنظيف", "استفسار", "افضل", "أفضل",
    "تجربة", "مين جرب", "رأيكم", "تنصحون", "ورشة", "سمكري", "قطع غيار",
    # الكلمات الجديدة المضافة:
    "عذر طبي", "سكليف", "سكليفات"
]


# ---------------------------------------------------------
# 2. المحرك الهجين (Hybrid Engine)
# ---------------------------------------------------------
async def analyze_message_hybrid(text):
    if not text or len(text) < 5 or len(text) > 400: 
        return False

    clean_text = normalize_text(text)
    
    # 1. الفلترة الفورية (الكلمات المحظورة)
    if any(k in clean_text for k in BLOCK_KEYWORDS + IRRELEVANT_TOPICS): 
        return False

    # 2. البرومبت العملاق المخصص للمدينة المنورة
    prompt = f"""
    Role: You are an elite AI Traffic Controller for the 'Madinah Taxi & Delivery' system. 
    Objective: Identify REAL CUSTOMERS in Al-Madinah Al-Munawwarah while ignoring drivers, ads, and spam.

    [CORE LOGIC]
    Return 'YES' ONLY if the sender is a HUMAN CUSTOMER seeking a ride or delivery.
    Return 'NO' if it's a driver offering service, an ad, or irrelevant talk.

    [📍 COMPREHENSIVE MADINAH GEOGRAPHY]
    Recognize any mention of these areas as a potential Madinah request:
    - Central & Holy Area: (Al-Haram, Al-Markazia, Al-Baqi, Bab Al-Salam, Bab Al-Majidi).
    - North: (Uhud, Sayh, Al-Raya, Al-Arid, Al-Azhari, Al-Ghaba, Bir Othman).
    - South: (Qurban, Al-Awali, Al-Hizam, Quba, Al-Jumu'ah, Shoran, Al-Hadiga).
    - West: (Al-Aziziyah, Al-Usayfirin, Al-Wabarah, Al-Duaithah, Al-Nasr, Al-Anisiyah).
    - East: (Al-Iskan, Al-Khalidiya, Al-Nakhil, Al-Rawabi, Al-Aql, Al-Ghara).
    - Landmarks: (Prophet's Mosque/Al-Haram, Prince Mohammad Bin Abdulaziz Airport MED, Haramain Train Station, Quba Mosque, Al-Qiblatain Mosque, Miqat Dhul Hulaifah, Mount Uhud, Taibah University, Islamic University).
    - Malls: (Al Rashid Mega Mall, Al Noor Mall, Alia Mall, Al Manar Mall).

    [✅ CLASSIFY AS 'YES' (CUSTOMER INTENT)]
    - Direct: "أبغا سواق"، "مطلوب كابتن"، "مين يوصلني للحرم"، "في أحد حول قطار المدينة؟"
    - Routes: "مشوار من العزيزية للراشد"، "من المطار للحرم"، "بكم توديني قباء؟"
    - Slang/Local: (أبغى، أبغا، فينك، كباتن، يوديني، يوصلني، دحين، حق مشوار، توصيلة).
    - Delivery: "أحتاج مندوب"، "توصيل غرض"، "أبغا أحد يجيب لي طلب من النور مول".

    [❌ CLASSIFY AS 'NO' (DRIVER/SPAM/ADS)]
    - Driver offers: "شغال الآن"، "موجود بالمدينة"، "سيارة نظيفة"، "توصيل مطار المدينة بأرخص الأسعار".
    - Keywords: (متواجد، متاح، أسعارنا، استقدام، عقار، سكليف، عذر طبي، قرض، باقات).

    Input Text: "{text}"

    FINAL ANSWER (Reply ONLY with 'YES' or 'NO'):
    """

    try:
        response = await asyncio.to_thread(ai_model.generate_content, prompt)
        result = response.text.strip().upper().replace(".", "").replace("'", "")
        
        if "YES" in result:
            print(f"✅ ذكاء اصطناعي: قبول طلب للمدينة المنورة")
            return True
        else:
            return False

    except Exception as e:
        print(f"⚠️ تجاوز AI (فشل الاتصال): {e}")
        return manual_fallback_check(clean_text)

def manual_fallback_check(clean_text):
    # كلمات الطلب والمدينة
    order_triggers = ["ابي", "ابغي", "أبغا", "محتاج", "مطلوب", "نبي", "مين يوديني"]
    madinah_keywords = ["سواق", "كابتن", "مشوار", "توصيل", "المدينة", "المدينه", "الحرم", "طيبة"]
    
    has_order = any(w in clean_text for w in order_triggers)
    has_keyword = any(w in clean_text for w in madinah_keywords)
    
    # فحص نمط "من ... إلى"
    has_route = "من" in clean_text and ("الى" in clean_text or "إلى" in clean_text or "لـ" in clean_text)
    
    return (has_order and has_keyword) or has_route

# ---------------------------------------------------------
# 3. [تعديل 2] دالة الإرسال للمستخدمين المحددين
# ---------------------------------------------------------
async def notify_users(detected_district, original_msg):
    content = original_msg.text or original_msg.caption
    if not content: return

    try:
        customer = original_msg.from_user
        bot_username = "Mishwariibot" 
        
        # ✅ استخدام "direct_" للسائقين المختارين لتجاوز فحص الاشتراك لاحقاً
        gateway_url = f"https://t.me/{bot_username}?start=direct_{customer.id}"

        buttons_list = [
            [InlineKeyboardButton("💬 مراسلة العميل الآن", url=gateway_url)],
        ]

        keyboard = InlineKeyboardMarkup(buttons_list)

        alert_text = (
            f"🎯 <b>طلب جديد تم التقاطه!</b>\n\n"
            f"📍 <b>المنطقة:</b> {detected_district}\n"
            f"👤 <b>اسم العميل:</b> {customer.first_name if customer else 'مخفي'}\n"
            f"📝 <b>نص الطلب:</b>\n<i>{content}</i>"
        )

        for user_id in TARGET_USERS:
            try:
                await bot_sender.send_message(
                    chat_id=user_id,
                    text=alert_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e_user:
                print(f"⚠️ فشل الإرسال للمستخدم {user_id}: {e_user}")

    except Exception as e:
        print(f"❌ خطأ عام في دالة الإرسال: {e}")

async def notify_channel(detected_district, original_msg):
    content = original_msg.text or original_msg.caption
    if not content: return

    # ... الكود السابق ...
    try:
        customer = original_msg.from_user
        customer_id = customer.id if customer else 0
        
        # ✅ نستخدم المتغير العام الذي عرفناه فوق
        gate_contact = f"https://t.me/{BOT_USERNAME}?start=chat_{customer_id}"

        buttons = [
            [InlineKeyboardButton("💬 مراسلة العميل (للمشتركين)", url=gate_contact)],
            [InlineKeyboardButton("💳 للاشتراك وتفعيل الحساب", url="https://t.me/x3FreTx")]
        ]   keyboard = InlineKeyboardMarkup(buttons)

        alert_text = (
            f"🎯 <b>طلب جديد تم التقاطه!</b>\n\n"
            f"📍 <b>المنطقة:</b> {detected_district}\n"
            f"👤 <b>اسم العميل:</b> {customer.first_name if customer else 'مخفي'}\n"
            f"📝 <b>نص الطلب:</b>\n<i>{content}</i>"
        )

        await bot_sender.send_message(
            chat_id=CHANNEL_ID,
            text=alert_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        print(f"✅ تم الإرسال للقناة برابط موحد (chat_): {detected_district}")

    except Exception as e:
        print(f"❌ خطأ إرسال للقناة: {e}")


# --- كلاس ودالة خادم الصحة (Health Check) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running")
    
    # لإيقاف ظهور سجلات الخادم المزعجة في التيرمينال
    def log_message(self, format, *args): 
        return

def run_health_server():
    # نستخدم البورت الذي يحدده Render أو 10000 كاحتياطي
    port = int(os.environ.get("PORT", 10000))
    print(f"🌍 تشغيل خادم الصحة على المنفذ: {port}")
    httpd = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    httpd.serve_forever()

# ---------------------------------------------------------
# 4. الرادار الرئيسي
# ---------------------------------------------------------
# --- [تطوير] معالج الرسائل الجديد (المستمع) ---
# هذا المعالج سيعمل تلقائياً عند وصول أي رسالة في المجموعات المشترك بها اليوزر بوت
@user_app.on_message(filters.group & ~filters.me)
async def message_handler(client, msg):
    try:
        text = msg.text or msg.caption
        if not text or len(text) < 5:
            return

        # 1. التحليل الأولي السريع (قبل استهلاك AI) لتوفير الموارد
        clean_text = normalize_text(text)
        
        # تخطي الرسائل التي تحتوي على كلمات محظورة فوراً
        if any(k in clean_text for k in BLOCK_KEYWORDS) or any(k in clean_text for k in IRRELEVANT_TOPICS):
            return

        # 2. التحليل الهجين (Hybrid)
        is_valid_order = await analyze_message_hybrid(text)

        if is_valid_order:
            # استخراج الحي
            found_d = "عام"
            text_c = normalize_text(text)
            for city, districts in CITIES_DISTRICTS.items():
                for d in districts:
                    if normalize_text(d) in text_c:
                        found_d = d
                        break

            # 3. إرسال الإشعارات
            # نستخدم create_task لضمان عدم توقف الرادار أثناء الإرسال
            asyncio.create_task(notify_users(found_d, msg))
            asyncio.create_task(notify_channel(found_d, msg))
            
            logging.info(f"✅ تم التقاط طلب جديد: {found_d}")

    except Exception as e:
        logging.error(f"⚠️ خطأ في معالجة الرسالة: {e}")


# --- [تطوير] معالج الرسائل الذكي ---
@user_app.on_message(filters.text & filters.group)
async def message_handler(client, msg):
    try:
        text = msg.text or msg.caption
        if not text or len(text) < 5:
            return

        # 1. التحليل الهجين (فلاتر + ذكاء اصطناعي)
        is_valid_order = await analyze_message_hybrid(text)

        if is_valid_order:
            # محاولة تحديد المنطقة من النص
            found_d = "جدة - عام"
            text_c = normalize_text(text)
            for city, districts in CITIES_DISTRICTS.items():
                for d in districts:
                    if normalize_text(d) in text_c:
                        found_d = d
                        break

            # 2. إرسال الإشعارات (استخدام asyncio.gather للسرعة)
            await asyncio.gather(
                notify_users(found_d, msg),
                notify_channel(found_d, msg)
            )
            logging.info(f"✅ تم تحويل طلب من: {msg.chat.title if msg.chat else 'Unknown'}")

    except Exception as e:
        logging.error(f"⚠️ خطأ في معالجة الرسالة: {e}")

# --- [تطوير] دالة التشغيل الرئيسية الموفرة للطاقة ---
# تأكد من استيراد ChatType في بداية الملف إذا لم يكن موجوداً

async def start_radar():
    print("🚀 بدء تشغيل الرادار...")
    try:
        # 1. تشغيل العميل
        await user_app.start()
        print("✅ تم اتصال اليوزر بوت بنجاح")

        # 2. 🔄 القراءة التلقائية للمجموعات (تحديث الكاش)
        print("⏳ جاري تحديث قائمة المجموعات...")
        group_count = 0
        async for dialog in user_app.get_dialogs():
            if dialog.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
                group_count += 1
        
        print(f"✅ الرادار يراقب الآن {group_count} مجموعة.")

        # 3. 🟢 تفعيل وضع الانتظار المستمر (Idle)
        # هذا السطر ضروري جداً لكي يعمل @user_app.on_message
        from pyrogram.methods.utilities.idle import idle
        await idle()

    except Exception as e:
        print(f"❌ خطأ في الرادار: {e}")
    finally:
        if user_app.is_connected:
            await user_app.stop()


# --- التشغيل الرئيسي ---
if __name__ == "__main__":
    # 1. تشغيل خادم الويب في خيط منفصل (Thread)
    # الآن الدالة run_health_server موجودة ولن يظهر خطأ
    threading.Thread(target=run_health_server, daemon=True).start()
    
    # 2. إعداد حلقة الأحداث (Loop) للرادار
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # 3. تشغيل الرادار
    try:
        loop.run_until_complete(start_radar())
    except (KeyboardInterrupt, SystemExit):
        print("👋 تم إيقاف الرادار يدوياً")
    except Exception as e:
        print(f"⚠️ خطأ غير متوقع في التشغيل: {e}")
