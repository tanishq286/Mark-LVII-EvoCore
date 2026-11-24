package com.marklvii.jarvis.core

class PrivacyState(initialOffline: Boolean = false) {
    @Volatile
    var onDeviceOnly: Boolean = initialOffline

    fun isOutboundSyncAllowed(): Boolean = !onDeviceOnly

    fun assertOutboundAllowed(operation: String) {
        check(!onDeviceOnly) {
            "Blocked '$operation': 100% On-Device Privacy Mode is enabled."
        }
    }
}

object PrivacyConfig {
    val state = PrivacyState(false)

    var onDeviceOnly: Boolean
        get() = state.onDeviceOnly
        set(value) {
            state.onDeviceOnly = value
        }

    fun isOutboundSyncAllowed(): Boolean = state.isOutboundSyncAllowed()
}
