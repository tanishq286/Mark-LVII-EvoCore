package com.marklvii.jarvis.core

import org.json.JSONArray
import org.json.JSONObject
import java.net.URI

data class DeviceRegister(
    val deviceId: String,
    val name: String,
    val model: String,
    val androidVersion: String,
    val capabilities: List<String>,
    val privacyMode: Boolean = false,
    val battery: Int? = null,
    val charging: Boolean? = null
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("type", "device_register")
        put("device_id", deviceId)
        put("name", name)
        put("model", model)
        put("android_version", androidVersion)
        put("capabilities", JSONArray(capabilities))
        put("privacy_mode", privacyMode)
        if (battery != null) put("battery", battery)
        if (charging != null) put("charging", charging)
    }
}

data class Heartbeat(
    val deviceId: String,
    val battery: Int?,
    val charging: Boolean?,
    val privacyMode: Boolean,
    val location: JSONObject? = null
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("type", "heartbeat")
        put("device_id", deviceId)
        if (battery != null) put("battery", battery)
        if (charging != null) put("charging", charging)
        put("privacy_mode", privacyMode)
        if (location != null) put("location", location)
    }
}

data class PcToAndroidCommand(
    val requestId: String,
    val action: String,
    val params: JSONObject
) {
    companion object {
        fun fromJson(obj: JSONObject): PcToAndroidCommand = PcToAndroidCommand(
            requestId = obj.optString("request_id", ""),
            action = obj.optString("action", ""),
            params = obj.optJSONObject("params") ?: JSONObject()
        )
    }
}

data class AndroidResult(
    val requestId: String,
    val ok: Boolean,
    val data: JSONObject = JSONObject(),
    val error: String = ""
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("type", "android_result")
        put("request_id", requestId)
        put("ok", ok)
        put("data", data)
        if (error.isNotEmpty()) put("error", error)
    }
}

data class ToolInvoke(
    val requestId: String,
    val tool: String,
    val parameters: JSONObject = JSONObject()
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("type", "tool_invoke")
        put("request_id", requestId)
        put("tool", tool)
        put("parameters", parameters)
    }
}

data class AndroidEvent(
    val event: String,
    val payload: JSONObject = JSONObject()
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("type", "android_event")
        put("event", event)
        put("payload", payload)
    }
}

/**
 * 1-Tap QR pairing payload parser supporting:
 *   1. JSON QR payload from `/api/android/qr` (`{"url": "...", "pin": "...", "cert_sha256": "..."}`)
 *   2. 1-Tap PWA pairing URL (`https://172.16.6.143:8000/pwa?pin=ABCD2345&fp=deadbeef...`)
 *   3. Auto-login URL (`https://172.16.6.143:8000/auto-login?key=ABCD2345`)
 */
data class PairingQrPayload(
    val baseUrl: String,
    val pin: String,
    val certSha256: String
) {
    companion object {
        fun parse(raw: String): PairingQrPayload? {
            val trimmed = raw.trim()
            if (trimmed.isEmpty()) return null
            if (trimmed.startsWith("{")) {
                return try {
                    val obj = JSONObject(trimmed)
                    val inner = obj.optJSONObject("qr_payload") ?: obj
                    val url = inner.optString("url", "").trimEnd('/')
                    if (url.isBlank()) null else PairingQrPayload(
                        baseUrl = url,
                        pin = inner.optString("pin", "").trim().uppercase(),
                        certSha256 = inner.optString("cert_sha256", "").trim().lowercase()
                    )
                } catch (_: Exception) {
                    null
                }
            }
            return try {
                val uri = URI(trimmed)
                val scheme = (uri.scheme ?: "https").lowercase()
                val host = uri.host ?: return null
                val portPart = if (uri.port > 0) ":${uri.port}" else ""
                val base = "$scheme://$host$portPart"
                val params = mutableMapOf<String, String>()
                (uri.rawQuery ?: "").split("&").forEach { kv ->
                    val idx = kv.indexOf('=')
                    if (idx > 0) {
                        params[kv.substring(0, idx)] = java.net.URLDecoder.decode(kv.substring(idx + 1), "UTF-8")
                    }
                }
                val pin = (params["pin"] ?: params["key"] ?: "").trim().uppercase()
                val fp = (params["fp"] ?: params["cert_sha256"] ?: "").trim().lowercase()
                PairingQrPayload(baseUrl = base, pin = pin, certSha256 = fp)
            } catch (_: Exception) {
                null
            }
        }
    }
}

data class ScreenInputCommand(
    val action: String,
    val normX: Float? = null,
    val normY: Float? = null,
    val text: String = "",
    val keys: String = "",
    val direction: String = "down",
    val amount: Int = 3
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("type", "screen_input")
        put("action", action)
        if (normX != null) put("norm_x", normX.toDouble().coerceIn(0.0, 1.0))
        if (normY != null) put("norm_y", normY.toDouble().coerceIn(0.0, 1.0))
        if (text.isNotEmpty()) put("text", text)
        if (keys.isNotEmpty()) put("keys", keys)
        if (action == "scroll") {
            put("direction", direction)
            put("amount", amount)
        }
    }
}

data class HudConfirmPrompt(
    val title: String,
    val detail: String,
    val expiresInSec: Double = 90.0
)

data class HudChoicePrompt(
    val title: String,
    val detail: String,
    val options: List<String>
)

data class ConfirmResolveCommand(
    val kind: String = "confirm",
    val accepted: Boolean = false,
    val selection: Int? = null
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("type", "confirm_resolve")
        put("kind", kind)
        if (kind == "choice") {
            if (selection == null) put("selection", JSONObject.NULL) else put("selection", selection)
        } else {
            put("accepted", accepted)
        }
    }
}
