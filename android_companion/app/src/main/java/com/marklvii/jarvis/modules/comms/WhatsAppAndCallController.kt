package com.marklvii.jarvis.modules.comms

import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.net.Uri
import android.speech.tts.TextToSpeech
import java.net.URLEncoder

/**
 * 3/7 Android MAX: Read unread WhatsApp notifications aloud, dispatch WhatsApp
 * text/call intents, and dial phone calls on speakerphone.
 */
class WhatsAppAndCallController(private val context: Context) {

    fun summarizeUnreadWhatsApp(): String {
        val msgs = JarvisNotificationListener.getUnreadWhatsAppMessages()
        if (msgs.isEmpty()) return "No unread WhatsApp messages cached."
        return msgs.takeLast(5).joinToString("; ") { "${it.sender}: ${it.message}" }
    }

    fun readUnreadAloud(tts: TextToSpeech?): String {
        val summary = summarizeUnreadWhatsApp()
        tts?.speak(summary, TextToSpeech.QUEUE_FLUSH, null, "wa_read")
        return summary
    }

    fun sendWhatsAppText(phoneOrContact: String, message: String): String {
        val cleanNum = phoneOrContact.filter { it.isDigit() }
        val encoded = URLEncoder.encode(message, "UTF-8")
        val uri = if (cleanNum.length >= 8) {
            Uri.parse("https://wa.me/$cleanNum?text=$encoded")
        } else {
            Uri.parse("whatsapp://send?text=$encoded")
        }
        val intent = Intent(Intent.ACTION_VIEW, uri).apply {
            setPackage("com.whatsapp")
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        return runCatching {
            context.startActivity(intent)
            "Opened WhatsApp composer for $phoneOrContact"
        }.getOrElse {
            "WhatsApp handoff unavailable: ${it.message}"
        }
    }

    fun startSpeakerphoneCall(number: String): String {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        am.mode = AudioManager.MODE_IN_COMMUNICATION
        @Suppress("DEPRECATION")
        am.isSpeakerphoneOn = true
        val uri = Uri.parse("tel:${Uri.encode(number)}")
        val callIntent = Intent(Intent.ACTION_CALL, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val dialIntent = Intent(Intent.ACTION_DIAL, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return runCatching {
            context.startActivity(callIntent)
            "Dialed $number with speakerphone enabled"
        }.getOrElse {
            runCatching {
                context.startActivity(dialIntent)
                "Opened dialer for $number with speakerphone prepared"
            }.getOrElse { e -> "Call handoff failed: ${e.message}" }
        }
    }
}
