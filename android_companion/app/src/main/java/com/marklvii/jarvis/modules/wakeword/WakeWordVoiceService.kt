package com.marklvii.jarvis.modules.wakeword

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.IBinder
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import androidx.core.app.NotificationCompat
import java.util.Locale

/**
 * 2/7 Android MAX: Foreground hotword & Hinglish/Hindi/English voice command listener.
 */
class WakeWordVoiceService : Service(), TextToSpeech.OnInitListener {
    companion object {
        const val CHANNEL_ID = "jarvis_wakeword"

        @Volatile
        var isListening: Boolean = false

        @Volatile
        var onWakeCommand: ((phrase: String, command: String, lang: String) -> Unit)? = null
    }

    private var recognizer: SpeechRecognizer? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false

    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "JARVIS Wake Listener", NotificationManager.IMPORTANCE_LOW)
        )
        tts = TextToSpeech(this, this)
    }

    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            tts?.language = Locale("en", "IN")
            ttsReady = true
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(202, buildNotif())
        startLoop()
        return START_STICKY
    }

    fun speak(text: String) {
        if (ttsReady && text.isNotBlank()) {
            tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "jarvis_wake")
        }
    }

    private fun startLoop() {
        if (isListening) return
        if (!SpeechRecognizer.isRecognitionAvailable(this)) return
        isListening = true
        recognizer = SpeechRecognizer.createSpeechRecognizer(this).apply {
            setRecognitionListener(object : RecognitionListener {
                override fun onReadyForSpeech(params: Bundle?) {}
                override fun onBeginningOfSpeech() {}
                override fun onRmsChanged(rmsdB: Float) {}
                override fun onBufferReceived(buffer: ByteArray?) {}
                override fun onEndOfSpeech() {}
                override fun onEvent(eventType: Int, params: Bundle?) {}

                override fun onError(error: Int) {
                    if (isListening) listenOnce()
                }

                override fun onPartialResults(partialResults: Bundle?) {}

                override fun onResults(results: Bundle?) {
                    val list = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION).orEmpty()
                    for (candidate in list) {
                        val hit = WakePhraseMatcher.match(candidate)
                        if (hit.matched) {
                            if (hit.trailingCommand.isEmpty()) speak("Yes, sir?")
                            onWakeCommand?.invoke(hit.wakePhrase, hit.trailingCommand, hit.languageHint)
                            break
                        }
                    }
                    if (isListening) listenOnce()
                }
            })
        }
        listenOnce()
    }

    private fun listenOnce() {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-IN")
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, "hi-IN")
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
        }
        try {
            recognizer?.startListening(intent)
        } catch (_: Exception) {
        }
    }

    private fun buildNotif(): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("JARVIS Voice Wake Active")
            .setContentText("Listening for 'Hey Jarvis' / 'Bolo Jarvis'")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .build()

    override fun onDestroy() {
        isListening = false
        recognizer?.destroy()
        tts?.shutdown()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
