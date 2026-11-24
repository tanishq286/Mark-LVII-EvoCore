package com.marklvii.jarvis.modules.comms

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import java.util.concurrent.CopyOnWriteArrayList

data class UnreadChatMessage(
    val sender: String,
    val message: String,
    val timestampMs: Long
)

class JarvisNotificationListener : NotificationListenerService() {
    companion object {
        private val unreadMessages = CopyOnWriteArrayList<UnreadChatMessage>()
        private val WHATSAPP_PACKAGES = setOf("com.whatsapp", "com.whatsapp.w4b")

        fun getUnreadWhatsAppMessages(): List<UnreadChatMessage> = unreadMessages.toList()
        fun clearUnread() = unreadMessages.clear()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName !in WHATSAPP_PACKAGES) return
        val extras = sbn.notification.extras ?: return
        val sender = extras.getCharSequence("android.title")?.toString().orEmpty().trim()
        val text = extras.getCharSequence("android.text")?.toString().orEmpty().trim()
        if (sender.isNotBlank() && text.isNotBlank()) {
            unreadMessages.add(UnreadChatMessage(sender, text, sbn.postTime))
            if (unreadMessages.size > 40) unreadMessages.removeAt(0)
        }
    }
}
