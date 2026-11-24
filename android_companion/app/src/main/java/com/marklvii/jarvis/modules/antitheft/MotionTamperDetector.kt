package com.marklvii.jarvis.modules.antitheft

import kotlin.math.abs
import kotlin.math.sqrt

/**
 * Pure JVM-testable accelerometer motion & tilt tamper detector for AntiTheftGuardService.
 */
class MotionTamperDetector(
    private val deltaThresholdMs2: Float = 4.2f,
    private val requiredHits: Int = 2,
    private val cooldownMs: Long = 5_000L
) {
    private var baselineMag: Float? = null
    private var consecutiveHits: Int = 0
    private var lastTriggerMs: Long = -cooldownMs

    fun reset() {
        baselineMag = null
        consecutiveHits = 0
    }

    fun evaluate(x: Float, y: Float, z: Float, timestampMs: Long): Boolean {
        val mag = sqrt(x * x + y * y + z * z)
        val base = baselineMag
        if (base == null) {
            baselineMag = mag
            return false
        }
        val delta = abs(mag - base)
        if (delta >= deltaThresholdMs2) {
            consecutiveHits += 1
        } else {
            consecutiveHits = 0
            // Exponentially adapt baseline slightly during still periods
            baselineMag = base * 0.9f + mag * 0.1f
        }
        if (consecutiveHits >= requiredHits && (timestampMs - lastTriggerMs) >= cooldownMs) {
            lastTriggerMs = timestampMs
            consecutiveHits = 0
            return true
        }
        return false
    }
}
