# Windows 应用图标

`app-icon-original.png` 保留用户提供的原图。`app-icon.png` 是正式图标，外围保留透明留白，主体约占画布的 80%；更换图案时不要将主体放大到铺满画布。

`app-icon-256.png` 用于 Tk 窗口；`../src-tauri/icons/icon.ico` 用于 Windows 宿主与安装程序。该目录中的 PNG 是不同尺寸的应用图标资源。

当前正式图标采用用户确认的白色版本：屏幕、背景和椅子改为白色，保留人物、中文文字和彩色图表；`app-icon-original.png` 继续保留最初的深蓝底参考图。

更新图标时按完整画布缩放 PNG，同步 ICO 的各尺寸并保留透明通道；将 256 像素 PNG 同步为 `app-icon-256.png`、`256x256.png` 和 `128x128@2x.png`。图标转换与安装包构建分别执行。
