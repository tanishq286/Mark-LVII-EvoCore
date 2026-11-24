package com.marklvii.jarvis.modules.antitheft

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class ChargerDisconnectReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_POWER_DISCONNECTED && AntiTheftGuardService.isArmed) {
            val trigger = Intent(context, AntiTheftGuardService::class.java).apply {
                action = AntiTheftGuardService.ACTION_CHARGER_TAMPER
            }
            context.startForegroundService(trigger)
        }
    }
}
