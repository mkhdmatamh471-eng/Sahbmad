import asyncio
import threading
import sys
import os
import logging
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyrogram import Client
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
import google.generativeai as genai
from datetime import datetime, timezone
from flask import Flask  # أضف هذا السطر
import threading
from pyrogram import Client, filters  # تأكد من إضافة filters هنا
from pyrogram import enums

# --- إعداد السجلات ---
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# --- استيراد الإعدادات ---
# --- استيراد الإعدادات ودوال القاعدة ---
try:
    # أضف get_db_connection و release_db_connection هنا
    from config import (
        normalize_text, 
        CITIES_DISTRICTS, 
        BOT_TOKEN, 
        get_db_connection, 
        release_db_connection,
        init_db  # إذا كنت تستدعيها في الأسفل
    )
    print("✅ تم تحميل الإعدادات ودوال قاعدة البيانات بنجاح")
except Exception as e:
    print(f"❌ خطأ في تحميل ملف config.py: {e}")
    sys.exit(1)


# --- متغيرات البيئة ---
API_ID = os.environ.get("API_ID", "36360458")
API_HASH = os.environ.get("API_HASH", "daae4628b4b4aac1f0ebfce23c4fa272")
SESSION_STRING = os.environ.get("SESSION_STRING", "BAIq0QoAhqQ7maNFOf6AUKx6sP1-w-GnmTM4GCyqL0INirrOO99rgvLN38CRda5n7P4vstDSL8lBamXl5i8urauRc3Zpq54NJsBdJyNy8pqhp9KzAGDoE1Lveo78y_81h81QYcn_7NQeMQIJLM5uw3S2XPnzYif7y_LYewcx15ZY_kgKWOE4mx0YZvt4V_8h3_zSSVsAWvY3rz_H0TmknpCgczsXx6XfhW90CekcU0-nH39h9ocdtYy6uJ9cXDqsHFf45wSwL5A9tuQNRTzbwe6uIrNTWwNzz86O7jysD53YEeV2zCx625iXuoDYy3b6YJnHzgGmKRpdts7LzrGEoOanUDLYSgAAAAH-ZrzOAA")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyA3g-MQeBtMjRA57g6ainK71yJaelG1d_0")

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
# اذهب إلى قسم إعداد Gemini وغير هذا السطر:
ai_model = genai.GenerativeModel(
    model_name='gemini-1.5-flash-latest', # أضف كلمة latest
    generation_config=generation_config
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
    if not text or len(text) < 5 or len(text) > 400: return False

    clean_text = normalize_text(text)
    route_pattern = r"(^|\s)من\s+.*?\s+(إلى|الى|لـ|للحرم|للمطار)(\s|$)"
    if re.search(route_pattern, clean_text):
        return True 

    if any(k in clean_text for k in BLOCK_KEYWORDS): return False
    if any(k in clean_text for k in IRRELEVANT_TOPICS): return False

        # البرومبت الشامل (The Master Prompt)
    prompt = f"""
    Role: You are an elite AI Traffic Controller for a specific 'Madinah Taxi & Delivery' Telegram group.
    Objective: Filter messages to identify REAL CUSTOMERS seeking services (Rides, Delivery, School Transport).
    
    [STRICT ANALYSIS RULES]
    You must classify the "Intent" of the sender.
    - SENDER = CUSTOMER (Needs service) -> Reply 'YES'
    - SENDER = DRIVER (Offers service) -> Reply 'NO'
    - SENDER = SPAM/CHATTER -> Reply 'NO'

    [✅ CLASSIFY AS 'YES' (CUSTOMER REQUESTS)]
    1. Explicit Ride Requests: (e.g., "أبغى سواق", "مطلوب كابتن", "سيارة للحرم", "مين يوديني؟").
    2. Route Descriptions (Implicit): Text mentioning a destination or path (e.g., "من العزيزية للحرم", "مشوار للمطار", "إلى الراشد مول").
    3. Location Pings (Incomplete Requests): If someone just names a location implies they need a driver there (e.g., "حي شوران؟", "أحد حول العالية؟", "في كباتن في الهجرة؟").
    4. School & Monthly Contracts: (e.g., "توصيل مدارس", "نقل طالبات", "عقد شهري", "توصيل دوام").
    5. Delivery & Logistics: Requests to move items (e.g., "توصيل غرض", "توصيل مفتاح", "طلبية من زاجل", "توصيل أكل").
    6. Price Inquiries by Customer: (e.g., "بكم المشوار للمطار؟", "توديني بـ 20؟").

    [❌ CLASSIFY AS 'NO' (IGNORE THESE)]
    1. Driver Offers (Supply): Any text indicating the sender IS a driver (e.g., "متواجد", "جاهز للتوصيل", "سيارة حديثة", "توصيل مشاوير", "على مدار الساعة", "الخاص مفتوح").
    2. Social & Religious: Greetings, prayers, wisdom (e.g., "صباح الخير", "جمعة مباركة", "سبحان الله", "دعاء", "حكم").
    3. Forbidden Spam Topics: 
       - Medical Excuses (e.g., "سكليف", "عذر طبي", "اجازة مرضية").
       - Marriage/Social (e.g., "خطابة", "زواج مسيار", "تعارف").
       - Financial/Real Estate (e.g., "قروض", "أرض للبيع", "استثمار").
    4. General Chat/Admin: Questions about rules, links, or weather.

    [📍 MADINAH CONTEXT KNOWLEDGE]
    Treat these as valid locations implying a request if mentioned alone:
    (Haram, Airport, Train Station, Aziziya, Shoran, Awali, Hijra, Baqdo, Quba, Sultana, Rashid Mall, Al-Noor, Taiba).

    [DECISION LOGIC]
    - "From A to B" -> YES
    - "I am available" -> NO
    - "School delivery needed" -> YES
    - "Sick leave for sale" -> NO
    - "Who is in Shoran?" -> YES

    Input Text: "{text}"

    FINAL ANSWER (Reply ONLY with 'YES' or 'NO'):
    """

    try:
        response = await asyncio.to_thread(ai_model.generate_content, prompt)
        result = response.text.strip().upper().replace(".", "")
        return "YES" in result
    except Exception as e:
        print(f"⚠️ تجاوز AI (فشل الاتصال): {e}")
        return manual_fallback_check(clean_text)

def manual_fallback_check(clean_text):
    order_words = ["ابي", "ابغي", "محتاج", "نبي", "مطلوب", "بكم"]
    service_words = ["سواق", "توصيل", "مشوار", "يوديني", "يوصلني"]
    has_order = any(w in clean_text for w in order_words)
    has_service = any(w in clean_text for w in service_words)
    has_route = "من " in clean_text and ("الى" in clean_text or "لي" in clean_text)
    return (has_order and has_service) or has_route

# ---------------------------------------------------------
# 3. [تعديل 2] دالة الإرسال للمستخدمين المحددين
# ---------------------------------------------------------

# ---------------------------------------------------------
# دالة البث للسائقين (معدلة للنظام الجديد - تستقبل بيانات نصية)
# ---------------------------------------------------------
async def broadcast_order_to_drivers(district, content, cust_id, cust_name):
    print(f"📢 البوت يبدأ معالجة البث لحي: {district}")

    # 1. تجهيز رابط التواصل باستخدام الآيدي القادم من البيانات
    contact_url = f"tg://user?id={cust_id}"
    
    # 2. تجهيز نص الرسالة
    base_text = (
        f"🎯 <b>طلب مشوار جديد</b>\n"
        f"📍 الحي: {district}\n"
        f"👤 العميل: {cust_name}\n"
        f"📝 التفاصيل: {content}\n"
    )

    conn = get_db_connection()
    if not conn:
        print("❌ فشل الاتصال بالقاعدة")
        return

    try:
        with conn.cursor() as cur:
            # جلب السائقين النشطين فقط
            cur.execute("""
                SELECT user_id, subscription_expiry 
                FROM users 
                WHERE is_blocked = FALSE AND TRIM(LOWER(role)) = 'driver'
            """)
            drivers = cur.fetchall()
            
            print(f"👥 جاري الإرسال لـ {len(drivers)} سائق...")

            for user_id, expiry in drivers:
                is_active = False
                if expiry:
                    # التأكد من التوقيت
                    now = datetime.now(timezone.utc)
                    is_active = (expiry > now)

                # تحديد الزر بناءً على الاشتراك
                if is_active:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💬 مراسلة العميل", url=contact_url)]])
                    footer = "\n✅ اشتراكك فعال"
                else:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💳 اشترك لتفعيل المراسلة", url="https://t.me/x3FreTx")]])
                    footer = "\n⚠️ التواصل للمشتركين فقط"

                try:
                    await bot_sender.send_message(
                        chat_id=int(user_id),
                        text=base_text + footer,
                        reply_markup=kb,
                        parse_mode=ParseMode.HTML,
                        disable_notification=False
                    )
                    # طباعة للتأكد (اختياري يمكن إزالته لتنظيف التيرمينال)
                    # print(f"✅ تم الإرسال للسائق: {user_id}")
                except Exception as e:
                    print(f"⚠️ تخطي السائق {user_id}: {e}")
                
                # تأخير بسيط جداً لمنع الحظر (FloodWait)
                await asyncio.sleep(0.05)

    except Exception as e:
        print(f"❌ خطأ عام في دالة البث: {e}")
    finally:
        release_db_connection(conn)
        print("🏁 انتهت عملية البث للسائقين.")


# ---------------------------------------------------------
# دالة الإرسال للقناة (معدلة للنظام الجديد)
# ---------------------------------------------------------
async def notify_channel(district, content, cust_id):
    if not content: return

    try:
        # الإعدادات
        bot_username = "Mishwariibot" 

        # تجهيز الروابط العميقة (Deep Links) باستخدام الآيدي النصي
        gate_contact = f"https://t.me/{bot_username}?start=contact_{cust_id}"

        buttons = [
            [InlineKeyboardButton("💬 مراسلة العميل (للمشتركين)", url=gate_contact)],
            [InlineKeyboardButton("💳 للاشتراك وتفعيل الحساب", url="https://t.me/x3FreTx")]
        ]

        keyboard = InlineKeyboardMarkup(buttons)

        # ملاحظة: تم إزالة رابط المصدر (gate_source) لأنه يحتاج آيدي الرسالة الأصلية 
        # واليوزر بوت يرسل البيانات كنص جديد، لكن الرابط الأساسي (المراسلة) يعمل 100%
        alert_text = (
            f"🎯 <b>طلب مشوار جديد</b>\n\n"
            f"📍 <b>المنطقة:</b> {district}\n"
            f"📝 <b>التفاصيل:</b>\n<i>{content}</i>\n\n"
            f"⏰ <b>الوقت:</b> {datetime.now().strftime('%H:%M:%S')}\n"
            f"⚠️ <i>الروابط أعلاه تفتح للمشتركين فقط.</i>"
        )

        await bot_sender.send_message(
            chat_id=CHANNEL_ID,
            text=alert_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        print(f"✅ تم الإرسال للقناة بنجاح: {district}")

    except Exception as e:
        print(f"❌ خطأ إرسال للقناة: {e}")

# ---------------------------------------------------------
# 4. الرادار الرئيسي
# ---------------------------------------------------------
# بدلاً من الحلقة القديمة، نستخدم Decorator لالتقاط الرسائل فور وصولها
@# ---------------------------------------------------------
# 4. الرادار الرئيسي (المعدل)
# ---------------------------------------------------------
@user_app.on_message(filters.group & ~filters.service)
async def handle_new_messages(client, message):
    # طباعة اسم الجروب للتأكد من المتابعة
    chat_title = message.chat.title or "جروب غير معروف"
    print(f"📥 الرادار استلم رسالة من [{chat_title}]")

    try:
        text = message.text or message.caption
        if not text or (message.from_user and message.from_user.is_self):
            return

        # التحليل بالذكاء الاصطناعي
        is_valid = await analyze_message_hybrid(text)

        if is_valid:
            # استخراج الحي
            found_d = "عام"
            text_c = normalize_text(text)
            for city, districts in CITIES_DISTRICTS.items():
                for d in districts:
                    if normalize_text(d) in text_c:
                        found_d = d
                        break
            
            # --- [التغيير الجوهري] ---
            # بدلاً من الإرسال المباشر، نرسل البيانات للبوت الموزع
            customer = message.from_user
            
            # تجهيز "حزمة بيانات" مشفرة ليستلمها البوت
            # نستخدم فواصل واضحة لنتمكن من فصل البيانات لاحقاً
            transfer_data = (
                f"#ORDER_DATA#\n"
                f"DISTRICT:{found_d}\n"
                f"CUST_ID:{customer.id}\n"
                f"CUST_NAME:{customer.first_name}\n"
                f"CONTENT:{text}"
            )
            
            # ⚠️ هام جداً: استبدل YOUR_BOT_USERNAME بمعرف بوتك (بدون @)
            # مثال: await user_app.send_message("TaxiMadinahBot", transfer_data)
            await user_app.send_message("Mishwariibot", transfer_data) 
            
            print(f"✅ تم تحويل الطلب ({found_d}) إلى البوت الموزع.")

    except Exception as e:
        logging.error(f"Error handling message: {e}")

# ---------------------------------------------------------
# 5. معالج البوت الموزع (يستقبل من الرادار ويوزع)
# ---------------------------------------------------------
@bot_sender.on_message(filters.private & filters.regex("#ORDER_DATA#"))
async def bot_distribution_handler(client, message):
    try:
        # تقطيع البيانات المستلمة من اليوزر بوت
        data = message.text.split("\n")
        
        # استخراج القيم (تعتمد على الترتيب الذي وضعناه في الرادار)
        # DISTRICT:Name -> نأخذ ما بعد النقطتين
        district = data[1].split(":", 1)[1]
        cust_id = data[2].split(":", 1)[1]
        cust_name = data[3].split(":", 1)[1]
        # المحتوى قد يحتوي على أسطر جديدة، لذا نأخذ الباقي
        content = data[4].split(":", 1)[1]

        print(f"🤖 البوت الموزع استلم طلباً لحي: {district}")

        # تشغيل البث في الخلفية (Parallel Tasks)
        # لاحظ أننا نمرر البيانات كنصوص الآن وليس كأوبجكت رسالة
        asyncio.create_task(broadcast_to_drivers_logic(district, content, cust_id, cust_name))
        asyncio.create_task(notify_channel_logic(district, content, cust_id))
        
    except Exception as e:
        print(f"❌ خطأ في معالج التوزيع بالبوت: {e}")

# دالة التشغيل التي تضمن بقاء العميل متصلاً
async def main_run():
    print("🚀 جاري تشغيل النظام (الرادار + البوت الموزع)...")
    
    # تشغيل الاثنين معاً
    await user_app.start()
    await bot_sender.start()
    
    print("✅ النظام يعمل! الرادار يراقب، والبوت جاهز للتوزيع.")
    
    # كود قراءة الجروبات (للرادار)
    try:
        async for dialog in user_app.get_dialogs(limit=None):
            if dialog.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
                pass 
        print("📋 تم تحديث قائمة الجروبات.")
    except Exception as e:
        print(f"⚠️ تنبيه مزامنة: {e}")
        
    await asyncio.Event().wait()

# --- خادم الويب (Health Check) ---
app = Flask(__name__)

@app.route('/')
def home():
    # هذه الرسالة ستظهر عند فتح رابط البوت على المتصفح
    return "Bot is Running Live!", 200

def run_flask():
    # Render يمرر المنفذ تلقائياً عبر متغير البيئة PORT
    port = int(os.environ.get("PORT", 10000))
    # تشغيل الفلاسك على 0.0.0.0 ضروري ليعمل على السيرفر
    app.run(host='0.0.0.0', port=port)


if __name__ == "__main__":
    # 1. تشغيل الفلاسك في خلفية منفصلة تماماً
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 2. تهيئة القاعدة
    try:
        init_db()
    except:
        pass

    # 3. تشغيل العميل بأمان
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main_run())
    except KeyboardInterrupt:
        loop.run_until_complete(user_app.stop())
    finally:
        loop.close()
