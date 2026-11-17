package com.starlightinfinite.phonemic

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.google.zxing.integration.android.IntentIntegrator
import com.google.zxing.integration.android.IntentResult

/** 安卓壳：只负责找到 PC 入口地址并全屏展示现有 mobile.html。
 *
 * 页面内容 100% 来自 PC 端 HTTP 服务，样式/交互改动不需要重装 APK；
 * 只有本壳的发现逻辑、文件选择器等原生能力变更才需要更新 APK。
 */
class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private lateinit var statusText: TextView
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    companion object {
        private const val REQ_FILE = 1001
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        webView = findViewById(R.id.webview)
        statusText = findViewById(R.id.status)
        setupWebView()
        connect()
    }

    private fun setupWebView() {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true          // mobile.html 的配置/公钥缓存
            allowFileAccess = true
            allowContentAccess = true
            loadWithOverviewMode = true
            useWideViewPort = true
            setSupportZoom(false)
            cacheMode = WebSettings.LOAD_NO_CACHE   // 每次都拉 PC 上最新页面
        }
        // 始终在当前 WebView 内导航，不跳出到系统浏览器
        webView.webViewClient = WebViewClient()
        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView?,
                callback: ValueCallback<Array<Uri>>?,
                params: FileChooserParams?,
            ): Boolean {
                fileCallback?.onReceiveValue(null)   // 上一次没结算就先清掉
                fileCallback = callback
                val intent = Intent(Intent.ACTION_GET_CONTENT).apply {
                    type = "*/*"
                    addCategory(Intent.CATEGORY_OPENABLE)
                    if (params?.isMultiple == true) {
                        putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                    }
                }
                startActivityForResult(
                    Intent.createChooser(intent, getString(R.string.choose_file)), REQ_FILE,
                )
                return true
            }
        }
    }

    /** 启动/重连：先 WLAN 自动发现，失败退缓存，再退扫一扫。 */
    private fun connect() {
        statusText.visibility = View.VISIBLE
        statusText.text = getString(R.string.status_discovering)
        Discovery.discover(this) { found ->
            runOnUiThread {
                val url = found ?: UrlStore.get(this)
                if (url.isNullOrBlank()) startScan() else load(url)
            }
        }
    }

    private fun load(url: String) {
        UrlStore.save(this, url)
        statusText.visibility = View.GONE
        webView.loadUrl(url)
    }

    /** 外网 Cloudflare / 发现失败时用：扫 PC 主界面的二维码。 */
    private fun startScan() {
        IntentIntegrator(this).apply {
            setDesiredBarcodeFormats(IntentIntegrator.QR_CODE)
            setPrompt(getString(R.string.scan_prompt))
            setBeepEnabled(true)
            setBarcodeImageEnabled(false)
            initiateScan()
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == IntentIntegrator.REQUEST_CODE) {
            val result: IntentResult? =
                IntentIntegrator.parseActivityResult(requestCode, resultCode, data)
            val contents = result?.contents
            if (!contents.isNullOrBlank() &&
                (contents.startsWith("http://") || contents.startsWith("https://"))
            ) {
                load(contents)
            } else {
                val cached = UrlStore.get(this)
                if (!cached.isNullOrBlank()) {
                    load(cached)
                } else if (!contents.isNullOrBlank()) {
                    Toast.makeText(this, R.string.invalid_qr, Toast.LENGTH_SHORT).show()
                    connect()
                } else {
                    finish()
                }
            }
            return
        }
        if (requestCode == REQ_FILE) {
            val uris: Array<Uri>? = if (resultCode == RESULT_OK && data != null) {
                data.clipData?.let { clip ->
                    (0 until clip.itemCount).map { clip.getItemAt(it).uri }.toTypedArray()
                } ?: data.data?.let { arrayOf(it) }
            } else {
                null
            }
            fileCallback?.onReceiveValue(uris)
            fileCallback = null
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
            return
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.menu_title)
            .setItems(
                arrayOf(getString(R.string.menu_rescan), getString(R.string.menu_exit)),
            ) { _, which ->
                if (which == 0) startScan() else finish()
            }
            .show()
    }
}
