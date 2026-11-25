package com.marklvii.jarvis.ui

import android.Manifest
import android.annotation.SuppressLint
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.speech.tts.TextToSpeech
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.marklvii.jarvis.core.AndroidResult
import com.marklvii.jarvis.core.HudChoicePrompt
import com.marklvii.jarvis.core.HudConfirmPrompt
import com.marklvii.jarvis.core.JarvisWebSocketClient
import com.marklvii.jarvis.core.PairingQrPayload
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
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.util.Locale

class MainActivity : ComponentActivity() {
    private lateinit var toggleManager: MobileToggleManager
    private lateinit var commsController: WhatsAppAndCallController
    private lateinit var sosManager: EmergencySosManager
    private lateinit var cameraController: VoiceCameraController
    private var tts: TextToSpeech? = null
    private lateinit var wsClient: JarvisWebSocketClient
    private var pttJob: Job? = null

    private val statusState = mutableStateOf("OFFLINE")
    private val logsState = mutableStateListOf("Ready for 1-tap HTTPS QR pairing or offline local mode.")
    private val missionLogsState = mutableStateListOf("Waiting for Brain / Swarm / Mission stream…")
    private val confirmPromptState = mutableStateOf<HudConfirmPrompt?>(null)
    private val choicePromptState = mutableStateOf<HudChoicePrompt?>(null)

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
            onMissionLog = { line -> appendMissionLog(line) },
            onConfirmPrompt = { p -> runOnUiThread { confirmPromptState.value = p } },
            onChoicePrompt = { p -> runOnUiThread { choicePromptState.value = p } },
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

    private fun appendMissionLog(msg: String) {
        runOnUiThread {
            if (missionLogsState.firstOrNull()?.startsWith("Waiting for") == true) {
                missionLogsState.clear()
            }
            missionLogsState.add(0, msg)
            if (missionLogsState.size > 50) missionLogsState.removeAt(missionLogsState.lastIndex)
        }
    }

    @SuppressLint("MissingPermission")
    private fun togglePushToTalk(active: Boolean, scope: kotlinx.coroutines.CoroutineScope) {
        if (!active) {
            pttJob?.cancel()
            pttJob = null
            wsClient.closePhoneAudioSocket()
            return
        }
        if (!wsClient.openPhoneAudioSocket()) {
            appendLog("Pair first before streaming push-to-talk audio.")
            return
        }
        pttJob?.cancel()
        pttJob = scope.launch(Dispatchers.IO) {
            val rate = 16000
            val minBuf = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            val bufSize = maxOf(minBuf, 2048)
            val recorder = AudioRecord(
                MediaRecorder.AudioSource.MIC,
                rate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufSize
            )
            val buf = ByteArray(2048)
            try {
                recorder.startRecording()
                while (isActive) {
                    val read = recorder.read(buf, 0, buf.size)
                    if (read > 0) {
                        wsClient.sendPhoneAudioPcm16(buf.copyOf(read))
                    }
                }
            } catch (e: Exception) {
                appendLog("PTT mic error: ${e.message}")
            } finally {
                try { recorder.stop() } catch (_: Exception) {}
                recorder.release()
                wsClient.closePhoneAudioSocket()
            }
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
        var serverUrl by remember { mutableStateOf("https://172.16.6.143:8000") }
        var pin by remember { mutableStateOf("") }
        var certPin by remember { mutableStateOf("") }
        var qrRaw by remember { mutableStateOf("") }
        var cmdText by remember { mutableStateOf("") }
        var remoteTypeText by remember { mutableStateOf("") }
        var screenStreaming by remember { mutableStateOf(false) }
        var screenBitmap by remember { mutableStateOf<ImageBitmap?>(null) }
        var pttStreaming by remember { mutableStateOf(false) }
        var privacyOn by remember { mutableStateOf(PrivacyConfig.onDeviceOnly) }
        var wakeOn by remember { mutableStateOf(WakeWordVoiceService.isListening) }
        var antiTheftOn by remember { mutableStateOf(AntiTheftGuardService.isArmed) }

        val activeConfirm = confirmPromptState.value
        val activeChoice = choicePromptState.value

        LaunchedEffect(screenStreaming, statusState.value) {
            while (screenStreaming && statusState.value == "CONNECTED") {
                val bytes = withContext(Dispatchers.IO) { wsClient.fetchScreenFrameBytes() }
                if (bytes != null && bytes.isNotEmpty()) {
                    val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    if (bmp != null) screenBitmap = bmp.asImageBitmap()
                }
                delay(900)
            }
        }

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
                    "MARK-LVII OMNICORE",
                    color = Color(0xFF00D4FF),
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 2.5.sp
                )
                Text(
                    statusState.value,
                    color = if (statusState.value == "CONNECTED") Color(0xFF00FF88) else Color(0xFFFF6B00),
                    fontFamily = FontFamily.Monospace,
                    fontSize = 12.sp
                )
            }

