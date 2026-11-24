package com.marklvii.jarvis.ui

import android.Manifest
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.speech.tts.TextToSpeech
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.marklvii.jarvis.core.AndroidResult
import com.marklvii.jarvis.core.JarvisWebSocketClient
import com.marklvii.jarvis.core.PcToAndroidCommand
import com.marklvii.jarvis.core.PrivacyConfig
import com.marklvii.jarvis.modules.antitheft.AntiTheftGuardService
import com.marklvii.jarvis.modules.antitheft.JarvisDeviceAdminReceiver
import com.marklvii.jarvis.modules.camera.VoiceCameraController
import com.marklvii.jarvis.modules.comms.WhatsAppAndCallController
import com.marklvii.jarvis.modules.sos.EmergencySosManager
import com.marklvii.jarvis.modules.toggles.MobileToggleManager
import com.marklvii.jarvis.modules.vision.ScreenVisionScrollService
import com.marklvii.jarvis.modules.wakeword.WakeWordVoiceService
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.Locale

class MainActivity : ComponentActivity() {
    private lateinit var toggleManager: MobileToggleManager
    private lateinit var commsController: WhatsAppAndCallController
    private lateinit var sosManager: EmergencySosManager
    private lateinit var cameraController: VoiceCameraController
    private var tts: TextToSpeech? = null
    private lateinit var wsClient: JarvisWebSocketClient

    private val statusState = mutableStateOf("OFFLINE")
    private val logsState = mutableStateListOf("Ready for LAN pairing or offline local mode.")

    private val permLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { granted ->
        appendLog("Permissions updated: ${granted.count { it.value }}/${granted.size} granted.")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        toggleManager = MobileToggleManager(this)
        commsController = WhatsAppAndCallController(this)
        sosManager = EmergencySosManager(this)
        cameraController = VoiceCameraController(this)
        tts = TextToSpeech(this) { st ->
            if (st == TextToSpeech.SUCCESS) tts?.language = Locale("en", "IN")
        }

        wsClient = JarvisWebSocketClient(
            onStatus = { s -> runOnUiThread { statusState.value = s } },
            onLog = { line -> appendLog(line) },
            onPcCommand = { cmd -> handlePcCommand(cmd) }
        ).apply {
            telemetryProvider = {
                val snap = toggleManager.batterySnapshot()
                Pair(snap.optInt("battery_percent", -1), snap.optBoolean("charging", false))
            }
        }

        AntiTheftGuardService.onTamperTriggered = { reason ->
            appendLog("ANTI-THEFT TRIGGERED: $reason")
            cameraController.bind(this, frontCamera = true)
            cameraController.capturePhoto("intruder") {}
            wsClient.emitEvent("anti_theft_triggered", JSONObject().put("reason", reason))
        }

        WakeWordVoiceService.onWakeCommand = { phrase, command, lang ->
            appendLog("Wake [$lang]: $phrase -> $command")
            if (command.isNotBlank() && PrivacyConfig.isOutboundSyncAllowed()) {
                wsClient.sendCommandText(command)
            }
        }

        val perms = buildList {
            add(Manifest.permission.RECORD_AUDIO)
            add(Manifest.permission.CAMERA)
            add(Manifest.permission.ACCESS_FINE_LOCATION)
            add(Manifest.permission.ACCESS_COARSE_LOCATION)
            add(Manifest.permission.SEND_SMS)
            add(Manifest.permission.CALL_PHONE)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        permLauncher.launch(perms.toTypedArray())

        setContent {
            val scheme = darkColorScheme(
                primary = Color(0xFF00D4FF),
                secondary = Color(0xFFFF6B00),
                background = Color(0xFF07090F),
                surface = Color(0xFF0E1621),
                onPrimary = Color(0xFF041017),
                onBackground = Color(0xFFE0F7FF),
                onSurface = Color(0xFFE0F7FF)
            )
            MaterialTheme(colorScheme = scheme) {
                CompanionScreen()
            }
        }
    }

    private fun appendLog(msg: String) {
        runOnUiThread {
            logsState.add(0, msg)
            if (logsState.size > 40) logsState.removeAt(logsState.lastIndex)
        }
    }

    private suspend fun handlePcCommand(cmd: PcToAndroidCommand): AndroidResult {
        val p = cmd.params
        return when (cmd.action.lowercase()) {
            "battery_gps" -> {
                val batt = toggleManager.batterySnapshot()
                val loc = sosManager.getLastGpsLocation()
                if (loc != null) {
                    batt.put("lat", loc.latitude)
                    batt.put("lon", loc.longitude)
                }
                AndroidResult(cmd.requestId, true, batt)
            }
            "toggle" -> {
                val out = toggleManager.handleToggle(p.optString("target"), p.optString("value"))
                AndroidResult(cmd.requestId, out.optBoolean("ok", true), out, out.optString("error"))
            }
            "scroll" -> {
                val svc = ScreenVisionScrollService.instance
                    ?: return AndroidResult(cmd.requestId, false, error = "Enable Accessibility service first")
                val label = p.optString("label")
                val ok = if (label.isNotBlank()) svc.autoTapByLabel(label)
                else svc.performScroll(p.optString("direction", "down"))
                AndroidResult(cmd.requestId, ok, JSONObject().put("scrolled", ok))
            }
            "speak" -> {
                val text = p.optString("text", commsController.readUnreadAloud(tts))
                tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "pc_speak")
                AndroidResult(cmd.requestId, true, JSONObject().put("spoken", text))
            }
            "siren" -> {
                val act = if (p.optBoolean("active", true)) AntiTheftGuardService.ACTION_TRIGGER_SIREN
                else AntiTheftGuardService.ACTION_DISARM
                startForegroundService(Intent(this, AntiTheftGuardService::class.java).setAction(act))
                AndroidResult(cmd.requestId, true, JSONObject().put("siren", p.optBoolean("active", true)))
            }
            "lock_screen" -> {
                val dpm = getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
                val admin = ComponentName(this, JarvisDeviceAdminReceiver::class.java)
                if (dpm.isAdminActive(admin)) {
                    dpm.lockNow()
                    AndroidResult(cmd.requestId, true, JSONObject().put("locked", true))
                } else {
                    AndroidResult(cmd.requestId, false, error = "Device Admin not active")
                }
            }
            "camera" -> {
                var resultMsg = "Camera queued"
                runOnUiThread {
                    val mode = p.optString("mode", "photo").lowercase()
                    cameraController.bind(this, frontCamera = (mode == "selfie"))
                    if (mode == "video") {
                        cameraController.toggleVideoRecording { appendLog(it) }
                    } else {
                        cameraController.capturePhoto("remote") { r ->
                            appendLog("Camera capture: ${r.getOrNull()?.name ?: r.exceptionOrNull()?.message}")
                        }
                    }
                    resultMsg = "Executed CameraX $mode"
                }
                AndroidResult(cmd.requestId, true, JSONObject().put("camera", resultMsg))
            }
            "whatsapp" -> {
                val msg = p.optString("message")
                val contact = p.optString("contact")
                val out = if (msg.isBlank()) commsController.readUnreadAloud(tts)
                else commsController.sendWhatsAppText(contact, msg)
                AndroidResult(cmd.requestId, true, JSONObject().put("whatsapp", out))
            }
            "call" -> {
                val out = commsController.startSpeakerphoneCall(p.optString("contact"))
                AndroidResult(cmd.requestId, true, JSONObject().put("call", out))
            }
            "sos" -> {
                val out = sosManager.triggerSos(
                    p.optString("contact", ""),
                    p.optString("message", "remote_pc_sos"),
                    wsClient,
                    confirmed = true
                )
                AndroidResult(cmd.requestId, out.optBoolean("ok", true), out)
            }
            else -> AndroidResult(cmd.requestId, false, error = "Unsupported action: ${cmd.action}")
        }
    }

