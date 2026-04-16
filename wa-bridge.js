const express = require('express');
const cors = require('cors');
const axios = require('axios');
const { 
    default: makeWASocket, 
    DisconnectReason, 
    initAuthCreds, 
    BufferJSON, 
    proto,
    fetchLatestBaileysVersion 
} = require("@whiskeysockets/baileys");
const { Boom } = require("@hapi/boom");
const pino = require("pino");
const { Pool } = require("pg");
require('dotenv').config();

const app = express();
app.use(cors());
app.use(express.json());

const PYTHON_BACKEND_URL = process.env.PYTHON_BACKEND_URL || "https://your-python-app.onrender.com";
const activeSessions = {};
const retryCount = {};
const initializingSessions = new Set(); // لمنع תضارب الطلبات (Race Conditions)

// 1. إعداد قاعدة البيانات مع مهلة زمنية صارمة (حل مشكلة التعليق الصامت)
const pool = new Pool({
    connectionString: process.env.DATABASE_URL,
    ssl: { rejectUnauthorized: false },
    max: 15, 
    connectionTimeoutMillis: 5000, 
    query_timeout: 10000, // الأهم: إحباط الاستعلام إذا تجاوز 10 ثوانٍ
    idleTimeoutMillis: 30000,
});

pool.on('error', (err) => console.error('[PG_POOL_ERR] 🚨', err.message));

// جلب نسخة واتساب مرة واحدة لتوفير الموارد وتقليل وقت التهيئة
let waVersion = [2, 3000, 1015901307]; // نسخة احتياطية
fetchLatestBaileysVersion().then(res => {
    waVersion = res.version;
    console.log(`[SYS] 🟢 تم جلب نسخة Baileys: ${waVersion.join('.')}`);
}).catch(() => console.log("[SYS] ⚠️ فشل جلب النسخة، سيتم استخدام النسخة الافتراضية."));

// 2. دالة استخراج النص المرنة (عالمية)
const getText = (message) => {
    if (!message) return null;
    return (
        message.conversation ||
        message.extendedTextMessage?.text ||
        message.buttonsResponseMessage?.selectedButtonId ||
        message.listResponseMessage?.title ||
        message.templateButtonReplyMessage?.selectedId ||
        null
    );
};

// 3. إدارة الجلسة في PostgreSQL
async function usePostgresAuthState(sessionId) {
    const writeData = async (data, id) => {
        try {
            const jsonStr = JSON.stringify(data, BufferJSON.replacer);
            await pool.query(
                "INSERT INTO whatsapp_sessions (id, data) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET data = $2",
                [`${sessionId}_${id}`, jsonStr]
            );
        } catch (err) { console.error(`[DB_WRITE_ERR] ❌`, err.message); }
    };

    const readData = async (id) => {
        try {
            const res = await pool.query("SELECT data FROM whatsapp_sessions WHERE id = $1", [`${sessionId}_${id}`]);
            return res.rows.length > 0 ? JSON.parse(JSON.stringify(res.rows[0].data), BufferJSON.reviver) : null;
        } catch (err) { 
            console.error(`[DB_READ_ERR] ⚠️`, err.message);
            return null; 
        }
    };

    const removeData = async (id) => {
        try { await pool.query("DELETE FROM whatsapp_sessions WHERE id = $1", [`${sessionId}_${id}`]); } catch (e) {}
    };

    const creds = await readData('creds') || initAuthCreds();

    return {
        state: {
            creds,
            keys: {
                get: async (type, ids) => {
                    const data = {};
                    await Promise.all(ids.map(async (id) => {
                        let value = await readData(`${type}-${id}`);
                        if (type === 'app-state-sync-key' && value) {
                            value = proto.Message.AppStateSyncKeyData.fromObject(value);
                        }
                        data[id] = value;
                    }));
                    return data;
                },
                set: async (data) => {
                    for (const type in data) {
                        for (const id in data[type]) {
                            const value = data[type][id];
                            value ? await writeData(value, `${type}-${id}`) : await removeData(`${type}-${id}`);
                        }
                    }
                }
            }
        },
        saveCreds: () => writeData(creds, 'creds')
    };
}

