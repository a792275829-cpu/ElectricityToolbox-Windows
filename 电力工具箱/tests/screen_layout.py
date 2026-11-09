"""Skip fixed-size GUI assertions on displays that cannot show the window.

GitHub's macOS runners ship with different virtual screen sizes (macos-15
uses 1024x768).  Tk clamps the window to the screen there, so assertions
about the minimum window layout would fail for an environment reason
rather than a layout regression.
"""
from __future__ import annotations

from toolbox.app import MIN_WINDOW_HEIGHT, MIN_WINDOW_WIDTH, SCREEN_EDGE_MARGIN


def skip_if_window_does_not_fit(
    case,
    app,
    width: int = MIN_WINDOW_WIDTH,
    height: int = MIN_WINDOW_HEIGHT,
) -> None:
    scale = float(getattr(app, "window_scale", 1.0) or 1.0)
    needed_width = int(width * scale)
    needed_height = int(height * scale)
    edge = int(SCREEN_EDGE_MARGIN * scale)
    screen_width = app.winfo_screenwidth() - edge
    screen_height = app.winfo_screenheight() - edge
    if screen_width < needed_width or screen_height < needed_height:
        case.skipTest(
            f"屏幕 {app.winfo_screenwidth()}x{app.winfo_screenheight()} "
            f"放不下 {needed_width}x{needed_height} 的窗口（缩放 {scale:.2f}），跳过固定尺寸布局断言"
        )
