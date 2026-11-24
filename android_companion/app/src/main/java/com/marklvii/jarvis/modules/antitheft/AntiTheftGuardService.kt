package com.marklvii.jarvis.modules.antitheft

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.IBinder
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * 1/7 Android MAX: Accelerometer motion + charger-disconnect anti-theft siren,
 * DevicePolicyManager screen lock, silent front-camera intruder hook, and PC alert.
 */
class AntiTheftGuardService : Service(), SensorEventListener {
    companion object {
        const val CHANNEL_ID = "jarvis_antitheft"
        const val ACTION_ARM = "com.marklvii.jarvis.ANTITHEFT_ARM"
        const val ACTION_DISARM = "com.marklvii.jarvis.ANTITHEFT_DISARM"
        const val ACTION_CHARGER_TAMPER = "com.marklvii.jarvis.ANTITHEFT_CHARGER"
        const val ACTION_TRIGGER_SIREN = "com.marklvii.jarvis.ANTITHEFT_SIREN"

        @Volatile
        var isArmed: Boolean = false

        @Volatile
        var onTamperTriggered: ((reason: String) -> Unit)? = null
    }

    private lateinit var sensorManager: SensorManager
    private val detector = MotionTamperDetector()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var sirenJob: Job? = null
    private var toneGen: ToneGenerator? = null

    override fun onCreate() {
        super.onCreate()
        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Anti-Theft Guard", NotificationManager.IMPORTANCE_LOW)
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(201, buildNotif())
        when (intent?.action) {
            ACTION_DISARM -> disarmAndStop()
            ACTION_CHARGER_TAMPER -> triggerAlarm("charger_disconnected")
            ACTION_TRIGGER_SIREN -> triggerAlarm("remote_siren")
            else -> arm()
        }
        return START_STICKY
    }

    private fun arm() {
        isArmed = true
        detector.reset()
        sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let { accel ->
            sensorManager.registerListener(this, accel, SensorManager.SENSOR_DELAY_NORMAL)
        }
    }

    private fun disarmAndStop() {
        isArmed = false
        stopSiren()
        sensorManager.unregisterListener(this)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onSensorChanged(event: SensorEvent) {
        if (!isArmed || event.sensor.type != Sensor.TYPE_ACCELEROMETER) return
        val x = event.values[0]
        val y = event.values[1]
        val z = event.values[2]
        if (detector.evaluate(x, y, z, System.currentTimeMillis())) {
            triggerAlarm("motion_tamper")
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    private fun triggerAlarm(reason: String) {
        lockDeviceIfAdmin()
        soundMaxSiren()
        onTamperTriggered?.invoke(reason)
    }

    private fun lockDeviceIfAdmin() {
        val dpm = getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
        val admin = ComponentName(this, JarvisDeviceAdminReceiver::class.java)
        if (dpm.isAdminActive(admin)) {
            dpm.lockNow()
        }
    }

    private fun soundMaxSiren() {
        val am = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val maxVol = am.getStreamMaxVolume(AudioManager.STREAM_ALARM)
        am.setStreamVolume(AudioManager.STREAM_ALARM, maxVol, 0)
        if (sirenJob?.isActive == true) return
        sirenJob = scope.launch {
            val tg = ToneGenerator(AudioManager.STREAM_ALARM, 100)
            toneGen = tg
            try {
                while (isActive) {
                    tg.startTone(ToneGenerator.TONE_CDMA_EMERGENCY_RINGBACK, 450)
                    delay(650)
                }
            } finally {
                tg.release()
            }
        }
    }

    private fun stopSiren() {
        sirenJob?.cancel()
        sirenJob = null
        try {
            toneGen?.release()
        } catch (_: Exception) {
        }
        toneGen = null
    }

    private fun buildNotif(): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("JARVIS Anti-Theft Guard")
            .setContentText(if (isArmed) "Motion & charger protection armed" else "Standby")
            .setSmallIcon(android.R.drawable.ic_lock_idle_lock)
            .setOngoing(true)
            .build()

    override fun onDestroy() {
        stopSiren()
        sensorManager.unregisterListener(this)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
