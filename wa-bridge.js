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

const PYTHON_BACKEND_URL = process.env.PYTHON_BACKEND_URL || "";
const activeSessions = {};
const isConnecting = {}; // قفل لمنع تكرار محاولات الاتصال لنفس المتجر

// إعداد قاعدة البيانات مع تحسين الأداء لتناسب خطة Render المجانية
const pool = new Pool({
    connectionString: process.env.DATABASE_URL,
    ssl: { rejectUnauthorized: false },
    max: 5, 
    idleTimeoutMillis: 30000,
});

/**
 * دالة إدارة الجلسة - تضمن الحفظ المتزامن لجميع مفاتيح التشفير (حل PreKey Error)
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
                    await Promise.all(tasks); // ضمان حفظ الكل قبل المتابعة
                }
            }
        },
        saveCreds: () => writeData(creds, 'creds')
    };
}

/**
 * المحرك الرئيسي - نظام الاتصال الذكي ومنع التعليق
 */
async function initWhatsApp(storeId, phoneNumber = null) {
    if (isConnecting[storeId]) return;
    isConnecting[storeId] = true;

    // تنظيف أي جلسة قديمة عالقة في الذاكرة
    if (activeSessions[storeId]) {
        try {
            activeSessions[storeId].ev.removeAllListeners();
            if (activeSessions[storeId].ws) activeSessions[storeId].ws.terminate();
        } catch (e) {}
        delete activeSessions[storeId];
    }

    try {
        console.log(`[START] 🚀 بدء الجلسة: ${storeId}`);
        const { state, saveCreds } = await usePostgresAuthState(storeId);
        
        const sock = makeWASocket({
            version: [2, 3000, 1015901307], // نسخة مستقرة لبروتوكول واتساب
            auth: state,
            logger: pino({ level: "silent" }),
            browser: ["Ubuntu", "Chrome", "20.0.04"],
            syncFullHistory: false,
            shouldSyncHistoryMessage: () => false, // منع أخطاء bad-request
            connectTimeoutMs: 20000,
            keepAliveIntervalMs: 60000,
        });

        activeSessions[storeId] = sock;

        // طلب كود الربط (Pairing)
        if (!sock.authState.creds.registered && phoneNumber) {
            setTimeout(async () => {
                try {
                    const cleanNumber = phoneNumber.replace(/\D/g, '');
                    const code = await sock.requestPairingCode(cleanNumber);
                    console.log(`[CODE] 🔑 الكود لـ ${storeId}: ${code}`);
                } catch (err) {}
            }, 5000);
        }

        sock.ev.on("creds.update", saveCreds);

        sock.ev.on("connection.update", async (update) => {
            const { connection, lastDisconnect } = update;

            if (connection === "open") {
                console.log(`[READY] ✅ المتجر ${storeId} متصل.`);
                isConnecting[storeId] = false;
            }

            if (connection === "close") {
                isConnecting[storeId] = false;
                const statusCode = (lastDisconnect?.error instanceof Boom)?.output?.statusCode;
                
                if (statusCode !== DisconnectReason.loggedOut) {
                    // انتظار 15 ثانية قبل إعادة المحاولة لمنع استهلاك الرام
                    setTimeout(() => initWhatsApp(storeId, phoneNumber), 15000);
                } else {
                    console.log(`[LOGOUT] 🚪 المتجر ${storeId} سجل خروجه.`);
                    await pool.query("DELETE FROM whatsapp_sessions WHERE id LIKE $1", [`${storeId}_%`]);
                }
            }
        });

        sock.ev.on("messages.upsert", async (m) => {
            if (m.type !== "notify") return;
            for (const msg of m.messages) {
                if (!msg.key.fromMe && msg.message) {
                    const remoteJid = msg.key.remoteJid;
                    let senderJid = remoteJid;

                    // تنظيف الـ JID من كود الجهاز المعقد
                    if (remoteJid.includes('@')) {
                        const [numberPart, domainPart] = remoteJid.split('@');
                        senderJid = `${numberPart.split(':')[0]}@${domainPart}`;
                    }

                    const text = msg.message.conversation || msg.message.extendedTextMessage?.text;
                    if (text && PYTHON_BACKEND_URL) {
                        axios.post(`${PYTHON_BACKEND_URL}/webhook/node-incoming`, {
                            storeId, customerPhone: senderJid, message: text
                        }, { timeout: 5000 }).catch(() => {});
                    }
                }
            }
        });

    } catch (err) {
        isConnecting[storeId] = false;
        console.error(`[FATAL] ❌ خطأ متجر ${storeId}:`, err.message);
    }
}

/**
 * المسارات (API Endpoints)
 */
app.post('/api/session/start', async (req, res) => {
    const { storeId, phoneNumber } = req.body;
    if (!storeId || !phoneNumber) return res.status(400).json({ status: "error", message: "بيانات ناقصة" });
    
    initWhatsApp(storeId, phoneNumber);
    res.status(200).json({ status: "success", message: "جاري المعالجة..." });
});

app.post('/api/message/send', async (req, res) => {
    const { storeId, customerPhone, text } = req.body;
    let jid = String(customerPhone);
    
    // تصحيح الـ JID بذكاء
    if (!jid.includes('@')) {
        const clean = jid.replace(/\D/g, '');
        jid = (clean.length >= 15 || clean.startsWith('257')) ? `${clean}@lid` : `${clean}@s.whatsapp.net`;
    } else {
        const [num, dom] = jid.split('@');
        jid = `${num.split(':')[0]}@${dom}`;
    }

    try {
        let sock = activeSessions[storeId];
        if (!sock) {
            await initWhatsApp(storeId);
            return res.status(404).json({ status: "error", message: "الجلسة قيد الاستعادة، حاول بعد قليل" });
        }

        // استخدام Promise.race لمنع تعليق الطلب (Timeout)
        const sentMsg = await Promise.race([
            sock.sendMessage(jid, { text }),
            new Promise((_, reject) => setTimeout(() => reject(new Error("TIMEOUT")), 15000))
        ]);

        res.json({ status: "success", messageId: sentMsg.key.id });
    } catch (e) {
        res.status(500).json({ status: "error", message: e.message });
    }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`🚀 Bridge Stable v7.0 on ${PORT}`));
