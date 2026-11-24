package com.marklvii.jarvis

import com.marklvii.jarvis.core.AndroidEvent
import com.marklvii.jarvis.core.AndroidResult
import com.marklvii.jarvis.core.CryptoEnvelope
import com.marklvii.jarvis.core.DeviceRegister
import com.marklvii.jarvis.core.PcToAndroidCommand
import com.marklvii.jarvis.core.PrivacyState
import com.marklvii.jarvis.core.ToolInvoke
import com.marklvii.jarvis.modules.antitheft.MotionTamperDetector
import com.marklvii.jarvis.modules.wakeword.WakePhraseMatcher
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import javax.crypto.AEADBadTagException

class MaxModulesUnitTest {

    @Test
    fun wakePhraseMatcherHandlesEnglishHindiAndHinglish() {
        val en = WakePhraseMatcher.match("Hey Jarvis turn on flashlight")
        assertTrue(en.matched)
        assertEquals("en", en.languageHint)
        assertEquals("turn on flashlight", en.trailingCommand)

        val hinglish = WakePhraseMatcher.match("Bolo Jarvis read unread whatsapp")
        assertTrue(hinglish.matched)
        assertEquals("hinglish", hinglish.languageHint)
        assertEquals("read unread whatsapp", hinglish.trailingCommand)

        val hindi = WakePhraseMatcher.match("हे जार्विस बैटरी बताओ")
        assertTrue(hindi.matched)
        assertEquals("hi", hindi.languageHint)

        assertFalse(WakePhraseMatcher.match("just background conversation").matched)
    }

    @Test
    fun motionTamperDetectorTriggersOnSustainedDeltaAndRespectsCooldown() {
        val det = MotionTamperDetector(deltaThresholdMs2 = 4.0f, requiredHits = 2, cooldownMs = 5_000L)
        // Baseline ~ 9.81
        assertFalse(det.evaluate(0.0f, 9.81f, 0.0f, 1_000L))
        assertFalse(det.evaluate(0.1f, 9.80f, 0.0f, 1_100L))
        // Sudden movement hits 1 then 2
        assertFalse(det.evaluate(8.0f, 14.0f, 6.0f, 2_000L))
        assertTrue(det.evaluate(9.0f, 15.0f, 7.0f, 2_200L))
        // Still inside 5s cooldown -> suppressed
        assertFalse(det.evaluate(9.0f, 15.0f, 7.0f, 3_000L))
        assertFalse(det.evaluate(9.0f, 15.0f, 7.0f, 3_200L))
    }

    @Test
    fun privacyStateBlocksOutboundWhenEnabled() {
        val p = PrivacyState(initialOffline = false)
        assertTrue(p.isOutboundSyncAllowed())
        p.onDeviceOnly = true
        assertFalse(p.isOutboundSyncAllowed())
        var blocked = false
        try {
            p.assertOutboundAllowed("ws_send")
        } catch (_: IllegalStateException) {
            blocked = true
        }
        assertTrue(blocked)
    }

    @Test
    fun protocolModelsAndCryptoEnvelopeRoundTrip() {
        val reg = DeviceRegister(
            deviceId = "pixel-12",
            name = "Pixel",
            model = "Pixel 9",
            androidVersion = "15",
            capabilities = listOf("sos", "camerax"),
            battery = 88,
            charging = true
        ).toJson()
        assertEquals("device_register", reg.getString("type"))
        assertEquals("pixel-12", reg.getString("device_id"))

        val cmd = PcToAndroidCommand.fromJson(
            JSONObject("""{"type":"pc_to_android","request_id":"r1","action":"toggle","params":{"target":"flashlight"}}""")
        )
        assertEquals("r1", cmd.requestId)
        assertEquals("flashlight", cmd.params.getString("target"))

        val resJson = AndroidResult("r1", true, JSONObject().put("flashlight", true)).toJson()
        assertEquals("android_result", resJson.getString("type"))
        val evJson = AndroidEvent("sos_triggered", JSONObject().put("lat", 19.07)).toJson()
        assertEquals("android_event", evJson.getString("type"))
        val invokeJson = ToolInvoke("req2", "computer_settings", JSONObject().put("action", "volume_up")).toJson()
        assertEquals("tool_invoke", invokeJson.getString("type"))

        val key = CryptoEnvelope.deriveKey("TESTPIN9")
        val cipher = CryptoEnvelope.encrypt(key, "hello mark lvii")
        assertEquals("hello mark lvii", CryptoEnvelope.decrypt(key, cipher))

        val tampered = cipher.dropLast(2) + "AA"
        var badTag = false
        try {
            CryptoEnvelope.decrypt(key, tampered)
        } catch (_: AEADBadTagException) {
            badTag = true
        } catch (_: Exception) {
            badTag = true
        }
        assertTrue(badTag)
    }
}
