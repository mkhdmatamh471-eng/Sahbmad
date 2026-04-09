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

// عنوان سيرفر البايثون (يجب إضافته في إعدادات Render)
const PYTHON_BACKEND_URL = process.env.PYTHON_BACKEND_URL || "https://your-python-app.onrender.com";

// تخزين الجلسات النشطة في الذاكرة
const activeSessions = {};
// تخزين عدد محاولات إعادة الاتصال لتجنب الحلقة المفرغة (Infinite Loop)
const retryCount = {};

/**
 * 1. إعداد الـ Pool (اتصال مستدام بقاعدة البيانات)
 */
const pool = new Pool({
    connectionString: process.env.DATABASE_URL,
    ssl: { rejectUnauthorized: false },
    max: 5,
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 5000,
});

let isDbConnected = false;

/**
 * 2. دالة إدارة الجلسة من PostgreSQL
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
            if (!isDbConnected) {
                console.log(`[DB] ✅ متصل بـ PostgreSQL.`);
                isDbConnected = true;
            }
            return res.rows.length > 0 ? JSON.parse(JSON.stringify(res.rows[0].data), BufferJSON.reviver) : null;
        } catch (err) {
            return null;
        }
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
 * 3. المحرك الرئيسي (تأسيس الاتصال)
 */
function initWhatsApp(storeId, phoneNumber = null) {
    return new Promise(async (resolve, reject) => {
        if (activeSessions[storeId]) {
            return resolve({ status: "ALREADY_CONNECTED" });
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
                defaultQueryTimeoutMs: undefined, 
                connectTimeoutMs: 60000,
                keepAliveIntervalMs: 10000,
            });

            activeSessions[storeId] = sock;

            // طلب كود الربط إذا تم تمرير رقم هاتف
            if (!sock.authState.creds.registered && phoneNumber) {
                console.log(`[PAIRING] 🔑 طلب كود للرقم: ${phoneNumber}`);
                setTimeout(async () => {
                    try {
                        const code = await sock.requestPairingCode(phoneNumber.replace(/\D/g, ''));
                        resolve({ status: "pairing_code", code: code });
                    } catch (err) {
                        reject(err);
                    }
                }, 4000); 
            }

            sock.ev.on("creds.update", saveCreds);

            sock.ev.on("connection.update", async (update) => {
                const { connection, lastDisconnect, qr } = update;

                if (qr && !phoneNumber) {
                    resolve({ status: "qr_code", qr: qr });
                }

                if (connection === "close") {
                    const statusCode = (lastDisconnect?.error instanceof Boom)?.output?.statusCode;
                    console.log(`[CLOSED] المتجر ${storeId} انقطع الاتصال. الكود: ${statusCode || 'Unknown'}`);

                    const isLoggedOut = statusCode === DisconnectReason.loggedOut || statusCode === 401 || statusCode === 403 || statusCode === 428;

                    // تتبع المحاولات الفاشلة لمنع الحلقة المفرغة
                    if (!retryCount[storeId]) retryCount[storeId] = 0;
                    retryCount[storeId]++;

                    if (isLoggedOut || retryCount[storeId] > 3) {
                        console.log(`[CLEANUP] 🗑️ الجلسة تالفة للمتجر ${storeId}. جاري الحذف لتجنب الحلقة المفرغة...`);
                        delete activeSessions[storeId];
                        delete retryCount[storeId];
                        await pool.query("DELETE FROM whatsapp_sessions WHERE id LIKE $1", [`${storeId}_%`]);
                        reject(new Error("SESSION_TERMINATED"));
                    } else {
                        console.log(`[RECONNECT] 🔄 محاولة ${retryCount[storeId]} لإعادة الاتصال للمتجر ${storeId} بعد 10 ثوانٍ...`);
                        setTimeout(() => initWhatsApp(storeId, phoneNumber).catch(() => {}), 10000);
                    }
                } else if (connection === "open") {
                    console.log(`[WA_READY] 🎉 متصل بنجاح: ${storeId}`);
                    retryCount[storeId] = 0; // تصفير العداد عند النجاح
                    resolve({ status: "connected" });
                }
            });

            // إرسال الرسائل الواردة إلى سيرفر البايثون (Webhook)
            sock.ev.on("messages.upsert", async (m) => {
                if (m.type !== "notify") return;
                for (const msg of m.messages) {
                    if (!msg.key.fromMe && msg.message) {
                        const remoteJid = msg.key.remoteJid;
                        let sender = remoteJid.split("@")[0].split(":")[0].replace(/\D/g, ''); // تنظيف فوري للرقم

                        const text = msg.message.conversation || msg.message.extendedTextMessage?.text;

                        if (text) {
                            console.log(`[INCOMING] ${storeId} <- ${sender}: ${text}`);
                            try {
                                await axios.post(`${PYTHON_BACKEND_URL}/webhook/node-incoming`, {
                                    storeId: storeId,
                                    customerPhone: sender, // استخدام الحقل الموحد مع بايثون
                                    phone: sender, // كاحتياط
                                    message: text
                                });
                            } catch (err) {
                                console.error(`[WEBHOOK_ERR] فشل إرسال الرسالة للبايثون: ${err.message}`);
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
 * 4. مسارات API (Endpoints)
 */

// بدء الجلسة أو طلب كود الربط/الباركود
app.post('/api/session/start', async (req, res) => {
    const { storeId, phoneNumber } = req.body;
    if (!storeId) return res.status(400).json({ error: "storeId is required" });

    try {
        const result = await initWhatsApp(storeId, phoneNumber);
        res.json(result);
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

// إرسال رسالة
app.post('/api/message/send', async (req, res) => {
    try {
        // استقبال customerPhone (وترك phone كاحتياط)
        const { storeId, customerPhone, phone, text } = req.body;
        const targetPhone = customerPhone || phone;
        
        console.log(`📩 طلب إرسال للمتجر ${storeId} -> ${targetPhone}`);

        if (!storeId || !targetPhone || !text) {
            return res.status(400).json({ error: "Missing parameters" });
        }

        // تنظيف الرقم تحسباً لأي زوائد
        const cleanPhone = String(targetPhone).split('@')[0].split(':')[0].replace(/\D/g, '');

        let sock = activeSessions[storeId];

        if (!sock) {
            console.log(`🔄 الجلسة خاملة، محاولة إعادة التنشيط للمتجر ${storeId}...`);
            await initWhatsApp(storeId);
            await new Promise(r => setTimeout(r, 3000));
            sock = activeSessions[storeId];
        }

        if (!sock) {
            return res.status(404).json({ error: "Store session not found" });
        }

        // الإرسال الفعلي
        await sock.sendMessage(`${cleanPhone}@s.whatsapp.net`, { text: text });
        console.log(`✅ [SUCCESS] تم إرسال الرسالة للمتجر ${storeId} إلى ${cleanPhone}`);
        res.json({ status: "success", sentTo: cleanPhone });

    } catch (error) {
        console.error(`❌ [ERROR] فشل الإرسال للواتساب: ${error.message}`);
        res.status(500).json({ error: error.message });
    }
});

// التحقق من صحة السيرفر
app.get('/health', (req, res) => {
    res.json({ 
        status: "online", 
        active_stores: Object.keys(activeSessions).length 
    });
});

app.get('/', (req, res) => {
    res.send("Jaddahh WhatsApp Bridge is Live!");
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`🚀 Node.js WhatsApp Bridge is running on port ${PORT}`);
});
