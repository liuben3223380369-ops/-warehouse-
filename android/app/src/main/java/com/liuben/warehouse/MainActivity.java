package com.liuben.warehouse;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.ViewGroup;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

/**
 * 仓库管理系统 Android 外壳。
 *
 * 思路：Python 侧（android_entry.py）在后台线程起一个只监听 127.0.0.1 的
 * Flask 服务，这里用系统 WebView 打开它。所有业务代码仍是同一套 Python，
 * 不重写成 Java，也不用联网。
 */
public class MainActivity extends Activity {

    private static final String TAG = "Warehouse";
    private WebView webView;
    private String homeUrl = null;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 1) 启动 Python 解释器（ChaQuopy）
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(this));
        }

        // 2) 起 Flask，拿到端口。放在 try 里，失败也要能看到原因
        int port = 0;
        try {
            PyObject entry = Python.getInstance().getModule("android_entry");
            String filesDir = getFilesDir().getAbsolutePath();
            port = entry.callAttr("start_server", filesDir).toJava(Integer.class);
        } catch (Exception e) {
            Log.e(TAG, "启动 Python 服务失败", e);
            Toast.makeText(this, "启动失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
        }

        // 3) WebView 显示
        webView = new WebView(this);
        setContentView(webView);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        // 手机屏幕小，用一个合适的初始缩放
        s.setTextZoom(100);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        }

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, String url) {
                // 本机服务自己处理；外部链接交给浏览器
                if (url != null && (url.startsWith("http://127.0.0.1")
                        || url.startsWith("http://localhost"))) {
                    return false;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                } catch (Exception ignored) {
                }
                return true;
            }

            @Override
            public void onReceivedError(WebView v, int errorCode,
                                       String description, String failingUrl) {
                // 服务可能还没起来，稍等一下重试一次
                if (homeUrl != null && failingUrl != null && failingUrl.equals(homeUrl)) {
                    v.postDelayed(new Runnable() {
                        @Override
                        public void run() {
                            v.loadUrl(homeUrl);
                        }
                    }, 800);
                }
            }
        });
        webView.setWebChromeClient(new WebChromeClient());

        if (port > 0) {
            homeUrl = "http://127.0.0.1:" + port + "/";
            webView.loadUrl(homeUrl);
        } else {
            webView.loadData("<h3>服务启动失败</h3><p>请重启应用；若反复失败，" +
                    "看 logcat 中 Warehouse 标签的报错。</p>", "text/html; charset=utf-8", null);
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            ViewGroup vg = (ViewGroup) webView.getParent();
            if (vg != null) {
                vg.removeView(webView);
            }
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
