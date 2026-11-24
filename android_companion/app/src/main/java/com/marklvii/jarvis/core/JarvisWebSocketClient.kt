package com.marklvii.jarvis.core

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.UUID
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/**
 * Bidirectional TLS-pinned WebSocket client bridging the Android companion
 * with Mark-LVII's dashboard/server.py (`device_register`, `heartbeat`,
 * `pc_to_android` -> `android_result`, and `tool_invoke` / `android_event`).
 */
class JarvisWebSocketClient(
    private val onStatus: (String) -> Unit = {},
    private val onLog: (String) -> Unit = {},
    private val onPcCommand: suspend (PcToAndroidCommand) -> AndroidResult
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var webSocket: WebSocket? = null
    private var heartbeatJob: Job? = null
    private var client: OkHttpClient = buildClient("")

    @Volatile
    var serverBaseUrl: String = ""
        private set

    @Volatile
    var authToken: String = ""
        private set

    @Volatile
    var sessionKey: String = ""
        private set

    @Volatile
    var aesKey: ByteArray? = null
        private set

    @Volatile
    var deviceId: String = "android-" + UUID.randomUUID().toString().take(8)
        private set

    @Volatile
    var lastRegisterMeta: DeviceRegister? = null

    var telemetryProvider: () -> Pair<Int?, Boolean?> = { Pair(null, null) }

    private fun buildClient(expectedSha256: String): OkHttpClient {
        val builder = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS)

        val pin = expectedSha256.trim().lowercase().replace(":", "")
        if (pin.isNotEmpty()) {
            val trustManager = object : X509TrustManager {
                override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
                override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
                override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                    val leaf = chain?.firstOrNull()
                        ?: throw CertificateException("Empty server certificate chain")
                    val digest = MessageDigest.getInstance("SHA-256")
                        .digest(leaf.encoded)
                        .joinToString("") { "%02x".format(it) }
                    if (digest != pin) {
                        throw CertificateException("TLS pin mismatch: expected=$pin actual=$digest")
                    }
                }
            }
            val ssl = SSLContext.getInstance("TLS")
            ssl.init(null, arrayOf(trustManager), null)
            builder.sslSocketFactory(ssl.socketFactory, trustManager)
            builder.hostnameVerifier { _, _ -> true }
        }
        return builder.build()
    }

    fun pairAndConnect(
        baseUrl: String,
        pin: String,
        deviceName: String,
        deviceModel: String,
        androidVersion: String,
        capabilities: List<String>,
        certSha256: String = ""
    ) {
        if (!PrivacyConfig.isOutboundSyncAllowed()) {
            onStatus("PRIVACY LOCAL")
            onLog("Pairing blocked: 100% On-Device Privacy Mode is enabled.")
            return
        }
        serverBaseUrl = baseUrl.trimEnd('/')
        client = buildClient(certSha256)
        scope.launch {
            try {
                val payload = JSONObject().apply {
                    put("pin", pin.trim())
                    put("device_id", deviceId)
                    put("name", deviceName)
                    put("model", deviceModel)
                    put("android_version", androidVersion)
                    put("capabilities", org.json.JSONArray(capabilities))
                }
                val req = Request.Builder()
                    .url("$serverBaseUrl/api/android/pair")
                    .post(payload.toString().toRequestBody("application/json".toMediaType()))
                    .build()
                client.newCall(req).execute().use { resp ->
                    val bodyStr = resp.body?.string().orEmpty()
                    val json = JSONObject(bodyStr)
                    if (!resp.isSuccessful || !json.optBoolean("ok", false)) {
                        onStatus("PAIR FAILED")
                        onLog("Pair error: ${json.optString("error", "HTTP ${resp.code}")}")
                        return@use
                    }
                    authToken = json.getString("token")
                    sessionKey = json.optString("session_key", pin.trim())
                    aesKey = CryptoEnvelope.deriveKey(sessionKey)
                    val pinnedHash = certSha256.ifBlank { json.optString("cert_sha256", "") }
                    if (pinnedHash.isNotBlank() && certSha256.isBlank()) {
                        client = buildClient(pinnedHash)
                    }
                    val (batt, chg) = telemetryProvider()
                    lastRegisterMeta = DeviceRegister(
                        deviceId = deviceId,
                        name = deviceName,
                        model = deviceModel,
                        androidVersion = androidVersion,
                        capabilities = capabilities,
                        privacyMode = PrivacyConfig.onDeviceOnly,
                        battery = batt,
                        charging = chg
                    )
                    connectSocket()
                }
            } catch (e: Exception) {
                onStatus("OFFLINE")
                onLog("Pair exception: ${e.message}")
            }
        }
    }

    fun connectSocket() {
        if (!PrivacyConfig.isOutboundSyncAllowed() || authToken.isBlank() || serverBaseUrl.isBlank()) {
            return
        }
        webSocket?.close(1000, "Reconnect")
        val wsUrl = serverBaseUrl
            .replaceFirst("https://", "wss://")
            .replaceFirst("http://", "ws://") + "/ws?token=$authToken"

        val request = Request.Builder().url(wsUrl).build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                onStatus("CONNECTED")
                onLog("TLS WebSocket paired cleanly.")
                lastRegisterMeta?.let { webSocket.send(it.toJson().toString()) }
                startHeartbeat()
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                scope.launch { handleInbound(webSocket, text) }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                stopHeartbeat()
                onStatus(if (PrivacyConfig.onDeviceOnly) "PRIVACY LOCAL" else "DISCONNECTED")
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                stopHeartbeat()
                onStatus("RETRYING")
                onLog("WebSocket failure: ${t.message}")
                if (PrivacyConfig.isOutboundSyncAllowed() && authToken.isNotBlank()) {
                    scope.launch {
                        delay(4000)
                        connectSocket()
                    }
                }
            }
        })
    }

    private suspend fun handleInbound(ws: WebSocket, raw: String) {
        try {
            val obj = JSONObject(raw)
            when (obj.optString("type")) {
                "pc_to_android" -> {
                    val cmd = PcToAndroidCommand.fromJson(obj)
                    onLog("PC -> Android: ${cmd.action}")
                    val res = try {
                        onPcCommand(cmd)
                    } catch (e: Exception) {
                        AndroidResult(cmd.requestId, ok = false, error = e.message ?: "error")
                    }
                    ws.send(res.toJson().toString())
                }
                "tool_result" -> onLog("PC Tool [${obj.optString("request_id")}]: ${obj.optString("result", obj.optString("error"))}")
                "sys", "jarvis_reply" -> onLog("JARVIS: ${obj.optString("text")}")
            }
        } catch (e: Exception) {
            onLog("WS parse error: ${e.message}")
        }
    }

    private fun startHeartbeat() {
        heartbeatJob?.cancel()
        heartbeatJob = scope.launch {
            while (isActive && PrivacyConfig.isOutboundSyncAllowed()) {
                delay(25_000)
                val (batt, chg) = telemetryProvider()
                val hb = Heartbeat(
                    deviceId = deviceId,
                    battery = batt,
                    charging = chg,
                    privacyMode = PrivacyConfig.onDeviceOnly
                )
                webSocket?.send(hb.toJson().toString())
            }
        }
    }

    private fun stopHeartbeat() {
        heartbeatJob?.cancel()
        heartbeatJob = null
    }

    fun sendCommandText(text: String): Boolean {
        if (!PrivacyConfig.isOutboundSyncAllowed()) {
            onLog("Blocked by On-Device Privacy Mode.")
            return false
        }
        val ws = webSocket ?: return false
        val key = aesKey
        val payload = JSONObject().apply {
            put("type", "command")
            if (key != null && serverBaseUrl.startsWith("https://")) {
                put("enc", CryptoEnvelope.encrypt(key, text))
            } else {
                put("text", text)
            }
        }
        return ws.send(payload.toString())
    }

    fun invokePcTool(tool: String, parameters: JSONObject = JSONObject()): Boolean {
        if (!PrivacyConfig.isOutboundSyncAllowed()) return false
        val ws = webSocket ?: return false
        val reqId = "and_${UUID.randomUUID().toString().take(8)}"
        return ws.send(ToolInvoke(reqId, tool, parameters).toJson().toString())
    }

    fun emitEvent(event: String, payload: JSONObject = JSONObject()): Boolean {
        if (!PrivacyConfig.isOutboundSyncAllowed()) return false
        val ws = webSocket ?: return false
        return ws.send(AndroidEvent(event, payload).toJson().toString())
    }

    fun applyPrivacyMode(enabled: Boolean) {
        PrivacyConfig.onDeviceOnly = enabled
        if (enabled) {
            stopHeartbeat()
            webSocket?.close(1000, "On-Device Privacy Mode enabled")
            webSocket = null
            onStatus("PRIVACY LOCAL")
            onLog("100% On-Device Privacy Mode active — outbound network suspended.")
        } else {
            onStatus("RECONNECTING")
            connectSocket()
        }
    }
}