            // (c) 1-Tap Remote HUD Approval Banner (CONFIRM & ChoiceBanner)
            if (activeConfirm != null) {
                Card(
                    colors = CardDefaults.cardColors(containerColor = Color(0xFF220B05)),
                    modifier = Modifier.fillMaxWidth().border(2.dp, Color(0xFFFF6B00), RoundedCornerShape(12.dp))
                ) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("⚠ HUD CONFIRMATION REQUIRED", color = Color(0xFFFF6B00), fontWeight = FontWeight.Bold, fontSize = 12.sp)
                        Text(activeConfirm.title, fontWeight = FontWeight.Bold, fontSize = 14.sp)
                        if (activeConfirm.detail.isNotBlank()) {
                            Text(activeConfirm.detail, fontSize = 12.sp, color = Color(0xFFCBD5E1))
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                            Button(
                                onClick = { wsClient.resolveHudConfirm(true) },
                                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF00FF88)),
                                modifier = Modifier.weight(1f)
                            ) { Text("CONFIRM", color = Color(0xFF041017), fontWeight = FontWeight.Bold) }
                            OutlinedButton(
                                onClick = { wsClient.resolveHudConfirm(false) },
                                modifier = Modifier.weight(1f)
                            ) { Text("CANCEL") }
                        }
                    }
                }
            }

            if (activeChoice != null) {
                Card(
                    colors = CardDefaults.cardColors(containerColor = Color(0xFF081826)),
                    modifier = Modifier.fillMaxWidth().border(2.dp, Color(0xFF00D4FF), RoundedCornerShape(12.dp))
                ) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("◆ HUD CHOICE BANNER", color = Color(0xFF00D4FF), fontWeight = FontWeight.Bold, fontSize = 12.sp)
                        Text(activeChoice.title, fontWeight = FontWeight.Bold, fontSize = 14.sp)
                        activeChoice.options.forEachIndexed { idx, opt ->
                            OutlinedButton(
                                onClick = { wsClient.resolveHudChoice(idx) },
                                modifier = Modifier.fillMaxWidth()
                            ) { Text("${idx + 1}. $opt") }
                        }
                        OutlinedButton(
                            onClick = { wsClient.resolveHudChoice(null) },
                            modifier = Modifier.fillMaxWidth()
                        ) { Text("Cancel Choice") }
                    }
                }
            }

            // 1-Tap QR / HTTPS Pairing Card
            Card(colors = CardDefaults.cardColors(containerColor = Color(0xFF0E1621))) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("1-TAP HTTPS PAIRING & TLS PIN", color = Color(0xFF00D4FF), fontWeight = FontWeight.Bold, fontSize = 12.sp)
                    OutlinedTextField(
                        value = qrRaw,
                        onValueChange = { raw ->
                            qrRaw = raw
                            PairingQrPayload.parse(raw)?.let { parsed ->
                                serverUrl = parsed.baseUrl
                                if (parsed.pin.isNotBlank()) pin = parsed.pin
                                if (parsed.certSha256.isNotBlank()) certPin = parsed.certSha256
                            }
                        },
                        label = { Text("Paste Scanned QR Link or JSON (1-Tap Auto-Fill)") },
                        modifier = Modifier.fillMaxWidth()
                    )
                    OutlinedTextField(
                        value = serverUrl,
                        onValueChange = { serverUrl = it },
                        label = { Text("HTTPS LAN / Tailscale / Tunnel URL") },
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
                            label = { Text("TLS SHA-256 Pin") },
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
                                capabilities = listOf("anti_theft", "wakeword", "whatsapp", "toggles", "accessibility_vision", "sos", "camerax", "screen_remote", "hud_confirm", "ptt_voice"),
                                certSha256 = certPin
                            )
                        },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("Pair & Connect (HTTPS Pinned)") }
                }
            }

            // (a) Live PC Screen Viewer + Remote Tap / Type
            Card(colors = CardDefaults.cardColors(containerColor = Color(0xFF0E1621))) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text("LIVE PC SCREEN + TAP/TYPE", color = Color(0xFF00D4FF), fontWeight = FontWeight.Bold, fontSize = 12.sp)
                        OutlinedButton(onClick = { screenStreaming = !screenStreaming }) {
                            Text(if (screenStreaming) "Stop Stream" else "Live Stream")
                        }
                    }
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(190.dp)
                            .background(Color(0xFF03060B), RoundedCornerShape(8.dp))
                            .border(1.dp, Color(0xFF1A3A4A), RoundedCornerShape(8.dp)),
                        contentAlignment = Alignment.Center
                    ) {
                        val bmp = screenBitmap
                        if (bmp != null) {
                            Image(
                                bitmap = bmp,
                                contentDescription = "Live PC Screen",
                                contentScale = ContentScale.FillBounds,
                                modifier = Modifier
                                    .fillMaxSize()
                                    .pointerInput(Unit) {
                                        detectTapGestures(
                                            onTap = { offset ->
                                                if (size.width > 0 && size.height > 0) {
                                                    wsClient.sendScreenTap(offset.x / size.width, offset.y / size.height, "tap")
                                                }
                                            },
                                            onDoubleTap = { offset ->
                                                if (size.width > 0 && size.height > 0) {
                                                    wsClient.sendScreenTap(offset.x / size.width, offset.y / size.height, "double_click")
                                                }
                                            }
                                        )
                                    }
                            )
                        } else {
                            Text("Tap 'Live Stream' when connected to view & tap PC", fontSize = 12.sp, color = Color(0xFF7291A6))
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        OutlinedTextField(
                            value = remoteTypeText,
                            onValueChange = { remoteTypeText = it },
                            label = { Text("Type onto PC window") },
                            modifier = Modifier.weight(1f)
                        )
                        Button(onClick = {
                            if (remoteTypeText.isNotEmpty()) {
                                wsClient.sendScreenText(remoteTypeText)
                                remoteTypeText = ""
                            }
                        }) { Text("Type") }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.fillMaxWidth()) {
                        OutlinedButton(onClick = { wsClient.sendScreenKey("enter") }, modifier = Modifier.weight(1f)) { Text("Enter") }
                        OutlinedButton(onClick = { wsClient.sendScreenKey("escape") }, modifier = Modifier.weight(1f)) { Text("ESC") }
                        OutlinedButton(onClick = { wsClient.sendScreenKey("tab") }, modifier = Modifier.weight(1f)) { Text("Tab") }
                    }
                }
            }

            // (b) Push-to-Talk Voice + Mission Command
            Card(colors = CardDefaults.cardColors(containerColor = Color(0xFF0E1621))) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("PUSH-TO-TALK VOICE & COMMAND", color = Color(0xFF00D4FF), fontWeight = FontWeight.Bold, fontSize = 12.sp)
                    Button(
                        onClick = {
                            pttStreaming = !pttStreaming
                            togglePushToTalk(pttStreaming, scope)
                        },
                        colors = ButtonDefaults.buttonColors(
                            containerColor = if (pttStreaming) Color(0xFFFF3355) else Color(0xFF00D4FF)
                        ),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text(
                            if (pttStreaming) "🔴 STOP LIVE MIC STREAM" else "🎙 PUSH-TO-TALK (16kHz LIVE MIC)",
                            color = if (pttStreaming) Color.White else Color(0xFF041017),
                            fontWeight = FontWeight.Bold
                        )
                    }
                    OutlinedTextField(
                        value = cmdText,
                        onValueChange = { cmdText = it },
                        label = { Text("Command or Multi-Agent Swarm Goal") },
                        modifier = Modifier.fillMaxWidth()
                    )
                    Button(
                        onClick = {
                            if (cmdText.isNotBlank()) {
                                wsClient.sendCommandText(cmdText)
                                appendMissionLog("YOU: $cmdText")
                                cmdText = ""
                            }
                        },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("Send Encrypted Command") }
                }
            }

            // 7/7 MAX Modules Card
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

            // Live Mission Log Stream
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .border(1.dp, Color(0xFF1A3A4A), RoundedCornerShape(10.dp))
                    .padding(10.dp)
            ) {
                Text("LIVE MISSION & SWARM STREAM", color = Color(0xFF00D4FF), fontSize = 11.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(4.dp))
                missionLogsState.take(12).forEach { item ->
                    Text(item, fontFamily = FontFamily.Monospace, fontSize = 11.sp, color = Color(0xFF9DE8FF))
                }
            }

            // Telemetry Log
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .border(1.dp, Color(0xFF1A3A4A), RoundedCornerShape(10.dp))
                    .padding(10.dp)
            ) {
                Text("TELEMETRY LOG", color = Color(0xFF5AB8CC), fontSize = 11.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(4.dp))
                logsState.take(8).forEach { item ->
                    Text(item, fontFamily = FontFamily.Monospace, fontSize = 11.sp, color = Color(0xFFBCEFFF))
                }
            }
        }
    }

    override fun onDestroy() {
        pttJob?.cancel()
        wsClient.closePhoneAudioSocket()
        tts?.shutdown()
        super.onDestroy()
    }
}
