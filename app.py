"""
Acer PopGo Companion — battery, DPI, and mouse info.

Run:
  python app.py
"""
from __future__ import annotations

import json
import platform
import sys
import threading
from pathlib import Path
from typing import Optional

import customtkinter as ctk

from mouse_device import DPI_LEVELS, BATTERY_CAPACITY_MAH, MouseStatus, PopGoMouse, StatusPoller

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform == "darwin"

try:
    import winreg
except ImportError:
    winreg = None  # type: ignore

try:
    import pystray
    from PIL import Image, ImageDraw

    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False


APP_NAME = "Acer PopGo Companion"
APP_VERSION = "1.1.0"

# Fixed window — sized so every control is visible without resizing
WINDOW_W = 480
WINDOW_H = 700

ACER_GREEN = "#83B81A"
ACER_DARK = "#1A1A1A"
CARD_BG = "#242424"
MUTED = "#9A9A9A"


def app_base_dir() -> Path:
    """Directory for config next to the script or frozen executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


CONFIG_PATH = app_base_dir() / "config.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"dpi_index": None, "poll_seconds": 2.0, "start_minimized": False}


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


def windows_mouse_speed() -> tuple[Optional[int], Optional[bool]]:
    if winreg is None:
        return None, None
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Mouse")
        speed_s, _ = winreg.QueryValueEx(key, "MouseSensitivity")
        try:
            accel, _ = winreg.QueryValueEx(key, "MouseSpeed")
            enhanced = str(accel) not in ("0", "")
        except OSError:
            enhanced = None
        winreg.CloseKey(key)
        return int(speed_s), enhanced
    except OSError:
        return None, None


def set_windows_mouse_speed(speed: int) -> bool:
    if winreg is None:
        return False
    speed = max(1, min(20, int(speed)))
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\Mouse",
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "MouseSensitivity", 0, winreg.REG_SZ, str(speed))
        winreg.CloseKey(key)
        try:
            import ctypes

            ctypes.windll.user32.SystemParametersInfoW(
                0x0071, 0, speed, 0x01 | 0x02
            )
        except Exception:
            pass
        return True
    except OSError:
        return False


def battery_color(percent: Optional[int]) -> str:
    if percent is None:
        return MUTED
    if percent <= 10:
        return "#E74C3C"
    if percent <= 20:
        return "#E67E22"
    if percent <= 50:
        return "#F1C40F"
    return ACER_GREEN


def make_tray_image(percent: Optional[int]) -> "Image.Image":
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = battery_color(percent)
    draw.rounded_rectangle((10, 18, 48, 46), radius=6, outline=color, width=3)
    draw.rectangle((48, 26, 54, 38), fill=color)
    if percent is not None:
        fill_w = int(30 * max(0, min(100, percent)) / 100)
        if fill_w > 0:
            draw.rounded_rectangle((14, 22, 14 + fill_w, 42), radius=3, fill=color)
    return img


def center_window(win: ctk.CTk, width: int, height: int) -> None:
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = max(0, (sw - width) // 2)
    y = max(0, (sh - height) // 2)
    win.geometry(f"{width}x{height}+{x}+{y}")


class PopGoApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME}  v{APP_VERSION}")
        self.configure(fg_color=ACER_DARK)

        # Fixed size + fixed corners (not user-resizable)
        self.resizable(False, False)
        try:
            self.minsize(WINDOW_W, WINDOW_H)
            self.maxsize(WINDOW_W, WINDOW_H)
        except Exception:
            pass
        center_window(self, WINDOW_W, WINDOW_H)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        self.cfg = load_config()
        self.mouse = PopGoMouse()
        if self.cfg.get("dpi_index") is not None:
            try:
                self.mouse.set_tracked_dpi_index(int(self.cfg["dpi_index"]))
            except (TypeError, ValueError):
                pass

        self._tray = None
        self._tray_thread: Optional[threading.Thread] = None
        self._closing = False
        self._last_low_notify_pct: Optional[int] = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        interval = float(self.cfg.get("poll_seconds", 2.0))
        self.poller = StatusPoller(self.mouse, self._on_status, interval=interval)
        self.poller.start()

        self.after(100, lambda: self._on_status(self.mouse.refresh()))
        # Re-assert geometry after first layout pass
        self.after(50, lambda: center_window(self, WINDOW_W, WINDOW_H))

        if HAS_TRAY:
            self.after(300, self._start_tray)

    # ------------------------------------------------------------------- UI
    def _card(self, parent: ctk.CTkBaseClass, **pack_kw) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=10)
        defaults = {"fill": "x", "padx": 14, "pady": 5}
        defaults.update(pack_kw)
        f.pack(**defaults)
        return f

    def _section(self, parent: ctk.CTkBaseClass, title: str) -> None:
        ctk.CTkLabel(
            parent,
            text=title,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=MUTED,
        ).pack(anchor="w", padx=12, pady=(10, 0))

    def _build_ui(self) -> None:
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(
            header,
            text="Acer PopGo",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="white",
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text=f"Companion  ·  v{APP_VERSION}  ·  battery & DPI",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        ).pack(anchor="w")

        # Connection
        conn = self._card(self, pady=(6, 5))
        self.conn_dot = ctk.CTkLabel(
            conn, text="●", font=ctk.CTkFont(size=12), text_color="#E74C3C", width=16
        )
        self.conn_dot.pack(side="left", padx=(10, 4), pady=8)
        self.conn_label = ctk.CTkLabel(
            conn,
            text="Searching for mouse…",
            font=ctk.CTkFont(size=12),
            text_color="white",
            anchor="w",
        )
        self.conn_label.pack(side="left", fill="x", expand=True, pady=8)
        ctk.CTkButton(
            conn,
            text="Refresh",
            width=72,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color=ACER_GREEN,
            hover_color="#6FA016",
            text_color="black",
            command=self._manual_refresh,
        ).pack(side="right", padx=10, pady=6)

        # Battery
        bat = self._card(self)
        self._section(bat, "BATTERY")
        row = ctk.CTkFrame(bat, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(2, 2))
        self.bat_value = ctk.CTkLabel(
            row, text="—", font=ctk.CTkFont(size=34, weight="bold"), text_color="white"
        )
        self.bat_value.pack(side="left")
        ctk.CTkLabel(
            row,
            text=f"  ·  {BATTERY_CAPACITY_MAH} mAh",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        ).pack(side="left", pady=(10, 0))
        self.bat_bar = ctk.CTkProgressBar(
            bat, height=10, progress_color=ACER_GREEN, fg_color="#333333"
        )
        self.bat_bar.pack(fill="x", padx=12, pady=(0, 4))
        self.bat_bar.set(0)
        self.bat_status = ctk.CTkLabel(
            bat,
            text="Waiting for HID readout…",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
            anchor="w",
        )
        self.bat_status.pack(anchor="w", padx=12, pady=(0, 10))

        # DPI
        dpi = self._card(self)
        self._section(dpi, "SENSITIVITY (DPI)")
        self.dpi_value = ctk.CTkLabel(
            dpi, text="Set level below", font=ctk.CTkFont(size=24, weight="bold"), text_color="white"
        )
        self.dpi_value.pack(anchor="w", padx=12, pady=(0, 2))
        ctk.CTkLabel(
            dpi,
            text="Use the mouse DPI button, then mark the active step:",
            font=ctk.CTkFont(size=10),
            text_color=MUTED,
            wraplength=WINDOW_W - 56,
            justify="left",
            anchor="w",
        ).pack(anchor="w", padx=12, pady=(0, 4))

        self.dpi_buttons_frame = ctk.CTkFrame(dpi, fg_color="transparent")
        self.dpi_buttons_frame.pack(fill="x", padx=8, pady=(0, 2))
        self._dpi_btns: list[ctk.CTkButton] = []
        for i, level in enumerate(DPI_LEVELS):
            btn = ctk.CTkButton(
                self.dpi_buttons_frame,
                text=str(level),
                width=68,
                height=28,
                font=ctk.CTkFont(size=11),
                fg_color="#333333",
                hover_color="#444444",
                text_color="white",
                command=lambda idx=i: self._select_dpi(idx),
            )
            btn.grid(row=i // 4, column=i % 4, padx=3, pady=3, sticky="ew")
            self._dpi_btns.append(btn)
        for c in range(4):
            self.dpi_buttons_frame.grid_columnconfigure(c, weight=1)

        ctk.CTkButton(
            dpi,
            text="I pressed DPI button  →  next level",
            height=30,
            font=ctk.CTkFont(size=12),
            fg_color=ACER_GREEN,
            hover_color="#6FA016",
            text_color="black",
            command=self._cycle_dpi,
        ).pack(fill="x", padx=12, pady=(4, 10))

        # Pointer speed (Windows only full support)
        win = self._card(self)
        self._section(win, "POINTER SPEED (OS)")
        if IS_WINDOWS:
            self.win_speed_label = ctk.CTkLabel(
                win, text="Speed: — / 20", font=ctk.CTkFont(size=12), text_color="white", anchor="w"
            )
            self.win_speed_label.pack(anchor="w", padx=12, pady=(2, 0))
            self.win_slider = ctk.CTkSlider(
                win,
                from_=1,
                to=20,
                number_of_steps=19,
                height=16,
                progress_color=ACER_GREEN,
                button_color=ACER_GREEN,
                command=self._on_win_speed_slide,
            )
            self.win_slider.pack(fill="x", padx=12, pady=6)
            speed, enhanced = windows_mouse_speed()
            if speed is not None:
                self.win_slider.set(speed)
                enh = "On" if enhanced else "Off" if enhanced is not None else "?"
                self.win_speed_label.configure(
                    text=f"Speed: {speed} / 20  ·  Enhance precision: {enh}"
                )
            ctk.CTkLabel(
                win,
                text="Windows mouse speed only — does not change sensor DPI.",
                font=ctk.CTkFont(size=10),
                text_color=MUTED,
                anchor="w",
            ).pack(anchor="w", padx=12, pady=(0, 10))
        else:
            os_name = "macOS" if IS_MAC else "Linux" if IS_LINUX else platform.system()
            ctk.CTkLabel(
                win,
                text=f"OS pointer settings are managed by {os_name} System Settings.\n"
                "Sensor DPI is still tracked above.",
                font=ctk.CTkFont(size=11),
                text_color=MUTED,
                justify="left",
                anchor="w",
                wraplength=WINDOW_W - 56,
            ).pack(anchor="w", padx=12, pady=(4, 10))

        # Device
        det = self._card(self, pady=(5, 12))
        self._section(det, "DEVICE")
        self.detail_label = ctk.CTkLabel(
            det,
            text=(
                f"USB 32C2:0066 · 2.4 GHz · {platform.system()}\n"
                f"Firmware: — · Pack: {BATTERY_CAPACITY_MAH} mAh"
            ),
            font=ctk.CTkFont(size=11),
            text_color="white",
            justify="left",
            anchor="w",
            wraplength=WINDOW_W - 56,
        )
        self.detail_label.pack(anchor="w", padx=12, pady=(2, 4))
        self.error_label = ctk.CTkLabel(
            det,
            text="",
            font=ctk.CTkFont(size=10),
            text_color="#E74C3C",
            wraplength=WINDOW_W - 56,
            justify="left",
            anchor="w",
        )
        self.error_label.pack(anchor="w", padx=12, pady=(0, 10))

    # --------------------------------------------------------------- handlers
    def _manual_refresh(self) -> None:
        self._on_status(self.mouse.refresh())

    def _select_dpi(self, index: int) -> None:
        self.mouse.set_tracked_dpi_index(index)
        self.cfg["dpi_index"] = index
        save_config(self.cfg)
        self._highlight_dpi(index)
        self.dpi_value.configure(text=f"{DPI_LEVELS[index]} DPI")

    def _cycle_dpi(self) -> None:
        idx = self.mouse.cycle_tracked_dpi()
        self.cfg["dpi_index"] = idx
        save_config(self.cfg)
        self._highlight_dpi(idx)
        self.dpi_value.configure(text=f"{DPI_LEVELS[idx]} DPI")

    def _highlight_dpi(self, index: Optional[int]) -> None:
        for i, btn in enumerate(self._dpi_btns):
            if index is not None and i == index:
                btn.configure(fg_color=ACER_GREEN, text_color="black", hover_color="#6FA016")
            else:
                btn.configure(fg_color="#333333", text_color="white", hover_color="#444444")

    def _on_win_speed_slide(self, value: float) -> None:
        speed = int(round(value))
        set_windows_mouse_speed(speed)
        _, enhanced = windows_mouse_speed()
        enh = "On" if enhanced else "Off" if enhanced is not None else "?"
        self.win_speed_label.configure(
            text=f"Speed: {speed} / 20  ·  Enhance precision: {enh}"
        )

    def _on_status(self, status: MouseStatus) -> None:
        try:
            self.after(0, lambda s=status: self._apply_status(s))
        except Exception:
            pass

    def _apply_status(self, status: MouseStatus) -> None:
        if self._closing:
            return

        if status.connected:
            self.conn_dot.configure(text_color=ACER_GREEN)
            name = status.product_name
            if len(name) > 36:
                name = name[:33] + "…"
            self.conn_label.configure(text=f"Connected · {name}")
        else:
            self.conn_dot.configure(text_color="#E74C3C")
            self.conn_label.configure(text="Not connected — plug in USB receiver")

        pct = status.battery_percent
        if pct is not None:
            self.bat_value.configure(text=f"{pct}%", text_color=battery_color(pct))
            self.bat_bar.set(pct / 100.0)
            self.bat_bar.configure(progress_color=battery_color(pct))
            tips = {
                "critical": "Charge soon — under 10% (LED may flash red).",
                "low": "Battery low — plan to recharge.",
                "medium": "Battery OK.",
                "good": "Battery healthy.",
                "full": "Battery full / near full.",
            }
            self.bat_status.configure(text=tips.get(status.battery_level_name, ""))
            if pct <= 15 and self._last_low_notify_pct != pct:
                self._last_low_notify_pct = pct
                self._notify_low_battery(pct)
        else:
            self.bat_value.configure(text="—", text_color=MUTED)
            self.bat_bar.set(0)
            self.bat_status.configure(text=status.last_error or "No battery data yet")

        idx = status.dpi_index
        if idx is not None and 0 <= idx < len(DPI_LEVELS):
            self.dpi_value.configure(text=f"{DPI_LEVELS[idx]} DPI")
            self._highlight_dpi(idx)
        elif status.dpi is not None:
            self.dpi_value.configure(text=f"{status.dpi} DPI")
        else:
            self.dpi_value.configure(text="Set level below")

        fw = status.firmware or "—"
        self.detail_label.configure(
            text=(
                f"USB 32C2:0066 (OnMicro 2.4G)  ·  {platform.system()} {platform.machine()}\n"
                f"Firmware: {fw}  ·  Pack: {BATTERY_CAPACITY_MAH} mAh  ·  "
                f"Updated: {self._fmt_age(status.last_update)}\n"
                f"DPI steps: {', '.join(str(d) for d in DPI_LEVELS)}"
            )
        )
        self.error_label.configure(text=status.last_error or "")

        if HAS_TRAY and self._tray is not None:
            try:
                self._tray.icon = make_tray_image(pct)
                self._tray.title = (
                    f"PopGo  {pct}%  ·  {status.dpi_label}"
                    if pct is not None
                    else "PopGo · disconnected"
                )
            except Exception:
                pass

    @staticmethod
    def _fmt_age(ts: float) -> str:
        import time as _t

        age = max(0, int(_t.time() - ts))
        if age < 3:
            return "just now"
        if age < 60:
            return f"{age}s ago"
        return f"{age // 60}m ago"

    def _notify_low_battery(self, pct: int) -> None:
        if not IS_WINDOWS:
            return
        try:
            import subprocess

            title = "Acer PopGo battery low"
            msg = f"Mouse battery is at {pct}%. Plug in to charge."
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] > $null; "
                "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
                "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                '$text = $template.GetElementsByTagName("text"); '
                f'$text.Item(0).AppendChild($template.CreateTextNode("{title}")) > $null; '
                f'$text.Item(1).AppendChild($template.CreateTextNode("{msg}")) > $null; '
                "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
                '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('
                '"Acer PopGo Companion").Show($toast);'
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", ps],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------- tray
    def _start_tray(self) -> None:
        if not HAS_TRAY:
            return

        def on_show(icon, item):  # noqa: ARG001
            self.after(0, self._show_window)

        def on_quit(icon, item):  # noqa: ARG001
            self.after(0, self._quit_app)

        menu = pystray.Menu(
            pystray.MenuItem("Open", on_show, default=True),
            pystray.MenuItem("Quit", on_quit),
        )
        self._tray = pystray.Icon(
            "popgo",
            make_tray_image(self.mouse.status.battery_percent),
            APP_NAME,
            menu,
        )
        self._tray_thread = threading.Thread(target=self._tray.run, daemon=True)
        self._tray_thread.start()

    def _show_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def _on_close(self) -> None:
        if HAS_TRAY and self._tray is not None:
            self.withdraw()
        else:
            self._quit_app()

    def _quit_app(self) -> None:
        self._closing = True
        try:
            self.poller.stop()
        except Exception:
            pass
        try:
            self.mouse.close()
        except Exception:
            pass
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
        self.destroy()


def main() -> int:
    app = PopGoApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
