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
const connectionAttempts = {}; // تتبع محاولات الاتصال لمنع التكرار اللانهائي

const pool = new Pool({
    connectionString: process.env.DATABASE_URL,
    ssl: { rejectUnauthorized: false },
    max: 10, 
    idleTimeoutMillis: 30000,
});

/**
 * دالة إدارة الجلسة - الحفظ الذري
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
 * المحرك الرئيسي - محسن للأجهزة الضعيفة (Render Free)
 */
async function initWhatsApp(storeId, phoneNumber = null) {
    // 1. تنظيف صارم للجلسة الموجودة
    if (activeSessions[storeId]) {
        try {
            const oldSock = activeSessions[storeId];
            oldSock.ev.removeAllListeners();
            if (oldSock.ws) oldSock.ws.terminate();
        } catch (e) {}
        delete activeSessions[storeId];
    }

    // 2. منع الانفجار (Circuit Breaker)
    if (connectionAttempts[storeId] > 5) {
        console.log(`[BLOCK] 🛑 توقف مؤقت للمتجر ${storeId} بسبب كثرة الانهيارات.`);
        setTimeout(() => { connectionAttempts[storeId] = 0; }, 600000); // تصفير المحاولات بعد 10 دقائق
        return;
    }

    try {
        const { state, saveCreds } = await usePostgresAuthState(storeId);
        
        const sock = makeWASocket({
            version: [2, 3000, 1015901307],
            auth: state,
            logger: pino({ level: "silent" }), // صمت تام لتوفير الذاكرة
            browser: ["Ubuntu", "Chrome", "20.0.04"],
            printQRInTerminal: false,
            syncFullHistory: false,
            shouldSyncHistoryMessage: () => false,
            connectTimeoutMs: 20000, 
            keepAliveIntervalMs: 30000, 
            generateHighQualityLinkPreview: false, // تعطيل المعاينة لتوفير الرام
        });

        activeSessions[storeId] = sock;

        // طلب كود الربط
        if (!sock.authState.creds.registered && phoneNumber) {
            setTimeout(async () => {
                try {
                    const code = await sock.requestPairingCode(phoneNumber.replace(/\D/g, ''));
                    console.log(`[PAIRING] 🔑 الكود لـ ${storeId}: ${code}`);
                } catch (err) {}
            }, 6000);
        }

        sock.ev.on("creds.update", saveCreds);

        sock.ev.on("connection.update", async (update) => {
            const { connection, lastDisconnect } = update;

            if (connection === "close") {
                const statusCode = (lastDisconnect?.error instanceof Boom)?.output?.statusCode;
                
                connectionAttempts[storeId] = (connectionAttempts[storeId] || 0) + 1;
                console.log(`[STATUS] 🚪 متجر ${storeId} فصل. محاولة رقم: ${connectionAttempts[storeId]}`);

                if (activeSessions[storeId]) {
                    activeSessions[storeId].ev.removeAllListeners();
                    delete activeSessions[storeId];
                }

                if (statusCode !== DisconnectReason.loggedOut && connectionAttempts[storeId] <= 5) {
                    // انتظار تصاعدي قبل إعادة المحاولة لمنع الضغط
                    const delay = 5000 * connectionAttempts[storeId];
                    setTimeout(() => initWhatsApp(storeId, phoneNumber), delay);
                }
            } 
            else if (connection === "open") {
                console.log(`[READY] ✅ متصل: ${storeId}`);
                connectionAttempts[storeId] = 0; // تصفير العداد عند النجاح
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
                        }, { timeout: 5000 }).catch(() => {});
                    }
                }
            }
        });

    } catch (err) {
        delete activeSessions[storeId];
    }
}

/**
 * المسارات
 */
app.post('/api/session/start', async (req, res) => {
    const { storeId, phoneNumber } = req.body;
    initWhatsApp(storeId, phoneNumber);
    res.json({ status: "processing" });
});

app.post('/api/message/send', async (req, res) => {
    const { storeId, customerPhone, text } = req.body;
    try {
        const sock = activeSessions[storeId];
        if (!sock) return res.status(404).send("Session not active");
        await sock.sendMessage(customerPhone, { text });
        res.json({ status: "sent" });
    } catch (e) { res.status(500).send(e.message); }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`🚀 Final Bridge on ${PORT}`));
