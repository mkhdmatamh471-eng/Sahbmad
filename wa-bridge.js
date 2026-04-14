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

// إعداد قاعدة البيانات مع تحسين الأداء
const pool = new Pool({
    connectionString: process.env.DATABASE_URL,
    ssl: { rejectUnauthorized: false },
    max: 10,
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 5000,
});

/**
 * إدارة الجلسة من PostgreSQL - نسخة الإصلاح الجذري للتشفير
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
            console.error(`[DB_WRITE_ERR] ❌ ${id}:`, err.message);
        }
    };

    const readData = async (id) => {
        try {
            const res = await pool.query("SELECT data FROM whatsapp_sessions WHERE id = $1", [`${sessionId}_${id}`]);
            return res.rows.length > 0 ? JSON.parse(JSON.stringify(res.rows[0].data), BufferJSON.reviver) : null;
        } catch (err) { return null; }
    };

    const removeData = async (id) => {
        try {
            await pool.query("DELETE FROM whatsapp_sessions WHERE id = $1", [`${sessionId}_${id}`]);
        } catch (e) {}
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
                    // 🛠️ إصلاح PreKey: ضمان حفظ جميع المفاتيح بشكل متزامن قبل المتابعة
                    const tasks = [];
                    for (const type in data) {
                        for (const id in data[type]) {
                            const value = data[type][id];
                            const keyPath = `${type}-${id}`;
                            if (value) {
                                tasks.push(writeData(value, keyPath));
                            } else {
                                tasks.push(removeData(keyPath));
                            }
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
 * المحرك الرئيسي - مع تعطيل المزامنة الثقيلة لمنع الانهيار
 */
function initWhatsApp(storeId, phoneNumber = null) {
    return new Promise(async (resolve, reject) => {
        
        if (activeSessions[storeId]) {
            console.log(`[CLEANUP] 🔄 إعادة تهيئة الجلسة للمتجر ${storeId}`);
            try {
                activeSessions[storeId].ev.removeAllListeners();
                if (activeSessions[storeId].ws) activeSessions[storeId].ws.close();
            } catch (e) {}
            delete activeSessions[storeId];
            if (!phoneNumber) return resolve({ status: "ALREADY_CONNECTED" });
        }

        try {
            const { state, saveCreds } = await usePostgresAuthState(storeId);
            
            // استخدام نسخة ثابتة ومستقرة لتجنب مشاكل الـ Binary Protocol
            const version = [2, 3000, 1015901307]; 

            const sock = makeWASocket({
                version,
                auth: state,
                logger: pino({ level: "error" }),
                browser: ["Ubuntu", "Chrome", "20.0.04"],
                printQRInTerminal: false,
                syncFullHistory: false,           // توفير الرام
                shouldSyncHistoryMessage: () => false, // 🛠️ منع أخطاء bad-request
                connectTimeoutMs: 60000,
                keepAliveIntervalMs: 30000,       // منع خمول Render
                transactionOpts: { maxRetries: 3, delayBetweenRetriesMs: 2000 },
            });

            activeSessions[storeId] = sock;

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
                    console.log(`[CONN_LOST] ❌ فصل: ${storeId} | الرمز: ${statusCode}`);

                    if (activeSessions[storeId]) {
                        activeSessions[storeId].ev.removeAllListeners();
                        delete activeSessions[storeId];
                    }

                    if (statusCode === DisconnectReason.loggedOut) {
                        await pool.query("DELETE FROM whatsapp_sessions WHERE id LIKE $1", [`${storeId}_%`]);
                        reject(new Error("SESSION_TERMINATED"));
                    } else {
                        setTimeout(() => initWhatsApp(storeId, phoneNumber).catch(() => {}), 10000);
                    }
                } else if (connection === "open") {
                    console.log(`[CONN_OPEN] ✅ المتجر ${storeId} متصل.`);
                    if (phoneNumber && sock.user) {
                        const myLid = sock.user.id.split(':')[0].split('@')[0];
                        await pool.query(
                            "INSERT INTO phone_mappings (store_id, lid, real_phone) VALUES ($1, $2, $3) ON CONFLICT (lid) DO UPDATE SET real_phone = $3",
                            [storeId, myLid, phoneNumber.replace(/\D/g, '')]
                        ).catch(() => {});
                    }
                    resolve({ status: "connected" });
                }
            });

            sock.ev.on("messages.upsert", async (m) => {
                if (m.type !== "notify") return;
                if (!sock.ws || sock.ws.readyState !== 1) return;

                for (const msg of m.messages) {
                    if (!msg.key.fromMe && msg.message) {
                        const remoteJid = msg.key.remoteJid;
                        // تنظيف الـ JID لاستخدامه في البايثون
                        const senderJid = remoteJid.includes(':') ? `${remoteJid.split(':')[0]}@${remoteJid.split('@')[1]}` : remoteJid;

                        const text = msg.message.conversation || 
                                     msg.message.extendedTextMessage?.text || 
                                     msg.message.buttonsResponseMessage?.selectedButtonId ||
                                     msg.message.listResponseMessage?.title;

                        if (text) {
                            console.log(`[INCOMING] 📨 ${storeId} <- ${senderJid}: ${text}`);
                            axios.post(`${PYTHON_BACKEND_URL}/webhook/node-incoming`, {
                                storeId, customerPhone: senderJid, message: text
                            }, { timeout: 8000 }).catch(() => {});
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
 * مسارات API
 */
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
            await initWhatsApp(storeId);
            sock = activeSessions[storeId];
        }

        const jid = customerPhone.includes('@') ? customerPhone : `${customerPhone.replace(/\D/g, '')}@s.whatsapp.net`;
        
        const sent = await Promise.race([
            sock.sendMessage(jid, { text }),
            new Promise((_, r) => setTimeout(() => r(new Error("TIMEOUT")), 15000))
        ]);

        res.json({ status: "success", id: sent.key.id });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.get('/health', (req, res) => res.send("OK"));

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`🚀 Bridge v3 Active on port ${PORT}`));
