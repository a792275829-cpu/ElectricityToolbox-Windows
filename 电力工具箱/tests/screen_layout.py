"""Skip fixed-size GUI assertions when the display cannot show the window.

Tk can clamp windows to smaller displays. Skip fixed-size layout assertions
when that screen cannot fit the requested dimensions.
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
