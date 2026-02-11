import asyncio
import sys
import os
import logging
from pyrogram import Client, filters 
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
import google.generativeai as genai
from pyrogram.enums import ChatType

# --- إعداد السجلات ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

# --- استيراد الإعدادات الخارجية ---
try:
    from config import normalize_text, CITIES_DISTRICTS, BOT_TOKEN
    print("✅ تم تحميل الإعدادات من config.py بنجاح")
except Exception as e:
    print(f"❌ خطأ فادح: فشل تحميل config.py. تأكد من وجود الملف. التفاصيل: {e}")
    sys.exit(1)

# --- متغيرات البيئة ---
API_ID = os.environ.get("API_ID", "36360458")
API_HASH = os.environ.get("API_HASH", "daae4628b4b4aac1f0ebfce23c4fa272")
SESSION_STRING = os.environ.get("SESSION_STRING", "BAIq0QoAhqQ7maNFOf6AUKx6sP1-w-GnmTM4GCyqL0INirrOO99rgvLN38CRda5n7P4vstDSL8lBamXl5i8urauRc3Zpq54NJsBdJyNy8pqhp9KzAGDoE1Lveo78y_81h81QYcn_7NQeMQIJLM5uw3S2XPnzYif7y_LYewcx15ZY_kgKWOE4mx0YZvt4V_8h3_zSSVsAWvY3rz_H0TmknpCgczsXx6XfhW90CekcU0-nH39h9ocdtYy6uJ9cXDqsHFf45wSwL5A9tuQNRTzbwe6uIrNTWwNzz86O7jysD53YEeV2zCx625iXuoDYy3b6YJnHzgGmKRpdts7LzrGEoOanUDLYSgAAAAH-ZrzOAA")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyDvEF8WDhGt6nDWjqxgix0Rb8qaAmtEPbk")
BOT_USERNAME = "Mishwariibot" 
CHANNEL_ID = -1003763324430 

# قائمة الـ IDs الذين يستلمون الطلبات في الخاص
TARGET_USERS = [
    8563113166, 7897973056, 8123777916, 8181237063, 8246402319, 
    6493378017, 7068172120, 1658903455, 1506018292, 1193267455, 
    627214092, 336092598, 302374285, 987654321
]

# --- إعداد Gemini ---
genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config={"temperature": 0.1, "max_output_tokens": 10}
)

