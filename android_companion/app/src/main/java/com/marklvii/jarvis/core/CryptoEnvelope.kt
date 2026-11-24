package com.marklvii.jarvis.core

import java.nio.charset.StandardCharsets
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec

/**
 * PBKDF2-HMAC-SHA256 (200,000 rounds) + AES-256-GCM wire-compatible with
 * Mark-LVII dashboard/server.py (`nonce[12] || ciphertext || gcmTag[16]`).
 */
object CryptoEnvelope {
    private const val SALT = "JARVIS-DASHBOARD-v1"
    private const val ITERATIONS = 200_000
    private const val KEY_BITS = 256
    private const val NONCE_LEN = 12
    private const val TAG_BITS = 128

    private val rng = SecureRandom()

    fun deriveKey(sessionKey: String): ByteArray {
        val factory = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
        val spec = PBEKeySpec(
            sessionKey.toCharArray(),
            SALT.toByteArray(StandardCharsets.UTF_8),
            ITERATIONS,
            KEY_BITS
        )
        return factory.generateSecret(spec).encoded
    }

    fun encrypt(aesKey: ByteArray, plaintext: String): String {
        val nonce = ByteArray(NONCE_LEN)
        rng.nextBytes(nonce)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.ENCRYPT_MODE,
            SecretKeySpec(aesKey, "AES"),
            GCMParameterSpec(TAG_BITS, nonce)
        )
        val ct = cipher.doFinal(plaintext.toByteArray(StandardCharsets.UTF_8))
        val payload = ByteArray(NONCE_LEN + ct.size)
        System.arraycopy(nonce, 0, payload, 0, NONCE_LEN)
        System.arraycopy(ct, 0, payload, NONCE_LEN, ct.size)
        return Base64.getEncoder().encodeToString(payload)
    }

    fun decrypt(aesKey: ByteArray, encB64: String): String {
        val raw = Base64.getDecoder().decode(encB64)
        require(raw.size > NONCE_LEN) { "Ciphertext shorter than nonce" }
        val nonce = raw.copyOfRange(0, NONCE_LEN)
        val ct = raw.copyOfRange(NONCE_LEN, raw.size)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            SecretKeySpec(aesKey, "AES"),
            GCMParameterSpec(TAG_BITS, nonce)
        )
        return String(cipher.doFinal(ct), StandardCharsets.UTF_8)
    }
}
