package com.marklvii.jarvis.core

import org.json.JSONArray
import org.json.JSONObject

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
