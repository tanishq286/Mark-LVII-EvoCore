package com.marklvii.jarvis.modules.vision

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * 5/7 Android MAX: Accessibility Auto-Scroll (Reels/Shorts/Articles),
 * Screen Text Reader, Label-based Auto-Tap, Auto-Type, & Text Extraction.
 */
class ScreenVisionScrollService : AccessibilityService() {
    companion object {
        @Volatile
        var instance: ScreenVisionScrollService? = null
            private set
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        if (instance === this) instance = null
        return super.onUnbind(intent)
    }

    fun performScroll(direction: String = "down"): Boolean {
        val dm = resources.displayMetrics
        val x = dm.widthPixels * 0.5f
        val startY = if (direction == "up") dm.heightPixels * 0.30f else dm.heightPixels * 0.78f
        val endY = if (direction == "up") dm.heightPixels * 0.78f else dm.heightPixels * 0.24f
        val path = Path().apply {
            moveTo(x, startY)
            lineTo(x, endY)
        }
        val stroke = GestureDescription.StrokeDescription(path, 0L, 260L)
        return dispatchGesture(GestureDescription.Builder().addStroke(stroke).build(), null, null)
    }

    fun collectVisibleText(): String {
        val root = rootInActiveWindow ?: return ""
        val sb = StringBuilder()
        fun walk(node: AccessibilityNodeInfo?) {
            if (node == null) return
            val txt = node.text?.toString()?.trim()
            if (!txt.isNullOrEmpty()) sb.append(txt).append('\n')
            for (i in 0 until node.childCount) walk(node.getChild(i))
        }
        walk(root)
        return sb.toString().trim()
    }

    fun readScreenAloud(tts: TextToSpeech?): String {
        val text = collectVisibleText().take(900)
        if (text.isNotEmpty()) tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "screen_read")
        return text
    }

    fun autoTapByLabel(label: String): Boolean {
        val root = rootInActiveWindow ?: return false
        val wanted = label.trim().lowercase()
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.addLast(root)
        while (stack.isNotEmpty()) {
            val node = stack.removeFirst()
            val text = node.text?.toString()?.lowercase().orEmpty()
            val desc = node.contentDescription?.toString()?.lowercase().orEmpty()
            if ((text.contains(wanted) || desc.contains(wanted)) && node.isClickable) {
                return node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            }
            for (i in 0 until node.childCount) {
                node.getChild(i)?.let { stack.addLast(it) }
            }
        }
        return false
    }

    fun autoTypeFocused(text: String): Boolean {
        val root = rootInActiveWindow ?: return false
        val target = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: return false
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        return target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }
}
