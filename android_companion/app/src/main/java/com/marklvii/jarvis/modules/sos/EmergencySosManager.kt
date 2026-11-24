package com.marklvii.jarvis.modules.sos

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.os.Build
import android.telephony.SmsManager
import androidx.core.content.ContextCompat
import com.google.android.gms.location.LocationServices
import com.marklvii.jarvis.core.JarvisWebSocketClient
import kotlinx.coroutines.suspendCancellableCoroutine
import org.json.JSONObject
import kotlin.coroutines.resume

/**
 * 6/7 Android MAX: Emergency SOS + Live FusedLocation GPS Dispatch.
 */
class EmergencySosManager(private val context: Context) {

    suspend fun getLastGpsLocation(): Location? {
        val fine = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION)
        val coarse = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION)
        if (fine != PackageManager.PERMISSION_GRANTED && coarse != PackageManager.PERMISSION_GRANTED) {
            return null
        }
        val client = LocationServices.getFusedLocationProviderClient(context)
        return suspendCancellableCoroutine { cont ->
            client.lastLocation
                .addOnSuccessListener { loc -> if (cont.isActive) cont.resume(loc) }
                .addOnFailureListener { if (cont.isActive) cont.resume(null) }
        }
    }

    suspend fun triggerSos(
        emergencyContactNumber: String,
        reason: String,
        wsClient: JarvisWebSocketClient?,
        confirmed: Boolean
    ): JSONObject {
        if (!confirmed) {
            return JSONObject()
                .put("ok", false)
                .put("error", "SOS blocked: explicit safety confirmation required.")
        }
        val loc = getLastGpsLocation()
        val coordsText = if (loc != null) {
            "https://maps.google.com/?q=${loc.latitude},${loc.longitude}"
        } else {
            "GPS unavailable"
        }
        val msg = "EMERGENCY SOS via JARVIS ($reason). Live Location: $coordsText"

        var smsSent = false
        val hasSms = ContextCompat.checkSelfPermission(context, Manifest.permission.SEND_SMS) ==
            PackageManager.PERMISSION_GRANTED
        if (hasSms && emergencyContactNumber.isNotBlank()) {
            runCatching {
                val sms = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    context.getSystemService(SmsManager::class.java)
                } else {
                    @Suppress("DEPRECATION")
                    SmsManager.getDefault()
                }
                sms.sendTextMessage(emergencyContactNumber, null, msg, null, null)
                smsSent = true
            }
        }

        val payload = JSONObject().apply {
            put("reason", reason)
            put("maps_url", coordsText)
            put("sms_dispatched", smsSent)
            if (loc != null) {
                put("lat", loc.latitude)
                put("lon", loc.longitude)
            }
        }
        wsClient?.emitEvent("sos_triggered", payload)
        return JSONObject().put("ok", true).put("sos", payload)
    }
}
