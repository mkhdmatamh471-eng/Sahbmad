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

const pool = new Pool({
    connectionString: process.env.DATABASE_URL,
    ssl: { rejectUnauthorized: false },
    max: 10,
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 5000,
});

/**
 * دالة إدارة الجلسة من PostgreSQL - محسنة لضمان ثبات التشفير
 */
async function usePostgresAuthState(sessionId) {
    const writeData = async (data, id) => {
        try {
            const jsonStr = JSON.stringify(data, BufferJSON.replacer);
            await pool.query(
                "INSERT INTO whatsapp_sessions (id, data) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET data = $2",
                [`${sessionId}_${id}`, jsonStr]
            );
        } catch (err) { /* تسجيل صامت لتجنب إغراق السجلات */ }
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
 * المحرك الرئيسي مع معالجة الأخطاء الشاملة
 */
function initWhatsApp(storeId, phoneNumber = null) {
    return new Promise(async (resolve, reject) => {
        
        // تنظيف عميق للجلسة السابقة (حل مشاكل الذاكرة)
        if (activeSessions[storeId]) {
            console.log(`[CLEANUP] 🔄 تنظيف جلسة المتجر ${storeId}`);
            try {
                activeSessions[storeId].ev.removeAllListeners();
                if (activeSessions[storeId].ws) activeSessions[storeId].ws.close();
            } catch (e) {}
            delete activeSessions[storeId];
            if (!phoneNumber) return resolve({ status: "ALREADY_CONNECTED" });
        }

        try {
            const { state, saveCreds } = await usePostgresAuthState(storeId);
            const { version } = await fetchLatestBaileysVersion();

            const sock = makeWASocket({
                version,
                auth: state,
                logger: pino({ level: "error" }),
                browser: ["Ubuntu", "Chrome", "20.0.04"],
                printQRInTerminal: false,
                syncFullHistory: false, // أساسي لتقليل استهلاك الرام
                connectTimeoutMs: 60000,
                keepAliveIntervalMs: 30000, // نبضات قلب لمنع خمول Render
                generateHighQualityLinkPreview: false,
                transactionOpts: { maxRetries: 2, delayBetweenRetriesMs: 3000 },
            });

            activeSessions[storeId] = sock;

            // كود الربط (Pairing)
            if (!sock.authState.creds.registered && phoneNumber) {
                setTimeout(async () => {
                    try {
                        const code = await sock.requestPairingCode(phoneNumber.replace(/\D/g, ''));
                        resolve({ status: "pairing_code", code });
                    } catch (err) {
                        delete activeSessions[storeId];
                        reject(new Error("FAILED_TO_GENERATE_PAIRING_CODE"));
                    }
                }, 5000);
            }

            sock.ev.on("creds.update", saveCreds);

            sock.ev.on("connection.update", async (update) => {
                const { connection, lastDisconnect, qr } = update;
                if (qr && !phoneNumber) resolve({ status: "qr_code", qr });

                if (connection === "close") {
                    const statusCode = (lastDisconnect?.error instanceof Boom)?.output?.statusCode;
                    const isLoggedOut = statusCode === DisconnectReason.loggedOut || statusCode === 401;

                    console.log(`[CONN_LOST] ⚠️ فصل المتجر ${storeId}. الرمز: ${statusCode}`);

                    if (activeSessions[storeId]) {
                        activeSessions[storeId].ev.removeAllListeners();
                        delete activeSessions[storeId];
                    }

                    if (isLoggedOut) {
                        await pool.query("DELETE FROM whatsapp_sessions WHERE id LIKE $1", [`${storeId}_%`]);
                        reject(new Error("SESSION_TERMINATED"));
                    } else {
                        setTimeout(() => initWhatsApp(storeId, phoneNumber).catch(() => {}), 10000);
                    }
                } else if (connection === "open") {
                    console.log(`[CONN_OPEN] ✅ المتجر ${storeId} متصل ومستعد.`);
                    
                    // تحديث الـ Phone Mapping تلقائياً
                    if (phoneNumber && sock.user) {
                        const myLid = sock.user.id.split(':')[0].split('@')[0];
                        await pool.query(
                            "INSERT INTO phone_mappings (store_id, lid, real_phone) VALUES ($1, $2, $3) ON CONFLICT (lid) DO UPDATE SET real_phone = $3",
                            [storeId, myLid, phoneNumber.replace(/\D/g, '')]
                        ).catch(e => console.error("[MAP_ERR]", e.message));
                    }
                    
                    retryCount[storeId] = 0;
                    resolve({ status: "connected" });
                }
            });

            // استقبال الرسائل ومعالجة الـ JID
            sock.ev.on("messages.upsert", async (m) => {
                if (m.type !== "notify") return;
                
                // حل مشكلة 428 الصامت (التحقق من حالة المقبس)
                if (!sock.ws || sock.ws.readyState !== 1) return;

                for (const msg of m.messages) {
                    if (!msg.key.fromMe && msg.message) {
                        const remoteJid = msg.key.remoteJid;
                        let senderJid = remoteJid.includes(':') ? `${remoteJid.split(':')[0]}@${remoteJid.split('@')[1]}` : remoteJid;

                        const text = msg.message.conversation || 
                                     msg.message.extendedTextMessage?.text || 
                                     msg.message.buttonsResponseMessage?.selectedButtonId;

                        if (text) {
                            console.log(`[INCOMING] ${storeId} <- ${senderJid}: ${text}`);
                            axios.post(`${PYTHON_BACKEND_URL}/webhook/node-incoming`, {
                                storeId, customerPhone: senderJid, message: text
                            }, { timeout: 8000 })
                            .catch(err => console.error(`[WEBHOOK_ERR] ❌ ${err.message}`));
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
app.get('/health', (req, res) => res.send("System Active 🚀"));

app.post('/api/session/start', async (req, res) => {
    const { storeId, phoneNumber } = req.body;
    try {
        const result = await initWhatsApp(storeId, phoneNumber);
        res.json(result);
    } catch (error) { res.status(500).json({ error: error.message }); }
});

app.post('/api/message/send', async (req, res) => {
    try {
        const { storeId, customerPhone, text } = req.body;
        let sock = activeSessions[storeId];

        // التحقق من صحة الاتصال قبل الإرسال لمنع تعليق الطلب
        if (!sock || !sock.ws || sock.ws.readyState !== 1) {
            console.log(`[AUTO_REC] 🔄 استعادة الجلسة للمتجر ${storeId}`);
            await initWhatsApp(storeId);
            sock = activeSessions[storeId];
            if (!sock) throw new Error("Session unavailable");
        }

        const jid = customerPhone.includes('@') ? customerPhone : `${customerPhone.replace(/\D/g, '')}@s.whatsapp.net`;
        
        const sent = await Promise.race([
            sock.sendMessage(jid, { text }),
            new Promise((_, r) => setTimeout(() => r(new Error("TIMEOUT")), 15000))
        ]);

        res.json({ status: "success", messageId: sent.key.id });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`🚀 Node.js Bridge on port ${PORT}`));
