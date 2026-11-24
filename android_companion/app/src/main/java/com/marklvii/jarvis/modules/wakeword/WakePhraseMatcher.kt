package com.marklvii.jarvis.modules.wakeword

/**
 * Bilingual / multilingual wake-phrase recognizer & trailing intent extractor
 * supporting English, Hindi (Devanagari), and Hinglish phrases.
 */
object WakePhraseMatcher {
    private val WAKE_PREFIXES = listOf(
        "hey jarvis",
        "ok jarvis",
        "hi jarvis",
        "bolo jarvis",
        "suno jarvis",
        "jarvis suno",
        "हे जार्विस",
        "बोलो जार्विस",
        "सुनो जार्विस",
        "जार्विस"
    )

    data class WakeMatch(
        val matched: Boolean,
        val wakePhrase: String = "",
        val trailingCommand: String = "",
        val languageHint: String = "en"
    )

    fun match(transcript: String): WakeMatch {
        val trimmed = transcript.trim()
        if (trimmed.isEmpty()) return WakeMatch(false)
        val lower = trimmed.lowercase().replace(Regex("[,!?.।]+"), " ").replace(Regex("\\s+"), " ").trim()

        for (prefix in WAKE_PREFIXES) {
            val idx = lower.indexOf(prefix)
            if (idx >= 0) {
                val after = lower.substring(idx + prefix.length).trim()
                val lang = when {
                    prefix.any { it in '\u0900'..'\u097F' } -> "hi"
                    prefix.startsWith("bolo") || prefix.startsWith("suno") || prefix.endsWith("suno") -> "hinglish"
                    else -> "en"
                }
                return WakeMatch(
                    matched = true,
                    wakePhrase = prefix,
                    trailingCommand = after,
                    languageHint = lang
                )
            }
        }
        return WakeMatch(false)
    }
}