function cleanupSession(storeId) {
    if (activeSessions[storeId]) {
        console.log(`[CLEANUP] 🧹 إغلاق المتجر ${storeId}`);
        try { 
            activeSessions[storeId].ev.removeAllListeners(); 
            activeSessions[storeId].end(); 
        } catch (e) {}
        delete activeSessions[storeId];
    }
}

// 4. معالجة الرسائل الواردة وإرسالها للبايثون
async function handleIncomingMessages(m, storeId) {
    if (m.type !== "notify") return;

    for (const msg of m.messages) {
        const remoteJid = msg.key.remoteJid;
        
        // فلاتر صارمة
        if (!remoteJid || remoteJid === 'status@broadcast' || remoteJid.endsWith('@g.us') || msg.key.fromMe) continue;

        const text = getText(msg.message);
        if (!text) continue;

        // إرسال للبايثون (عبر Webhook) مع Timeout
        axios.post(`${PYTHON_BACKEND_URL}/webhook/node-incoming`, {
            storeId,
            customerPhone: remoteJid,
            message: text,
            pushName: msg.pushName || "User"
        }, { timeout: 8000 }).catch(err => {
            console.error(`[WEBHOOK_FAIL] ❌ فشل الإرسال للبايثون (المتجر: ${storeId}):`, err.message);
        });
    }
}

// 5. المحرك الرئيسي
function initWhatsApp(storeId, phoneNumber = null) {
    return new Promise(async (resolve, reject) => {
        if (activeSessions[storeId]) return resolve({ status: "ALREADY_CONNECTED" });
        if (initializingSessions.has(storeId)) return resolve({ status: "INITIALIZING" });

        initializingSessions.add(storeId);

        try {
            console.log(`[WA] 🛠️ تهيئة المتجر: ${storeId}`);
            const { state, saveCreds } = await usePostgresAuthState(storeId);

            const sock = makeWASocket({
                version: waVersion,
                auth: state,
                logger: pino({ level: "silent" }),
                browser: ["Ubuntu", "Chrome", "20.0.04"],
                printQRInTerminal: false,
                syncFullHistory: false,
                connectTimeoutMs: 60000,
                keepAliveIntervalMs: 15000, // نبض للحفاظ على الاتصال في Render
            });

            activeSessions[storeId] = sock;

            if (!sock.authState.creds.registered && phoneNumber) {
                console.log(`[PAIRING] 🔑 جاري طلب كود للمتجر ${storeId}`);
                setTimeout(async () => {
                    try {
                        const code = await sock.requestPairingCode(phoneNumber.replace(/\D/g, ''));
                        resolve({ status: "pairing_code", code });
                    } catch (e) {
                        cleanupSession(storeId);
                        reject(e);
                    } finally {
                        initializingSessions.delete(storeId);
                    }
                }, 4000);
            } else {
                initializingSessions.delete(storeId);
            }

            sock.ev.on("creds.update", saveCreds);

            sock.ev.on("connection.update", async (update) => {
                const { connection, lastDisconnect, qr } = update;
                
                if (qr && !phoneNumber) resolve({ status: "qr_code", qr });

                if (connection === "close") {
                    const statusCode = (lastDisconnect?.error instanceof Boom)?.output?.statusCode;
                    const isLogout = statusCode === DisconnectReason.loggedOut || statusCode === 401;
                    
                    cleanupSession(storeId);
                    
                    if (isLogout) {
                        console.log(`[LOGOUT] 🚪 تم تسجيل الخروج أو سحب الجلسة للمتجر ${storeId}`);
                        await pool.query("DELETE FROM whatsapp_sessions WHERE id LIKE $1", [`${storeId}_%`]);
                    } else {
                        // Exponential Backoff لإعادة الاتصال (تجنب الضغط على السيرفر)
                        retryCount[storeId] = (retryCount[storeId] || 0) + 1;
                        const delay = Math.min(retryCount[storeId] * 5000, 30000);
                        console.log(`[RECONNECT] 🔄 المتجر ${storeId} مقطوع (${statusCode}). محاولة رقم ${retryCount[storeId]} خلال ${delay/1000} ثوانٍ...`);
                        setTimeout(() => initWhatsApp(storeId), delay);
                    }
                } else if (connection === "open") {
                    console.log(`[CONNECTED] 🎉 متصل بنجاح: ${storeId}`);
                    retryCount[storeId] = 0; // تصفير العداد
                    resolve({ status: "connected" });
                }
            });

            sock.ev.on("messages.upsert", (m) => handleIncomingMessages(m, storeId));

        } catch (err) {
            cleanupSession(storeId);
            initializingSessions.delete(storeId);
            reject(err);
        }
    });
}

