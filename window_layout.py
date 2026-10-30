"""Window bounds in the same logical coordinates that Tk uses on Windows."""
import os


def work_area(root):
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    fallback = (0, 0, screen_w, max(1, screen_h - 48))
    if os.name != "nt":
        return fallback
    try:
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return fallback
        # Some Windows/Tk combinations report the work area in physical pixels.
        # Normalize against Tk's logical primary-screen size before computing geometry.
        scale = max(1, rect.right / max(screen_w, 1), rect.bottom / max(screen_h, 1))
        return tuple(round(value / scale) for value in (rect.left, rect.top, rect.right, rect.bottom))
    except (OSError, AttributeError):
        return fallback


def initial_geometry(area):
    left, top, right, bottom = area
    work_w, work_h = right - left, bottom - top
    # These are client dimensions: explicitly reserve title bar, borders, and breathing room.
    width = max(200, min(1200, round(work_w * .88), work_w - 32))
    height = max(180, min(680, round(work_h * .80), work_h - 76))
    x = left + max(12, (work_w - width - 16) // 2)
    y = top + max(12, (work_h - height - 44) // 2)
    return width, height, x, y


def place_initial(root):
    width, height, x, y = initial_geometry(work_area(root))
    root.minsize(min(860, width), min(440, height))
    root.geometry(f"{width}x{height}+{x}+{y}")
    return width, height
