import asyncio
import os
import re
import logging
import threading # أضف هذا للـ Flask
import google.generativeai as genai
from pyrogram import Client, filters
from flask import Flask # للتأكد من وجودها
# تأكد أن ملف config.py يحتوي على normalize_text و CITIES_DISTRICTS
from config import normalize_text, CITIES_DISTRICTS 
from pyrogram import Client, filters, idle, enums  # أضف enums هنا
 
# --- إعدادات الهوية ---


# --- متغيرات البيئة ---
API_ID = os.environ.get("API_ID", "36360458")
API_HASH = os.environ.get("API_HASH", "daae4628b4b4aac1f0ebfce23c4fa272")
SESSION_STRING = os.environ.get("SESSION_STRING","BAIq0QoAX5mya6NENJII4U3gDEzpysbtycl61XAyJjoPoPyNzEhH4bhG9XwwT4AEbNP3FNlsmufFDkGf4ksTGWf6KNOOOlqNjrq7lGsnG88l2ZYeAkCCcM5TYtnfkjPVg5m1wuG7IwDlsg-ozJIeqnhB7PXr3RNnJegM8_VLBcmbIdso9l79SenaqhYp1Kqsfpew42KG6hXvtFc8shQHOhF2PTUWwm_pk144uE3_lu9bUim2O9dO1nER2L972CXa4tFoEW7x96jt4nod-FtC1Tkmob5KY6gxI0d_pp6eAxoLMt1BatECz-ttZVJ4tNpcxLDytuOlS4Edto_iHylF9nuxqzLPIQAAAAH-ZrzOAA")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyA3g-MQeBtMjRA57g6ainK71yJaelG1d_0")
BOT_USERNAME = "Mishwariibot" 
# ---------------------------------------------------------
# 🛠️ [تعديل 1] قائمة المستخدمين الذين سيستلمون الطلبات
# ضع الـ IDs الخاصة بهم هنا (أرقام فقط)
# ---------------------------------------------------------
# 🛠️ قائمة الـ IDs المحدثة الذين سيستلمون الطلبات في الخاص (مفتوحة)
 # <--- ضع الآيديات الحقيقية هنا

TARGET_CHATS = [
    -1002066080450, -1001236223360, -1002112114167, -1001199555920, 
    -1002521083369, -1001653442381, -1001484510620, -1001615555209, 
    -1001801366018, -1001333159209, -1002425448607, -1001442812315, 
    -1001419990293, -1002197678343, -1001671410526, -1001406320324
]


# --- إعداد Gemini 1.5 Flash ---
genai.configure(api_key=GEMINI_API_KEY)
generation_config = {
  "temperature": 0.1,
  "top_p": 0.95,
  "top_k": 40,
  "max_output_tokens": 5,
}
# اذهب إلى قسم إعداد Gemini وغير هذا السطر:
# التعديل الصحيح لضمان عدم حدوث خطأ 404
Ai_model = genai.GenerativeModel(
    model_name='gemini-1.5-flash', # احذف كلمة -latest لضمان الاستقرار
    generation_config=generation_config
)


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


# بدلاً من الفلتر القديم، استخدم هذا للاختبار:
# استخدم هذا الفلتر الشامل
@user_app.on_message(filters.group & ~filters.service)
async def handle_new_messages(client, message):
    try:
        # --- سطر الاختبار (تأكد من ظهوره في اللوج) ---
        print(f"📥 استلمت رسالة من: {message.chat.title} | النص: {message.text[:30]}...")

        text = message.text or message.caption
        # تجاهل الرسائل الفارغة أو رسائل الحساب نفسه
        if not text or (message.from_user and message.from_user.is_self):
            return

        # 1. التحليل بالذكاء الاصطناعي
        is_valid = await analyze_message_hybrid(text)
        
        # سطر إضافي للتأكد من نتيجة الذكاء الاصطناعي
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
            customer = message.from_user
            transfer_data = (
                f"#ORDER_DATA#\n"
                f"DISTRICT:{found_d}\n"
                f"CUST_ID:{customer.id}\n"
                f"CUST_NAME:{customer.first_name if customer.first_name else 'عميل'}\n"
                f"CONTENT:{text}"
            )

            # إرسال لبوت التوزيع
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



import asyncio
from pyrogram import idle

async def main():
    print("🚀 بدء تشغيل الرادار (جدة)...")
    try:
        # تشغيل اليوزر بوت
        if not user_app.is_connected:
            await user_app.start()
        
        print("✅ اليوزر بوت متصل ومستقر!")
        
        # تشغيل الفلاسك في خلفية الحلقة (اختياري إذا كنت تستخدم threading)
        # إذا كنت تستخدم threading.Thread لـ Flask، تأكد أنها تبدأ قبل asyncio.run

        # السطر السحري لمنع تصادم الـ Loops
        await idle() 

    except Exception as e:
        print(f"❌ خطأ في main: {e}")
    finally:
        if user_app.is_connected:
            await user_app.stop()

if __name__ == "__main__":
    # 1. تشغيل سيرفر الصحة (Flask) في خيط منفصل تماماً
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 2. تشغيل الحلقة الأساسية لـ Pyrogram بشكل نظيف
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"⚠️ خطأ فادح في التشغيل: {e}")