// 6. استعادة الجلسات التلقائية (Auto-Boot)
async function recoverSessions() {
    try {
        const res = await pool.query("SELECT DISTINCT id FROM whatsapp_sessions");
        const storeIds = [...new Set(res.rows.map(row => row.id.split('_')[0]))];
        
        console.log(`[BOOT] 🚀 بدء تشغيل السيرفر. جاري استعادة ${storeIds.length} متجر...`);
        
        for (const id of storeIds) {
            initWhatsApp(id).catch(() => {});
            await new Promise(r => setTimeout(r, 2500)); // تأخير لتجنب الحظر
        }
    } catch (err) { console.error("[BOOT_CRITICAL]", err.message); }
}

// 7. مسارات الـ API
app.post('/api/session/start', async (req, res) => {
    const { storeId, phoneNumber } = req.body;
    if (!storeId) return res.status(400).json({ error: "storeId is required" });
    try {
        res.json(await initWhatsApp(storeId, phoneNumber));
    } catch (error) { 
        res.status(500).json({ error: error.message }); 
    }
});

app.post('/api/message/send', async (req, res) => {
    const { storeId, customerPhone, text } = req.body;
    let sock = activeSessions[storeId];

    if (!sock || !sock.user) {
        return res.status(503).json({ error: "Session offline" });
    }

    try {
        let jid;
        const phoneStr = String(customerPhone).trim();

        // تحديد المعرف (JID)
        if (phoneStr.includes('@')) {
            jid = phoneStr;
        } else {
            const cleanPhone = phoneStr.replace(/\D/g, '');
            jid = (cleanPhone.startsWith('257') || cleanPhone.length >= 14) 
                ? `${cleanPhone}@lid` 
                : `${cleanPhone}@s.whatsapp.net`;
        }

        // --- إضافة ميزة "جاري الكتابة" ---
        // 1. إرسال حالة "جاري الكتابة"
        await sock.sendPresenceUpdate('composing', jid);
        
        // 2. الانتظار لمدة (مثلاً 2-3 ثوانٍ) لمحاكاة الكتابة البشرية
        const typingDuration = Math.min(Math.max(text.length * 50, 1500), 4000); // وقت متغير حسب طول النص
        await new Promise(resolve => setTimeout(resolve, typingDuration));

        // 3. إيقاف حالة "جاري الكتابة" (تلقائياً عند إرسال الرسالة)
        const sentMsg = await sock.sendMessage(jid, { text: text });
        
        console.log(`[SEND_SUCCESS] ✅ تم الإرسال للمتجر ${storeId} مع تأثير الكتابة`);
        res.json({ status: "success", messageId: sentMsg.key.id });

    } catch (error) {
        console.error(`[SEND_FAIL] ❌ خطأ في المتجر ${storeId}:`, error.message);
        res.status(500).json({ error: error.message });
    }
});

// تشغيل السيرفر
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`🌐 السيرفر يعمل على المنفذ ${PORT}`);
    setTimeout(recoverSessions, 3000); // بدء الاستعادة بعد 3 ثوانٍ
});
