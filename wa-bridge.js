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
        } catch (err) { console.error(`[DB_ERR]: ${err.message}`); }
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
 * المحرك الرئيسي
 */
function initWhatsApp(storeId, phoneNumber = null) {
    return new Promise(async (resolve, reject) => {
        
        if (activeSessions[storeId]) {
            console.log(`[CLEANUP] 🔄 تنظيف الجلسة ${storeId}`);
            try {
                activeSessions[storeId].ev.removeAllListeners();
                if (activeSessions[storeId].ws) activeSessions[storeId].ws.close();
            } catch (e) {}
            delete activeSessions[storeId];
        }

        try {
            const { state, saveCreds } = await usePostgresAuthState(storeId);
            const { version } = await fetchLatestBaileysVersion();

            const sock = makeWASocket({
                version,
                auth: state,
                logger: pino({ level: "error" }), // رفع المستوى لمشاهدة أخطاء المكتبة فقط
                browser: ["Ubuntu", "Chrome", "20.0.04"],
                printQRInTerminal: false,
                syncFullHistory: false,
                connectTimeoutMs: 60000,
                keepAliveIntervalMs: 30000, // منع خمول السوكيت
                generateHighQualityLinkPreview: false,
                transactionOpts: { maxRetries: 2, delayBetweenRetriesMs: 3000 },
            });

            activeSessions[storeId] = sock;

            sock.ev.on("creds.update", saveCreds);

            sock.ev.on("connection.update", async (update) => {
                const { connection, lastDisconnect, qr } = update;
                if (qr && !phoneNumber) resolve({ status: "qr_code", qr });

                if (connection === "close") {
                    const statusCode = (lastDisconnect?.error instanceof Boom)?.output?.statusCode;
                    console.log(`[CONN_LOST] ❌ المتجر ${storeId} فصل. الرمز: ${statusCode}`);
                    
                    if (activeSessions[storeId]) {
                        activeSessions[storeId].ev.removeAllListeners();
                        delete activeSessions[storeId];
                    }

                    if (statusCode !== DisconnectReason.loggedOut) {
                        setTimeout(() => initWhatsApp(storeId, phoneNumber).catch(() => {}), 10000);
                    }
                } else if (connection === "open") {
                    console.log(`[CONN_OPEN] ✅ المتجر ${storeId} متصل الآن ويستمع...`);
                    resolve({ status: "connected" });
                }
            });

            // 🛠️ سجلات استلام الرسائل المحسنة
            sock.ev.on("messages.upsert", async (m) => {
                console.log(`[EVENT] تلقي حدث رسالة جديد من النوع: ${m.type}`);

                if (m.type !== "notify") return;

                // حل مشكلة الخطأ 428 الصامت
                if (!sock.ws || sock.ws.readyState !== 1) {
                    console.error(`[WS_ERR] ⚠️ السوكيت غير جاهز (State: ${sock.ws ? sock.ws.readyState : 'null'})`);
                    return;
                }

                for (const msg of m.messages) {
                    // سجل بسيط لكل رسالة تمر عبر النظام
                    console.log(`[MSG_RAW] رسالة من: ${msg.key.remoteJid} | FromMe: ${msg.key.fromMe}`);

                    if (!msg.key.fromMe && msg.message) {
                        const text = msg.message.conversation || 
                                     msg.message.extendedTextMessage?.text || 
                                     msg.message.buttonsResponseMessage?.selectedButtonId ||
                                     msg.message.listResponseMessage?.title;

                        if (text) {
                            console.log(`[INCOMING] 📨 ${storeId} <- ${msg.key.remoteJid}: ${text}`);
                            
                            axios.post(`${PYTHON_BACKEND_URL}/webhook/node-incoming`, {
                                storeId: storeId,
                                customerPhone: msg.key.remoteJid,
                                message: text
                            }, { timeout: 10000 })
                            .catch(err => console.error(`[WEBHOOK_ERR] ❌: ${err.message}`));
                        } else {
                            console.log(`[MSG_SKIP] الرسالة لا تحتوي على نص مدعوم.`);
                        }
                    }
                }
            });

        } catch (err) {
            reject(err);
        }
    });
}

/**
 * مسارات الـ API
 */
app.get('/status', (req, res) => res.send("Bridge is Alive 🚀"));

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

        if (!sock || !sock.ws || sock.ws.readyState !== 1) {
            console.log(`[AUTO_RECONNECT] 🔄 استعادة الجلسة للمتجر ${storeId}`);
            await initWhatsApp(storeId);
            sock = activeSessions[storeId];
        }

        const jid = customerPhone.includes('@') ? customerPhone : `${customerPhone.replace(/\D/g, '')}@s.whatsapp.net`;
        const sent = await sock.sendMessage(jid, { text });
        res.json({ status: "success", id: sent.key.id });
    } catch (error) { res.status(500).json({ error: error.message }); }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`🚀 Server running on port ${PORT}`));
