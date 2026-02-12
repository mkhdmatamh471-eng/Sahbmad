import asyncio
import os
import re
import logging
import threading
import google.generativeai as genai
from flask import Flask

# --- استيرادات Telethon ---
from telethon import TelegramClient, events, sync
from telethon.sessions import StringSession

# تأكد أن ملف config.py يحتوي على normalize_text و CITIES_DISTRICTS
from config import normalize_text, CITIES_DISTRICTS 

# --- متغيرات البيئة ---
API_ID = os.environ.get("API_ID", "36360458")
API_HASH = os.environ.get("API_HASH", "daae4628b4b4aac1f0ebfce23c4fa272")
# ⚠️ انتبه: كود جلسة Pyrogram لا يعمل هنا. قم بتوليد كود جديد أو تسجيل الدخول لأول مرة.
SESSION_STRING = os.environ.get("TELETHON_SESSION", "1BJWap1sBuyfIQ9CyhEsZ-f9Xo4W1pr24lihTxGhG_Lrkv25fXoe_HFNLnH0KFqQiXYsMuR_8gzff_3pZLDXF4Q8VUCAQdH_TA_x4z7P8byAP4gTJUc6SNucFy6bznjDHSBnJZht4rrrrwUU9wSeQvsvmP0imFJMFhutiX91CxHYLZVWivexnRXb5h8r_0szwlll1-nbULa7yTc7zx7R2AxcpwRGhGfDCz75HfAKx-YJ9LJZPqU5_dEvyFoC2LssEakTy_gl2tgU9Hy2dLq8HL6Bu-K6GugoAZ6tC83znjckwk_DgWeU9kwOYOms3amFf54JdIf7ML25n9zSkM9WaSR-C_9FD3n4=") 
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyA3g-MQeBtMjRA57g6ainK71yJaelG1d_0")
BOT_USERNAME = "Mishwariibot" 

# --- إعداد Gemini (كما هو) ---
genai.configure(api_key=GEMINI_API_KEY)
generation_config = {
  "temperature": 0.1,
  "top_p": 0.95,
  "top_k": 40,
  "max_output_tokens": 5,
}

Ai_model = genai.GenerativeModel(
    model_name='gemini-1.5-flash', 
    generation_config=generation_config
)

# --- إعداد عميل Telethon ---
if SESSION_STRING:
    # استخدام كود الجلسة إذا وجد
    client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
else:
    # سيطلب منك تسجيل الدخول وإنشاء ملف .session عند التشغيل الأول
    client = TelegramClient('radar_session', int(API_ID), API_HASH)


# --- عملاء تليجرام ---
# هذا هو المحرك الوحيد المطلوب في سيرفر الرادار
user_app = Client("my_session", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

# سطر bot_sender = Bot(token=BOT_TOKEN) قم بحذفه من هنا

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
        # تأكد من استخدام Ai_model هنا (نفس الاسم المعرف في الأعلى)
        response = await asyncio.to_thread(Ai_model.generate_content, prompt)
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


# بدلاً من الفلتر القديم، استخدم هذا للاختبار:
# استخدم هذا الفلتر الشامل
# الفلتر في تيليثون لاستقبال رسائل المجموعات فقط (القادمة Incoming)
@client.on(events.NewMessage(incoming=True))
async def handle_new_messages(event):
    # التحقق من أن الرسالة في مجموعة (Group أو Supergroup)
    if not event.is_group:
        return

    try:
        # الحصول على كائن المحادثة والنص
        chat = await event.get_chat()
        text = event.raw_text # في تيليثون نستخدم raw_text
        
        # تجاهل الرسائل الفارغة
        if not text:
            return

        # سطر الاختبار
        print(f"📥 استلمت رسالة من: {chat.title} | النص: {text[:30]}...")

        # 1. التحليل بالذكاء الاصطناعي
        is_valid = await analyze_message_hybrid(text)
        print(f"🧐 نتيجة تحليل الذكاء الاصطناعي: {is_valid}")

        if is_valid:
            # 2. استخراج الحي
            found_d = "عام"
            text_c = normalize_text(text)
            for city, districts in CITIES_DISTRICTS.items():
                for d in districts:
                    if normalize_text(d) in text_c:
                        found_d = d
                        break

            # 3. إرسال البيانات للبوت
            sender = await event.get_sender()
            sender_id = sender.id if sender else 0
            sender_name = sender.first_name if sender and sender.first_name else "عميل"

            transfer_data = (
                f"#ORDER_DATA#\n"
                f"DISTRICT:{found_d}\n"
                f"CUST_ID:{sender_id}\n"
                f"CUST_NAME:{sender_name}\n"
                f"CONTENT:{text}"
            )

            # إرسال لبوت التوزيع (تيليثون يستخدم send_message أيضاً)
            # ملاحظة: يجب أن يكون الرادار قد راسل البوت سابقاً أو يعرفه
            await client.send_message(BOT_USERNAME, transfer_data) 
            print(f"✅ [رادار] تم قنص طلب في ({found_d}) وتحويله للبوت.")

    except Exception as e:
        print(f"⚠️ خطأ في معالجة الرسالة: {e}")

# ---------------------------------------------------------
# 5. معالج البوت الموزع (يستقبل من الرادار ويوزع)
# ---------------------------------------------------------

# دالة التشغيل التي تضمن بقاء العميل متصلاً

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

# تأكد من وجود هذا الاستيراد



async def main():
    print("🚀 بدء تشغيل الرادار الشامل (تيليثون)...")
    try:
        # بدء الاتصال
        await client.start()
        
        print("✅ اليوزر بوت متصل. جاري مزامنة المجموعات...")
        
        # --- حل مشكلة المجموعات الكبيرة في تيليثون ---
        # iter_dialogs يقوم بتحديث الذاكرة والوصول لكل المجموعات
        async for dialog in client.iter_dialogs():
            pass # مجرد المرور عليها يكفي لتفعيل الاستقبال
        
        print(f"🚀 الرادار يراقب الآن جميع المجموعات (خاصة + عامة) بنجاح!")
        
        # التشغيل المستمر في تيليثون
        await client.run_until_disconnected()

    except Exception as e:
        print(f"❌ خطأ في main: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    # 1. تشغيل سيرفر الصحة (Flask)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 2. تشغيل الحلقة الأساسية
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"⚠️ خطأ فادح في التشغيل: {e}")
