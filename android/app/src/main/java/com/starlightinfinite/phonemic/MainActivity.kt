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
    private lateinit var connectGuide: View
    private lateinit var statusText: TextView
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    companion object {
        private const val REQ_FILE = 1001
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        webView = findViewById(R.id.webview)
        connectGuide = findViewById(R.id.connect_guide)
        statusText = findViewById(R.id.status)
        // 扫码改为按需：只有用户主动点按钮才拉起，不再自动弹扫码界面。
        findViewById<View>(R.id.btn_scan).setOnClickListener { startScan() }
        findViewById<View>(R.id.btn_retry).setOnClickListener { connect() }
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
                    // 用 mode 判断而非 isMultiple 属性：后者在部分 Kotlin/AGP 组合下
                    // 解析不到 Java 的 isMultiple()，会导致 compileDebugKotlin 失败。
                    if (params?.mode == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE) {
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

    /** 启动/重连：先 WLAN 自动发现，失败退缓存；两者都没有才显示扫码引导。
     * 关键：不再自动弹扫码——扫一扫由用户按需主动触发。 */
    private fun connect() {
        showGuide(R.string.status_discovering)
        Discovery.discover(this) { found ->
            runOnUiThread {
                val url = found ?: UrlStore.get(this)
                if (url.isNullOrBlank()) {
                    // 发现失败且无缓存：停在引导页，等用户自己决定何时扫码
                    showGuide(R.string.status_not_found)
                } else {
                    load(url)
                }
            }
        }
    }

    /** 未连接时的引导页：文案随状态更新，扫码/重试按钮始终可见。 */
    private fun showGuide(statusRes: Int) {
        statusText.text = getString(statusRes)
        connectGuide.visibility = View.VISIBLE
    }

    private fun load(url: String) {
        UrlStore.save(this, url)
        connectGuide.visibility = View.GONE
        webView.loadUrl(url)
    }

    /** 外网 Cloudflare / 发现失败时用：扫 PC 主界面的二维码。
     * 强制竖屏：指定本 App 的竖屏 Activity，并关闭库的「按当前设备方向锁定」。 */
    private fun startScan() {
        IntentIntegrator(this).apply {
            setCaptureActivity(PortraitCaptureActivity::class.java)
            setOrientationLocked(false)
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
                    showGuide(R.string.status_not_found)
                } else {
                    // 用户主动取消扫码：回到引导页，不再直接退出应用
                    showGuide(R.string.status_not_found)
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