# --- عملاء تليجرام ---
user_app = Client("my_session", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
bot_sender = Bot(token=BOT_TOKEN)

# --- قوائم الفلترة الصارمة ---
BLOCK_KEYWORDS = [
    "متواجد الآن", "شغال الآن", "سيارة نظيفة", "أسعارنا", "بخدمتكم", "أوصل مشاوير", 
    "واتساب", "نقل عفش", "سطحة", "تأمين", "قرض", "تمويل", "خادمات", "عقار", 
    "مسيار", "خطابة", "تجديد", "معقب"
]

IRRELEVANT_TOPICS = [
    "عذر طبي", "سكليف", "تقويم اسنان", "قطع غيار", "سمكري", "افضل دكتور", "مين جرب"
]

# ---------------------------------------------------------
# المحرك الذكي للفلترة والتحليل
# ---------------------------------------------------------

def manual_fallback_check_madinah(clean_text):
    """فحص يدوي سريع للكلمات المفتاحية للمدينة المنورة"""
    order_triggers = ["ابي", "أبغا", "محتاج", "مطلوب", "يوصلني", "بكم", "مشوار", "كابتن"]
    madinah_keywords = [
        "الحرم", "النبوي", "العزيزية", "شوران", "الهجرة", "العوالي", "قربان", 
        "سلطانة", "القبلتين", "المطار", "القطار", "قباء", "احد", "الراشد", "النور"
    ]
    
    has_order = any(w in clean_text for w in order_triggers)
    has_geo = any(w in clean_text for w in madinah_keywords)
    
    # التقاط صيغ الروابط (من.. إلى..)
    has_route = "من" in clean_text and any(x in clean_text for x in ["الى", "إلى", "لـ"])
    
    return (has_order and has_geo) or has_route

async def analyze_message_hybrid(text):
    """تحليل هجين: فلاتر -> يدوي -> ذكاء اصطناعي"""
    if not text or len(text) < 8: return False
    
    clean_text = normalize_text(text)
    
    # 1. استبعاد الإعلانات والسبام فوراً
    if any(k in clean_text for k in BLOCK_KEYWORDS + IRRELEVANT_TOPICS): 
        return False

    # 2. الفحص اليدوي (يوفر استهلاك الـ AI)
    if manual_fallback_check_madinah(clean_text):
        return True

    # 3. الاستعانة بالذكاء الاصطناعي للحالات غير الواضحة
    prompt = f"Is this text a customer asking for a taxi/delivery in Madinah? Answer YES or NO only. Text: '{text}'"
    try:
        response = await asyncio.to_thread(ai_model.generate_content, prompt)
        return "YES" in response.text.upper()
    except Exception as e:
        logging.error(f"⚠️ AI Error: {e}")
        return False

# ---------------------------------------------------------
# نظام الإشعارات الموحد
# ---------------------------------------------------------

async def notify_all(detected_district, msg):
    """إرسال الإشعارات للمستخدمين والقناة في وقت واحد"""
    content = msg.text or msg.caption
    customer = msg.from_user
    if not customer: return

    # روابط التواصل
    direct_url = f"https://t.me/{BOT_USERNAME}?start=direct_{customer.id}"
    channel_sub_url = "https://t.me/x3FreTx"

    alert_text = (
        f"🎯 <b>طلب جديد في المدينة المنورة!</b>\n\n"
        f"📍 <b>المنطقة:</b> {detected_district}\n"
        f"👤 <b>العميل:</b> {customer.first_name}\n"
        f"📝 <b>الطلب:</b>\n<i>{content}</i>"
    )

    # مهام الإرسال
    tasks = []
    
    # إرسال للمستخدمين المستهدفين
    kb_user = InlineKeyboardMarkup([[InlineKeyboardButton("💬 مراسلة العميل الآن", url=direct_url)]])
    for user_id in TARGET_USERS:
        tasks.append(bot_sender.send_message(user_id, alert_text, reply_markup=kb_user, parse_mode=ParseMode.HTML))

    # إرسال للقناة
    kb_chan = InlineKeyboardMarkup([[InlineKeyboardButton("💳 للاشتراك وتفعيل الحساب", url=channel_sub_url)]])
    tasks.append(bot_sender.send_message(CHANNEL_ID, alert_text, reply_markup=kb_chan, parse_mode=ParseMode.HTML))

    # تنفيذ الإرسال بشكل متوازي لسرعة البرق
    results = await asyncio.gather(*tasks, return_exceptions=True)
    logging.info(f"✅ تم معالجة إرسال الطلب لـ {len(results)} وجهة.")

# ---------------------------------------------------------
# الرادار (المستمع الرئيسي)
# ---------------------------------------------------------

@user_app.on_message(filters.group & ~filters.me)
async def radar_handler(client, msg):
    try:
        text = msg.text or msg.caption
        if not text: return

        if await analyze_message_hybrid(text):
            # تحديد الحي
            found_district = "المدينة (عام)"
            text_normalized = normalize_text(text)
            
            # محاولة مطابقة الحي من القائمة في config
            for city, districts in CITIES_DISTRICTS.items():
                for d in districts:
                    if normalize_text(d) in text_normalized:
                        found_district = d
                        break

            # تشغيل مهمة الإرسال في الخلفية لعدم تعطيل الرادار
            asyncio.create_task(notify_all(found_district, msg))

    except Exception as e:
        logging.error(f"⚠️ خطأ في الرادار: {e}")

# ---------------------------------------------------------
# دالة التشغيل (نظيفة ومتوافقة مع Python 3.13)
# ---------------------------------------------------------

async def start_radar():
    print("🚀 جاري تشغيل رادار المدينة المنورة...")
    try:
        await user_app.start()
        print("✅ تم اتصال اليوزر بوت بنجاح.")
        
        # إحصائية سريعة للمجموعات
        group_count = 0
        async for dialog in user_app.get_dialogs():
            if dialog.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
                group_count += 1
        
        print(f"📡 الرادار يراقب الآن {group_count} مجموعة.")
        print("🟢 البوت يعمل الآن (اعتمد على UptimeRobot للبقاء متيقظاً).")
        
        from pyrogram.methods.utilities.idle import idle
        await idle()
        
    except Exception as e:
        print(f"❌ خطأ فادح أثناء التشغيل: {e}")
    finally:
        if user_app.is_connected:
            await user_app.stop()

if __name__ == "__main__":
    try:
        # استخدام asyncio.run يضمن وجود Event Loop واحد فقط ويحل خطأ Different Loop
        asyncio.run(start_radar())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 تم إيقاف الرادار يدوياً.")
    except Exception as e:
        print(f"⚠️ فشل النظام: {e}")
        sys.exit(1)