    @Composable
    private fun CompanionScreen() {
        val scope = rememberCoroutineScope()
        var serverUrl by remember { mutableStateOf("http://192.168.1.100:8000") }
        var pin by remember { mutableStateOf("") }
        var certPin by remember { mutableStateOf("") }
        var cmdText by remember { mutableStateOf("") }
        var privacyOn by remember { mutableStateOf(PrivacyConfig.onDeviceOnly) }
        var wakeOn by remember { mutableStateOf(WakeWordVoiceService.isListening) }
        var antiTheftOn by remember { mutableStateOf(AntiTheftGuardService.isArmed) }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(Color(0xFF07090F))
                .padding(14.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    "MARK-LVII MAX",
                    color = Color(0xFF00D4FF),
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 3.sp
                )
                Text(
                    statusState.value,
                    color = if (statusState.value == "CONNECTED") Color(0xFF00FF88) else Color(0xFFFF6B00),
                    fontFamily = FontFamily.Monospace,
                    fontSize = 12.sp
                )
            }

            Card(colors = CardDefaults.cardColors(containerColor = Color(0xFF0E1621))) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("PC <-> ANDROID BRIDGE", color = Color(0xFF00D4FF), fontWeight = FontWeight.Bold, fontSize = 12.sp)
                    OutlinedTextField(
                        value = serverUrl,
                        onValueChange = { serverUrl = it },
                        label = { Text("Dashboard URL (http/https LAN IP)") },
                        modifier = Modifier.fillMaxWidth()
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(
                            value = pin,
                            onValueChange = { pin = it },
                            label = { Text("8-Char PIN") },
                            modifier = Modifier.weight(1f)
                        )
                        OutlinedTextField(
                            value = certPin,
                            onValueChange = { certPin = it },
                            label = { Text("TLS SHA-256 Pin (Opt)") },
                            modifier = Modifier.weight(1f)
                        )
                    }
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text("100% On-Device Privacy Mode", fontSize = 13.sp)
                        Switch(checked = privacyOn, onCheckedChange = {
                            privacyOn = it
                            wsClient.applyPrivacyMode(it)
                        })
                    }
                    Button(
                        onClick = {
                            wsClient.pairAndConnect(
                                baseUrl = serverUrl,
                                pin = pin,
                                deviceName = Build.MODEL ?: "Android Phone",
                                deviceModel = "${Build.MANUFACTURER} ${Build.MODEL}",
                                androidVersion = Build.VERSION.RELEASE ?: "Android",
                                capabilities = listOf("anti_theft", "wakeword", "whatsapp", "toggles", "accessibility_vision", "sos", "camerax"),
                                certSha256 = certPin
                            )
                        },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("Pair & Connect") }
                }
            }

            Card(colors = CardDefaults.cardColors(containerColor = Color(0xFF0E1621))) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("7/7 ANDROID MAX MODULES", color = Color(0xFF00D4FF), fontWeight = FontWeight.Bold, fontSize = 12.sp)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        OutlinedButton(
                            onClick = {
                                wakeOn = !wakeOn
                                val svc = Intent(this@MainActivity, WakeWordVoiceService::class.java)
                                if (wakeOn) startForegroundService(svc) else stopService(svc)
                            },
                            modifier = Modifier.weight(1f)
                        ) { Text(if (wakeOn) "Wake: ON" else "Wake: OFF") }

                        OutlinedButton(
                            onClick = {
                                antiTheftOn = !antiTheftOn
                                val act = if (antiTheftOn) AntiTheftGuardService.ACTION_ARM else AntiTheftGuardService.ACTION_DISARM
                                startForegroundService(Intent(this@MainActivity, AntiTheftGuardService::class.java).setAction(act))
                            },
                            modifier = Modifier.weight(1f)
                        ) { Text(if (antiTheftOn) "Anti-Theft: ARMED" else "Anti-Theft: OFF") }
                    }

                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        OutlinedButton(
                            onClick = {
                                val r = toggleManager.handleToggle("flashlight", "toggle")
                                appendLog("Torch: $r")
                            },
                            modifier = Modifier.weight(1f)
                        ) { Text("Flashlight") }

                        OutlinedButton(
                            onClick = { appendLog(commsController.readUnreadAloud(tts)) },
                            modifier = Modifier.weight(1f)
                        ) { Text("Read WhatsApp") }
                    }

                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        OutlinedButton(
                            onClick = { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) },
                            modifier = Modifier.weight(1f)
                        ) { Text("Scroll Vision") }

                        OutlinedButton(
                            onClick = {
                                cameraController.bind(this@MainActivity, frontCamera = true)
                                cameraController.capturePhoto("voice_snap") { res ->
                                    appendLog("CameraX snap: ${res.getOrNull()?.name ?: res.exceptionOrNull()?.message}")
                                }
                            },
                            modifier = Modifier.weight(1f)
                        ) { Text("CameraX Snap") }
                    }

                    Button(
                        onClick = {
                            scope.launch {
                                val out = sosManager.triggerSos("", "manual_app_sos", wsClient, confirmed = true)
                                appendLog("SOS: $out")
                            }
                        },
                        colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFFF3355)),
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("Emergency SOS + Live GPS", color = Color.White) }
                }
            }

            Card(colors = CardDefaults.cardColors(containerColor = Color(0xFF0E1621))) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = cmdText,
                        onValueChange = { cmdText = it },
                        label = { Text("Command to Windows JARVIS") },
                        modifier = Modifier.fillMaxWidth()
                    )
                    Button(
                        onClick = {
                            if (cmdText.isNotBlank()) {
                                wsClient.sendCommandText(cmdText)
                                appendLog("TO PC: $cmdText")
                                cmdText = ""
                            }
                        },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("Send Command") }
                }
            }

            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .border(1.dp, Color(0xFF1A3A4A), RoundedCornerShape(10.dp))
                    .padding(10.dp)
            ) {
                Text("TELEMETRY LOG", color = Color(0xFF5AB8CC), fontSize = 11.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(4.dp))
                logsState.take(10).forEach { item ->
                    Text(item, fontFamily = FontFamily.Monospace, fontSize = 11.sp, color = Color(0xFFBCEFFF))
                }
            }
        }
    }

    override fun onDestroy() {
        tts?.shutdown()
        super.onDestroy()
    }
}
