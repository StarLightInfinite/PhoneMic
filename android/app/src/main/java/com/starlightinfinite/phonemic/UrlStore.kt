package com.starlightinfinite.phonemic

import android.content.Context

/** 最近一次成功连接的入口 URL 缓存。
 *
 * URL 里带 secret 路径与公钥 fragment，等价于一张「电子二维码」；存在应用
 * 私有 SharedPreferences 里，其他应用读不到（未 root 的设备）。它只是离线
 * 兜底：每次启动仍先走局域网发现，拿到新地址就覆盖旧值。
 */
object UrlStore {
    private const val PREFS = "phonemic"
    private const val KEY_URL = "entry_url"

    fun get(context: Context): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_URL, null)

    fun save(context: Context, url: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_URL, url)
            .apply()
    }
}
