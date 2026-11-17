package com.starlightinfinite.phonemic

import android.content.Context
import android.net.wifi.WifiManager
import android.util.Log
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.SocketTimeoutException

/** WLAN 内发现 PC 当前入口 URL。协议见 phonemic/server/discovery.py。 */
object Discovery {
    private const val TAG = "PhoneMic.Discovery"
    private const val PORT = 12001
    private val MAGIC = "PHONEMIC-DISCOVER-1\n".toByteArray(Charsets.US_ASCII)
    private const val ATTEMPTS = 4
    private const val TIMEOUT_MS = 600

    /**
     * 异步探测，回调在非 UI 线程执行；调用方负责切回主线程。
     * 任一环节失败都只回 null（用缓存地址兜底），不抛异常给界面。
     */
    fun discover(context: Context, callback: (String?) -> Unit) {
        Thread {
            var found: String? = null
            val socket = DatagramSocket()
            var lock: WifiManager.MulticastLock? = null
            try {
                socket.broadcast = true
                socket.soTimeout = TIMEOUT_MS
                val wifi = context.applicationContext
                    .getSystemService(Context.WIFI_SERVICE) as? WifiManager
                lock = wifi?.createMulticastLock("phonemic-discovery")?.also { it.acquire() }

                val request = DatagramPacket(
                    MAGIC, MAGIC.size,
                    InetAddress.getByName("255.255.255.255"), PORT,
                )
                for (i in 0 until ATTEMPTS) {
                    socket.send(request)
                    val buffer = ByteArray(4096)
                    val reply = DatagramPacket(buffer, buffer.size)
                    try {
                        socket.receive(reply)
                        val json = JSONObject(
                            String(reply.data, 0, reply.length, Charsets.UTF_8)
                        )
                        if (json.optInt("v") == 1) {
                            val url = json.optString("url")
                            if (url.startsWith("http://") || url.startsWith("https://")) {
                                found = url
                                break
                            }
                        }
                    } catch (_: SocketTimeoutException) {
                        // 本轮没应答，重发下一次
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "discovery failed", e)
            } finally {
                lock?.release()
                socket.close()
            }
            callback(found)
        }.start()
    }
}
