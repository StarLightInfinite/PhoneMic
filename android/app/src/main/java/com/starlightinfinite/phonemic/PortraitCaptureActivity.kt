package com.starlightinfinite.phonemic

import com.journeyapps.barcodescanner.CaptureActivity

/** 竖屏扫码界面。
 *
 * ZXing 默认把扫描界面锁到「设备当前方向」（横着拿手机就是横屏），与 PhoneMic
 * 的移动端竖屏交互不符。这里继承库的 CaptureActivity，只在 manifest 里把方向
 * 声明为 portrait，并通过 IntentIntegrator.setOrientationLocked(false) 让
 * manifest 的 screenOrientation 生效——两者缺一不可：
 *   · 子类 + manifest portrait：告诉系统这个 Activity 只能竖屏；
 *   · setOrientationLocked(false)：阻止库按当前设备方向再覆盖回横屏。
 */
class PortraitCaptureActivity : CaptureActivity()
