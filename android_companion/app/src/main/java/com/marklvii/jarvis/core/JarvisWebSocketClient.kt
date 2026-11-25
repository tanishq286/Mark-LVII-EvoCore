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
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.UUID
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/**
 * Bidirectional TLS-pinned WebSocket & HTTPS client bridging the Android companion
 * with Mark-LVII's dashboard/server.py (`device_register`, `heartbeat`,
 * `pc_to_android` -> `android_result`, live PC screen viewer + tap/type,
 * push-to-talk PCM16 `/ws/phone-audio`, mission log stream, and 1-tap HUD approvals).
 */
class JarvisWebSocketClient(
    private val onStatus: (String) -> Unit = {},
    private val onLog: (String) -> Unit = {},
    private val onMissionLog: (String) -> Unit = {},
    private val onConfirmPrompt: (HudConfirmPrompt?) -> Unit = {},
    private val onChoicePrompt: (HudChoicePrompt?) -> Unit = {},
    private val onPcCommand: suspend (PcToAndroidCommand) -> AndroidResult
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var webSocket: WebSocket? = null
    private var audioWebSocket: WebSocket? = null
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
    var pinnedCertSha256: String = ""
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

    private fun buildClient(expectedSha256: String, allowTofuBootstrap: Boolean = false): OkHttpClient {
        val builder = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS)

        val pin = expectedSha256.trim().lowercase().replace(":", "")
        if (pin.isNotEmpty() || allowTofuBootstrap) {
            val trustManager = object : X509TrustManager {
                override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
                override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
                override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                    val leaf = chain?.firstOrNull()
                        ?: throw CertificateException("Empty server certificate chain")
                    val digest = MessageDigest.getInstance("SHA-256")
                        .digest(leaf.encoded)
                        .joinToString("") { "%02x".format(it) }
                    if (pin.isNotEmpty() && digest != pin) {
                        throw CertificateException("TLS pin mismatch: expected=$pin actual=$digest")
                    }
                    if (pin.isEmpty() && allowTofuBootstrap) {
                        pinnedCertSha256 = digest
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
        val explicitPin = certSha256.trim().lowercase().replace(":", "")
        pinnedCertSha256 = explicitPin
        client = buildClient(explicitPin, allowTofuBootstrap = serverBaseUrl.startsWith("https://") && explicitPin.isEmpty())
        scope.launch {
            try {
                val payload = JSONObject().apply {
                    put("pin", pin.trim().uppercase())
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
                    sessionKey = json.optString("session_key", pin.trim().uppercase())
                    aesKey = CryptoEnvelope.deriveKey(sessionKey)
                    val serverFp = json.optString("cert_sha256", "").trim().lowercase()
                    val finalPin = explicitPin.ifBlank { serverFp.ifBlank { pinnedCertSha256 } }
                    if (finalPin.isNotBlank()) {
                        pinnedCertSha256 = finalPin
                        client = buildClient(finalPin, allowTofuBootstrap = false)
                        onLog("Pinned TLS SHA-256: ${finalPin.take(20)}…")
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
                webSocket.send(JSONObject().put("type", "confirm_pending_req").toString())
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
                "mission_log" -> {
                    val src = obj.optString("source", "brain")
                    onMissionLog("[$src] ${obj.optString("text")}")
                }
                "log" -> {
                    val spk = obj.optString("speaker", "jarvis").uppercase()
                    onMissionLog("$spk: ${obj.optString("text")}")
                }
                "confirm_prompt" -> {
                    onConfirmPrompt(
                        HudConfirmPrompt(
                            title = obj.optString("title", "Confirm Action"),
                            detail = obj.optString("detail", ""),
                            expiresInSec = obj.optDouble("expires_in", 90.0)
                        )
                    )
                }
                "choice_prompt" -> {
                    val arr = obj.optJSONArray("options")
                    val opts = mutableListOf<String>()
                    if (arr != null) {
                        for (i in 0 until arr.length()) opts.add(arr.optString(i))
                    }
                    onChoicePrompt(HudChoicePrompt(obj.optString("title", "Select Option"), obj.optString("detail", ""), opts))
                }
                "confirm_pending" -> {
                    val cf = obj.optJSONObject("confirm")
                    onConfirmPrompt(if (cf != null) HudConfirmPrompt(cf.optString("title"), cf.optString("detail"), cf.optDouble("expires_in", 90.0)) else null)
                    val ch = obj.optJSONObject("choice")
                    if (ch != null) {
                        val arr = ch.optJSONArray("options")
                        val opts = mutableListOf<String>()
                        if (arr != null) for (i in 0 until arr.length()) opts.add(arr.optString(i))
                        onChoicePrompt(HudChoicePrompt(ch.optString("title"), ch.optString("detail"), opts))
                    } else {
                        onChoicePrompt(null)
                    }
                }
                "confirm_cleared", "choice_cleared", "confirm_resolved" -> {
                    onConfirmPrompt(null)
                    onChoicePrompt(null)
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

    fun sendScreenTap(normX: Float, normY: Float, action: String = "tap"): Boolean {
        if (!PrivacyConfig.isOutboundSyncAllowed()) return false
        val ws = webSocket ?: return false
        return ws.send(ScreenInputCommand(action = action, normX = normX, normY = normY).toJson().toString())
    }

    fun sendScreenText(text: String): Boolean {
        if (!PrivacyConfig.isOutboundSyncAllowed() || text.isEmpty()) return false
        val ws = webSocket ?: return false
        return ws.send(ScreenInputCommand(action = "type", text = text).toJson().toString())
    }

    fun sendScreenKey(keys: String): Boolean {
        if (!PrivacyConfig.isOutboundSyncAllowed() || keys.isBlank()) return false
        val ws = webSocket ?: return false
        return ws.send(ScreenInputCommand(action = "key", keys = keys).toJson().toString())
    }

    fun resolveHudConfirm(accepted: Boolean): Boolean {
        if (!PrivacyConfig.isOutboundSyncAllowed()) return false
        val ws = webSocket ?: return false
        onConfirmPrompt(null)
        return ws.send(ConfirmResolveCommand(kind = "confirm", accepted = accepted).toJson().toString())
    }

    fun resolveHudChoice(selection: Int?): Boolean {
        if (!PrivacyConfig.isOutboundSyncAllowed()) return false
        val ws = webSocket ?: return false
        onChoicePrompt(null)
        return ws.send(ConfirmResolveCommand(kind = "choice", selection = selection).toJson().toString())
    }

    fun fetchScreenFrameBytes(): ByteArray? {
        if (!PrivacyConfig.isOutboundSyncAllowed() || authToken.isBlank() || serverBaseUrl.isBlank()) return null
        return try {
            val req = Request.Builder()
                .url("$serverBaseUrl/api/screen/frame")
                .header("Authorization", "Bearer $authToken")
                .build()
            client.newCall(req).execute().use { resp ->
                if (resp.isSuccessful) resp.body?.bytes() else null
            }
        } catch (_: Exception) {
            null
        }
    }

    fun openPhoneAudioSocket(): Boolean {
        if (!PrivacyConfig.isOutboundSyncAllowed() || authToken.isBlank() || serverBaseUrl.isBlank()) return false
        audioWebSocket?.close(1000, "Reset")
        val wsUrl = serverBaseUrl
            .replaceFirst("https://", "wss://")
            .replaceFirst("http://", "ws://") + "/ws/phone-audio?token=$authToken"
        audioWebSocket = client.newWebSocket(Request.Builder().url(wsUrl).build(), object : WebSocketListener() {})
        return true
    }

    fun sendPhoneAudioPcm16(pcmBytes: ByteArray): Boolean {
        if (!PrivacyConfig.isOutboundSyncAllowed()) return false
        return audioWebSocket?.send(pcmBytes.toByteString(0, pcmBytes.size)) ?: false
    }

    fun closePhoneAudioSocket() {
        audioWebSocket?.close(1000, "PTT released")
        audioWebSocket = null
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
            closePhoneAudioSocket()
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
