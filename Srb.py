import os
import logging
import threading
from flask import Flask
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from pypdf import PdfReader

# --- إعداد Flask ---
server = Flask(__name__)

@server.route('/')
def home():
    return "Bot is Running!"

def run_flask():
    # Render يعطي المنفذ (Port) عبر متغيرات البيئة
    port = int(os.environ.get("PORT", 8080))
    server.run(host='0.0.0.0', port=port)

# --- إعدادات تسجيل الأخطاء ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- مفاتيح الـ API ---
TELEGRAM_TOKEN = os.environ.get("8550229814:AAG2VkTm_ZBgUSXeQtOMYKFaqwnI95tvGJ4", "ضع_توكن_تلجرام_هنا")
GEMINI_API_KEY = os.environ.get("AIzaSyA3g-MQeBtMjRA57g6ainK71yJaelG1d_0", "ضع_مفتاح_جوجل_Gemini_هنا")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

user_context = {}

# (دوال start و handle_message و callback_handler تبقى كما هي في الكود السابق)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 أهلاً بك في البوت الطلابي المجاني!\nأرسل نصاً أو ملف PDF.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    text_content = ""
    if update.message.text:
        text_content = update.message.text
    elif update.message.document and update.message.document.file_name.endswith('.pdf'):
        msg = await update.message.reply_text("⏳ جاري قراءة ملف PDF...")
        file = await context.bot.get_file(update.message.document.file_id)
        path = f"{chat_id}.pdf"
        await file.download_to_drive(path)
        reader = PdfReader(path)
        for page in reader.pages:
            text_content += page.extract_text() + "\n"
        os.remove(path)
        await msg.delete()

    if text_content:
        user_context[chat_id] = {"text": text_content[:30000]}
        keyboard = [[InlineKeyboardButton("📝 تلخيص", callback_data="action_sum")],
                    [InlineKeyboardButton("❓ اختبار", callback_data="action_quiz")]]
        await update.message.reply_text("ماذا تريد أن أفعل؟", reply_markup=InlineKeyboardMarkup(keyboard))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    data = query.data
    await query.answer()
    
    if chat_id not in user_context: return
    content = user_context[chat_id]["text"]

    if data == "action_sum":
        res = model.generate_content(f"لخص هذا النص بالعربي:\n{content}")
        await query.message.reply_text(res.text)
    elif data == "action_quiz":
        res = model.generate_content(f"اصنع 5 أسئلة من هذا النص:\n{content}")
        await query.message.reply_text(res.text)

def main():
    # تشغيل Flask في خيط (Thread) منفصل
    threading.Thread(target=run_flask).start()

    # تشغيل البوت
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    print("البوت يعمل وخادم Flask نشط...")
    app.run_polling()

if __name__ == '__main__':
    main()
