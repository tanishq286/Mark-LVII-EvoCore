package com.marklvii.jarvis.modules.toggles

import android.app.NotificationManager
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.Intent
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.provider.AlarmClock
import android.provider.Settings
import org.json.JSONObject

/**
 * 4/7 Android MAX: Flashlight, Wi-Fi, Bluetooth, Brightness, Sound/DND,
 * Alarms, Battery Telemetry, and App Launching.
 */
class MobileToggleManager(private val context: Context) {
    private var torchEnabled = false

    fun batterySnapshot(): JSONObject {
        val bm = context.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        val pct = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        val charging = bm.isCharging
        return JSONObject().apply {
            put("battery_percent", pct)
            put("charging", charging)
        }
    }

    fun handleToggle(target: String, value: String?): JSONObject {
        val t = target.lowercase().trim()
        val v = value?.lowercase()?.trim()
        return when (t) {
            "flashlight", "torch" -> setTorch(
                when (v) {
                    "on", "true", "1" -> true
                    "off", "false", "0" -> false
                    else -> !torchEnabled
                }
            )
            "wifi" -> toggleWifi(v)
            "bluetooth" -> toggleBluetooth()
            "dnd" -> setDnd(v != "off" && v != "false")
            "brightness" -> setBrightness(v?.toIntOrNull() ?: 60)
            "sound", "ringer" -> setRinger(v ?: "normal")
            "alarm" -> setAlarm(v ?: "07:00")
            "launch_app" -> launchApp(value.orEmpty())
            else -> JSONObject().put("ok", false).put("error", "Unknown toggle: $target")
        }
    }

    fun setTorch(enable: Boolean): JSONObject {
        val cm = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val camId = cm.cameraIdList.firstOrNull { id ->
            cm.getCameraCharacteristics(id).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
        } ?: return JSONObject().put("ok", false).put("error", "No camera flash hardware")
        cm.setTorchMode(camId, enable)
        torchEnabled = enable
        return JSONObject().put("ok", true).put("flashlight", enable)
    }

    private fun toggleWifi(desired: String?): JSONObject {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val panel = Intent(Settings.Panel.ACTION_WIFI).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(panel)
            return JSONObject().put("ok", true).put("wifi", "panel_opened")
        }
        @Suppress("DEPRECATION")
        val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        @Suppress("DEPRECATION")
        wm.isWifiEnabled = desired != "off"
        return JSONObject().put("ok", true).put("wifi", desired != "off")
    }

    private fun toggleBluetooth(): JSONObject {
        val bm = context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
        val enabled = bm.adapter?.isEnabled == true
        val intent = Intent(Settings.ACTION_BLUETOOTH_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
        return JSONObject().put("ok", true).put("bluetooth_enabled", enabled)
    }

    private fun setDnd(enable: Boolean): JSONObject {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (!nm.isNotificationPolicyAccessGranted) {
            context.startActivity(
                Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
            return JSONObject().put("ok", false).put("error", "DND policy permission requested")
        }
        nm.setInterruptionFilter(
            if (enable) NotificationManager.INTERRUPTION_FILTER_PRIORITY
            else NotificationManager.INTERRUPTION_FILTER_ALL
        )
        return JSONObject().put("ok", true).put("dnd", enable)
    }

    private fun setBrightness(percent: Int): JSONObject {
        if (!Settings.System.canWrite(context)) {
            val i = Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(i)
            return JSONObject().put("ok", false).put("error", "WRITE_SETTINGS permission requested")
        }
        val level = ((percent.coerceIn(5, 100) / 100.0) * 255).toInt()
        Settings.System.putInt(context.contentResolver, Settings.System.SCREEN_BRIGHTNESS, level)
        return JSONObject().put("ok", true).put("brightness_percent", percent.coerceIn(5, 100))
    }

    private fun setRinger(mode: String): JSONObject {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        am.ringerMode = when (mode) {
            "silent" -> AudioManager.RINGER_MODE_SILENT
            "vibrate" -> AudioManager.RINGER_MODE_VIBRATE
            else -> AudioManager.RINGER_MODE_NORMAL
        }
        return JSONObject().put("ok", true).put("ringer_mode", mode)
    }

    private fun setAlarm(hhmm: String): JSONObject {
        val parts = hhmm.split(":")
        val hour = parts.getOrNull(0)?.toIntOrNull()?.coerceIn(0, 23) ?: 7
        val min = parts.getOrNull(1)?.toIntOrNull()?.coerceIn(0, 59) ?: 0
        val intent = Intent(AlarmClock.ACTION_SET_ALARM).apply {
            putExtra(AlarmClock.EXTRA_HOUR, hour)
            putExtra(AlarmClock.EXTRA_MINUTES, min)
            putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
        return JSONObject().put("ok", true).put("alarm", "%02d:%02d".format(hour, min))
    }

    private fun launchApp(pkgOrName: String): JSONObject {
        if (pkgOrName.isBlank()) return JSONObject().put("ok", false).put("error", "Missing package name")
        val launch = context.packageManager.getLaunchIntentForPackage(pkgOrName)
            ?: return JSONObject().put("ok", false).put("error", "Package not found: $pkgOrName")
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(launch)
        return JSONObject().put("ok", true).put("launched", pkgOrName)
    }
}
