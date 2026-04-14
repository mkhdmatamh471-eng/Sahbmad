Const express = require('express');
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

const pool = new Pool({
    connectionString: process.env.DATABASE_URL,
    ssl: { rejectUnauthorized: false },
    max: 5,
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 5000,
});

/**
 * دالة إدارة الجلسة من PostgreSQL
 */
async function usePostgresAuthState(sessionId) {
    const writeData = async (data, id) => {
        try {
            const jsonStr = JSON.stringify(data, BufferJSON.replacer);
            await pool.query(
                "INSERT INTO whatsapp_sessions (id, data) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET data = $2",
                [`${sessionId}_${id}`, jsonStr]
            );
        } catch (err) {
            console.error(`[DB_WRITE_ERR] ❌: ${err.message}`);
        }
    };

    const readData = async (id) => {
        try {
            const res = await pool.query("SELECT data FROM whatsapp_sessions WHERE id = $1", [`${sessionId}_${id}`]);
            return res.rows.length > 0 ? JSON.parse(JSON.stringify(res.rows[0].data), BufferJSON.reviver) : null;
        } catch (err) { return null; }
    };

    const removeData = async (id) => {
        await pool.query("DELETE FROM whatsapp_sessions WHERE id = $1", [`${sessionId}_${id}`]);
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

/**
 * المحرك الرئيسي (تأسيس الاتصال)
 */
function initWhatsApp(storeId, phoneNumber = null) {
    return new Promise(async (resolve, reject) => {
        if (activeSessions[storeId]) {
            if (phoneNumber) {
                console.log(`[CLEANUP] 🔄 إنهاء الجلسة القديمة للمتجر ${storeId} لطلب كود جديد...`);
                try { activeSessions[storeId].end(); } catch (e) {}
                delete activeSessions[storeId];
            } else {
                return resolve({ status: "ALREADY_CONNECTED" });
            }
        }

        try {
            console.log(`[WA] 🛠️ تهيئة المقبس للمتجر: ${storeId}`);
            const { state, saveCreds } = await usePostgresAuthState(storeId);
            const { version } = await fetchLatestBaileysVersion();

            const sock = makeWASocket({
                version,
                auth: state,
                logger: pino({ level: "silent" }),
                browser: ["Ubuntu", "Chrome", "20.0.04"],
                printQRInTerminal: false,
                syncFullHistory: false,
                connectTimeoutMs: 60000,
                // تسريع عملية إعادة المحاولة الداخلية في المكتبة
                transactionOpts: { maxRetries: 5, delayBetweenRetriesMs: 1000 },
            });

            activeSessions[storeId] = sock;

            // طلب كود الربط
            if (!sock.authState.creds.registered && phoneNumber) {
                console.log(`[PAIRING] 🔑 جاري طلب كود ربط للرقم: ${phoneNumber}`);
                setTimeout(async () => {
                    try {
                        const cleanNumber = phoneNumber.replace(/\D/g, '');
                        const code = await sock.requestPairingCode(cleanNumber);
                        resolve({ status: "pairing_code", code: code });
                    } catch (err) {
                        delete activeSessions[storeId];
                        reject(new Error("FAILED_TO_GENERATE_PAIRING_CODE"));
                    }
                }, 5000);
            }

            sock.ev.on("creds.update", saveCreds);

            sock.ev.on("connection.update", async (update) => {
                const { connection, lastDisconnect, qr } = update;

                if (qr && !phoneNumber) {
                    resolve({ status: "qr_code", qr: qr });
                }

                if (connection === "close") {
                    const statusCode = (lastDisconnect?.error instanceof Boom)?.output?.statusCode;
                    const isLoggedOut = statusCode === DisconnectReason.loggedOut || statusCode === 401;

                    if (!retryCount[storeId]) retryCount[storeId] = 0;
                    retryCount[storeId]++;

                    if (isLoggedOut || retryCount[storeId] > 3) {
                        delete activeSessions[storeId];
                        await pool.query("DELETE FROM whatsapp_sessions WHERE id LIKE $1", [`${storeId}_%`]);
                        reject(new Error("SESSION_TERMINATED"));
                    } else {
                        setTimeout(() => initWhatsApp(storeId, phoneNumber).catch(() => {}), 10000);
                    }
                } 

                else if (connection === "open") {
                    console.log(`[WA_READY] 🎉 متصل بنجاح: ${storeId}`);

                    if (phoneNumber) {
                        try {
                            const myLid = sock.user.id.split(':')[0].split('@')[0];
                            const myRealPhone = phoneNumber.replace(/\D/g, '');

                            console.log(`[MAPPING] 🔗 حفظ الربط: ${myRealPhone} <-> ${myLid}`);
                            await pool.query(
                                "INSERT INTO phone_mappings (store_id, lid, real_phone) VALUES ($1, $2, $3) ON CONFLICT (lid) DO UPDATE SET real_phone = $3",
                                [storeId, myLid, myRealPhone]
                            );
                        } catch (err) {
                            console.error(`[MAPPING_ERR] ❌ فشل حفظ الربط: ${err.message}`);
                        }
                    }

                    retryCount[storeId] = 0;
                    resolve({ status: "connected" });
                }
            });

            // 🌟 التعديل الأول: استقبال الرسائل وإرسال الـ JID كاملاً
            sock.ev.on("messages.upsert", async (m) => {
                if (m.type !== "notify") return;
                for (const msg of m.messages) {
                    if (!msg.key.fromMe && msg.message) {
                        const remoteJid = msg.key.remoteJid;

                        // تنظيف الـ JID من كود الجهاز المعقد (مثل 967...@s.whatsapp.net:44)
                        // مع الاحتفاظ بالنطاق (@lid أو @s.whatsapp.net)
                        let senderJid = remoteJid;
                        if (remoteJid.includes('@')) {
                            const [numberPart, domainPart] = remoteJid.split('@');
                            const cleanNumber = numberPart.split(':')[0];
                            senderJid = `${cleanNumber}@${domainPart}`;
                        }

                        const text = msg.message.conversation || msg.message.extendedTextMessage?.text;

                        if (text) {
                            console.log(`[INCOMING] ${storeId} <- ${senderJid}: ${text}`);
                            try {
                                await axios.post(`${PYTHON_BACKEND_URL}/webhook/node-incoming`, {
                                    storeId: storeId,
                                    customerPhone: senderJid, // الآن بايثون سيتلقى JID كامل وصحيح
                                    message: text
                                });
                            } catch (err) {
                                console.error(`[WEBHOOK_ERR] ❌ فشل الإرسال للبايثون`);
                            }
                        }
                    }
                }
            });

        } catch (err) {
            delete activeSessions[storeId];
            reject(err);
        }
    });
}

/**
 * مسارات الـ API
 */
app.post('/api/session/start', async (req, res) => {
    const { storeId, phoneNumber } = req.body;
    if (!storeId) return res.status(400).json({ error: "storeId is required" });
    try {
        const result = await initWhatsApp(storeId, phoneNumber);
        res.json(result);
    } catch (error) { res.status(500).json({ error: error.message }); }
});

// 🌟 التعديل الثاني والثالث: مسار الإرسال الذكي والمحمي من التعليق
app.post('/api/message/send', async (req, res) => {
    try {
        const { storeId, customerPhone, text } = req.body;

        // تحديد الـ JID الصحيح بذكاء
        let jid = String(customerPhone);

        if (!jid.includes('@')) {
            // إذا لم يحتوي على '@' نقوم بتنظيفه وتوقع النطاق
            const cleanPhone = jid.replace(/\D/g, '');
            if (cleanPhone.length >= 15 || cleanPhone.startsWith('257')) {
                jid = `${cleanPhone}@lid`;
            } else {
                jid = `${cleanPhone}@s.whatsapp.net`;
            }
        } else {
            // إذا كان يحتوي على '@' (قادم من البايثون)، نتأكد فقط من إزالة كود الجهاز
            const [numPart, domainPart] = jid.split('@');
            jid = `${numPart.split(':')[0]}@${domainPart}`;
        }

        let sock = activeSessions[storeId];

        if (!sock || !sock.user) {
            console.log(`[RECONNECT] 🔄 الجلسة غير نشطة للمتجر ${storeId}، جاري الاستعادة...`);
            await initWhatsApp(storeId);
            await new Promise(r => setTimeout(r, 2000));
            sock = activeSessions[storeId];
            if (!sock) return res.status(404).json({ error: "Session not found or not connected" });
        }

        console.log(`[SENDING] 🚀 محاولة الإرسال الفعلي إلى: ${jid}`);

        // استخدام Promise.race لمنع التعليق
        const sendMessagePromise = sock.sendMessage(jid, { text: text });
        const timeoutPromise = new Promise((_, reject) => 
            setTimeout(() => reject(new Error("TIMEOUT")), 15000) // مهلة 15 ثانية
        );

        const sentMsg = await Promise.race([sendMessagePromise, timeoutPromise]);

        if (sentMsg) {
            console.log(`[SEND_SUCCESS] ✅ تم استلام التأكيد من السيرفر. ID: ${sentMsg.key.id}`);
            res.json({ status: "success", messageId: sentMsg.key.id, sentTo: jid });
        }

    } catch (error) {
        if (error.message === "TIMEOUT") {
            console.error(`[SEND_TIMEOUT] ⏳ تعلقت عملية الإرسال! السيرفر لم يرد.`);
            res.status(504).json({ error: "تعذر الوصول لخادم واتساب (Timeout)" });
        } else {
            console.error(`[SEND_ERROR] ❌ فشل فعلي: ${error.message}`);
            res.status(500).json({ error: error.message });
        }
    }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`🚀 Node.js Bridge on port ${PORT}`));