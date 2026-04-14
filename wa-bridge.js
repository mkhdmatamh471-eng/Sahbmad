const express = require('express');
const cors = require('cors');
const axios = require('axios');
const { 
    default: makeWASocket, 
    DisconnectReason, 
    initAuthCreds, 
    BufferJSON, 
    proto 
} = require("@whiskeysockets/baileys");
const { Boom } = require("@hapi/boom");
const pino = require("pino");
const { Pool } = require("pg");
require('dotenv').config();

const app = express();
app.use(cors());
app.use(express.json());

const PYTHON_BACKEND_URL = process.env.PYTHON_BACKEND_URL || "";
const activeSessions = {};
const isConnecting = {}; // قفل لمنع محاولات الاتصال المتزامنة لنفس المتجر

const pool = new Pool({
    connectionString: process.env.DATABASE_URL,
    ssl: { rejectUnauthorized: false },
    max: 5, // تقليل عدد الاتصالات لتوفير الذاكرة على Render
});

/**
 * إدارة الجلسة - حفظ مفاتيح التشفير
 */
async function usePostgresAuthState(sessionId) {
    const writeData = async (data, id) => {
        try {
            const jsonStr = JSON.stringify(data, BufferJSON.replacer);
            await pool.query(
                "INSERT INTO whatsapp_sessions (id, data) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET data = $2",
                [`${sessionId}_${id}`, jsonStr]
            );
        } catch (err) {}
    };

    const readData = async (id) => {
        try {
            const res = await pool.query("SELECT data FROM whatsapp_sessions WHERE id = $1", [`${sessionId}_${id}`]);
            return res.rows.length > 0 ? JSON.parse(JSON.stringify(res.rows[0].data), BufferJSON.reviver) : null;
        } catch (err) { return null; }
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
                    const tasks = [];
                    for (const type in data) {
                        for (const id in data[type]) {
                            const value = data[type][id];
                            const keyPath = `${type}-${id}`;
                            value ? tasks.push(writeData(value, keyPath)) : tasks.push(removeData(keyPath));
                        }
                    }
                    await Promise.all(tasks);
                }
            }
        },
        saveCreds: () => writeData(creds, 'creds')
    };
}

/**
 * المحرك الرئيسي - مع نظام "القفل" لمنع التكرار
 */
async function initWhatsApp(storeId, phoneNumber = null) {
    // 1. إذا كان المتجر في حالة اتصال حالياً، لا تفعل شيئاً
    if (isConnecting[storeId]) return;
    isConnecting[storeId] = true;

    // 2. تنظيف شامل قبل البدء
    if (activeSessions[storeId]) {
        try {
            activeSessions[storeId].ev.removeAllListeners();
            if (activeSessions[storeId].ws) activeSessions[storeId].ws.terminate();
        } catch (e) {}
        delete activeSessions[storeId];
    }

    try {
        console.log(`[START] 🚀 بدء جلسة المتجر: ${storeId}`);
        const { state, saveCreds } = await usePostgresAuthState(storeId);
        
        const sock = makeWASocket({
            version: [2, 3000, 1015901307],
            auth: state,
            logger: pino({ level: "silent" }),
            browser: ["Ubuntu", "Chrome", "20.0.04"],
            syncFullHistory: false,
            shouldSyncHistoryMessage: () => false,
            connectTimeoutMs: 15000, // مهلة قصيرة لقتل المحاولات الفاشلة بسرعة
            keepAliveIntervalMs: 60000,
        });

        activeSessions[storeId] = sock;

        // طلب كود الربط (Pairing)
        if (!sock.authState.creds.registered && phoneNumber) {
            setTimeout(async () => {
                try {
                    const code = await sock.requestPairingCode(phoneNumber.replace(/\D/g, ''));
                    console.log(`[CODE] 🔑 المتجر ${storeId}: ${code}`);
                } catch (err) {}
            }, 5000);
        }

        sock.ev.on("creds.update", saveCreds);

        sock.ev.on("connection.update", async (update) => {
            const { connection, lastDisconnect } = update;

            if (connection === "open") {
                console.log(`[READY] ✅ المتجر ${storeId} متصل الآن.`);
                isConnecting[storeId] = false; // فتح القفل عند النجاح
            }

            if (connection === "close") {
                isConnecting[storeId] = false; // فتح القفل للسماح بإعادة المحاولة المنظمة
                const statusCode = (lastDisconnect?.error instanceof Boom)?.output?.statusCode;
                
                console.log(`[CLOSE] 🚪 المتجر ${storeId} انفصل (رمز: ${statusCode})`);

                if (statusCode !== DisconnectReason.loggedOut) {
                    // انتظار 15 ثانية كاملة قبل السماح بإعادة المحاولة (كسر الحلقة)
                    console.log(`[RETRY] ⏳ انتظار 15 ثانية قبل إعادة المحاولة لـ ${storeId}...`);
                    setTimeout(() => initWhatsApp(storeId, phoneNumber), 15000);
                } else {
                    await pool.query("DELETE FROM whatsapp_sessions WHERE id LIKE $1", [`${storeId}_%`]);
                }
            }
        });

        sock.ev.on("messages.upsert", async (m) => {
            if (m.type !== "notify") return;
            for (const msg of m.messages) {
                if (!msg.key.fromMe && msg.message) {
                    const jid = msg.key.remoteJid;
                    const text = msg.message.conversation || msg.message.extendedTextMessage?.text;
                    if (text && PYTHON_BACKEND_URL) {
                        axios.post(`${PYTHON_BACKEND_URL}/webhook/node-incoming`, {
                            storeId, customerPhone: jid, message: text
                        }, { timeout: 4000 }).catch(() => {});
                    }
                }
            }
        });

    } catch (err) {
        isConnecting[storeId] = false;
        console.error(`[FATAL] ❌ خطأ في متجر ${storeId}:`, err.message);
    }
}

/**
 * المسارات
 */
app.post('/api/session/start', async (req, res) => {
    const { storeId, phoneNumber } = req.body;
    initWhatsApp(storeId, phoneNumber); // تشغيل في الخلفية
    res.json({ status: "initiated", message: "جاري بدء الجلسة، راقب السجلات" });
});

app.post('/api/message/send', async (req, res) => {
    const { storeId, customerPhone, text } = req.body;
    const sock = activeSessions[storeId];
    if (!sock) return res.status(404).json({ error: "Session offline" });
    try {
        await sock.sendMessage(customerPhone, { text });
        res.json({ status: "sent" });
    } catch (e) { res.status(500).json({ error: e.message }); }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`🚀 Final Stable Bridge on ${PORT}`));
