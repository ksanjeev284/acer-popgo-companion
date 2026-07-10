"""
Acer PopGo Companion — battery, DPI, and mouse info for Windows.

Run:
  python app.py
"""
from __future__ import annotations

import json
import sys
import threading
import tkinter as tk
from pathlib import Path
from typing import Optional

import customtkinter as ctk

from mouse_device import DPI_LEVELS, BATTERY_CAPACITY_MAH, MouseStatus, PopGoMouse, StatusPoller

try:
    import winreg
except ImportError:  # non-Windows
    winreg = None  # type: ignore

# Optional tray — degrade gracefully
try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont

    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False


APP_NAME = "Acer PopGo Companion"
CONFIG_PATH = Path(__file__).with_name("config.json")
ACER_GREEN = "#83B81A"
ACER_DARK = "#1A1A1A"
CARD_BG = "#242424"
MUTED = "#9A9A9A"


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
    """Return (speed 1-20, enhanced_pointer_precision)."""
    if winreg is None:
        return None, None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\Mouse",
        )
        speed_s, _ = winreg.QueryValueEx(key, "MouseSensitivity")
        # MouseSpeed "1" means enhance pointer precision on (accel)
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
        # Notify system (SPI_SETMOUSESPEED = 0x0071)
        try:
            import ctypes

            SPI_SETMOUSESPEED = 0x0071
            SPIF_UPDATEINIFILE = 0x01
            SPIF_SENDCHANGE = 0x02
            ctypes.windll.user32.SystemParametersInfoW(
                SPI_SETMOUSESPEED,
                0,
                speed,
                SPIF_UPDATEINIFILE | SPIF_SENDCHANGE,
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
    # body
    draw.rounded_rectangle((10, 18, 48, 46), radius=6, outline=color, width=3)
    # nub
    draw.rectangle((48, 26, 54, 38), fill=color)
    # fill
    if percent is not None:
        fill_w = int(30 * max(0, min(100, percent)) / 100)
        if fill_w > 0:
            draw.rounded_rectangle(
                (14, 22, 14 + fill_w, 42), radius=3, fill=color
            )
    return img


class PopGoApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("460x640")
        self.minsize(420, 580)
        self.configure(fg_color=ACER_DARK)

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

        # first paint
        self.after(100, lambda: self._on_status(self.mouse.refresh()))

        if HAS_TRAY:
            self.after(300, self._start_tray)

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        pad = {"padx": 18, "pady": 8}

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", **pad)
        ctk.CTkLabel(
            header,
            text="Acer PopGo",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="white",
        ).pack(anchor="w")
        self.subtitle = ctk.CTkLabel(
            header,
            text="Companion app · battery & DPI",
            font=ctk.CTkFont(size=13),
            text_color=MUTED,
        )
        self.subtitle.pack(anchor="w")

        # Connection badge
        self.conn_frame = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=12)
        self.conn_frame.pack(fill="x", padx=18, pady=(4, 8))
        self.conn_dot = ctk.CTkLabel(
            self.conn_frame, text="●", font=ctk.CTkFont(size=14), text_color="#E74C3C"
        )
        self.conn_dot.pack(side="left", padx=(14, 6), pady=12)
        self.conn_label = ctk.CTkLabel(
            self.conn_frame,
            text="Searching for mouse…",
            font=ctk.CTkFont(size=13),
            text_color="white",
        )
        self.conn_label.pack(side="left", pady=12)
        self.refresh_btn = ctk.CTkButton(
            self.conn_frame,
            text="Refresh",
            width=80,
            height=28,
            fg_color=ACER_GREEN,
            hover_color="#6FA016",
            text_color="black",
            command=self._manual_refresh,
        )
        self.refresh_btn.pack(side="right", padx=12, pady=10)

        # Battery card
        bat = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=12)
        bat.pack(fill="x", padx=18, pady=8)
        ctk.CTkLabel(
            bat, text="BATTERY", font=ctk.CTkFont(size=11, weight="bold"), text_color=MUTED
        ).pack(anchor="w", padx=16, pady=(14, 0))
        row = ctk.CTkFrame(bat, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(4, 6))
        self.bat_value = ctk.CTkLabel(
            row, text="—", font=ctk.CTkFont(size=42, weight="bold"), text_color="white"
        )
        self.bat_value.pack(side="left")
        self.bat_hint = ctk.CTkLabel(
            row,
            text=f"  ·  {BATTERY_CAPACITY_MAH} mAh pack",
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
        )
        self.bat_hint.pack(side="left", pady=(12, 0))
        self.bat_bar = ctk.CTkProgressBar(
            bat, height=12, progress_color=ACER_GREEN, fg_color="#333333"
        )
        self.bat_bar.pack(fill="x", padx=16, pady=(0, 8))
        self.bat_bar.set(0)
        self.bat_status = ctk.CTkLabel(
            bat, text="Waiting for HID readout…", font=ctk.CTkFont(size=12), text_color=MUTED
        )
        self.bat_status.pack(anchor="w", padx=16, pady=(0, 14))

        # DPI card
        dpi = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=12)
        dpi.pack(fill="x", padx=18, pady=8)
        ctk.CTkLabel(
            dpi, text="SENSITIVITY (DPI)", font=ctk.CTkFont(size=11, weight="bold"), text_color=MUTED
        ).pack(anchor="w", padx=16, pady=(14, 0))
        self.dpi_value = ctk.CTkLabel(
            dpi, text="—", font=ctk.CTkFont(size=32, weight="bold"), text_color="white"
        )
        self.dpi_value.pack(anchor="w", padx=16, pady=(2, 4))
        self.dpi_note = ctk.CTkLabel(
            dpi,
            text="Hardware DPI button cycles levels. Mark the active level below.",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
            wraplength=400,
            justify="left",
        )
        self.dpi_note.pack(anchor="w", padx=16, pady=(0, 8))

        self.dpi_buttons_frame = ctk.CTkFrame(dpi, fg_color="transparent")
        self.dpi_buttons_frame.pack(fill="x", padx=12, pady=(0, 6))
        self._dpi_btns: list[ctk.CTkButton] = []
        for i, level in enumerate(DPI_LEVELS):
            btn = ctk.CTkButton(
                self.dpi_buttons_frame,
                text=str(level),
                width=72,
                height=32,
                fg_color="#333333",
                hover_color="#444444",
                text_color="white",
                command=lambda idx=i: self._select_dpi(idx),
            )
            btn.grid(row=i // 4, column=i % 4, padx=4, pady=4, sticky="ew")
            self._dpi_btns.append(btn)
        for c in range(4):
            self.dpi_buttons_frame.grid_columnconfigure(c, weight=1)

        cycle_row = ctk.CTkFrame(dpi, fg_color="transparent")
        cycle_row.pack(fill="x", padx=16, pady=(4, 14))
        ctk.CTkButton(
            cycle_row,
            text="I pressed DPI button  →  next level",
            fg_color=ACER_GREEN,
            hover_color="#6FA016",
            text_color="black",
            command=self._cycle_dpi,
        ).pack(fill="x")

        # Windows sensitivity
        win = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=12)
        win.pack(fill="x", padx=18, pady=8)
        ctk.CTkLabel(
            win,
            text="WINDOWS POINTER SPEED",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED,
        ).pack(anchor="w", padx=16, pady=(14, 0))
        self.win_speed_label = ctk.CTkLabel(
            win, text="Speed: — / 20", font=ctk.CTkFont(size=13), text_color="white"
        )
        self.win_speed_label.pack(anchor="w", padx=16, pady=(4, 0))
        self.win_slider = ctk.CTkSlider(
            win,
            from_=1,
            to=20,
            number_of_steps=19,
            progress_color=ACER_GREEN,
            button_color=ACER_GREEN,
            command=self._on_win_speed_slide,
        )
        self.win_slider.pack(fill="x", padx=16, pady=8)
        speed, enhanced = windows_mouse_speed()
        if speed is not None:
            self.win_slider.set(speed)
            enh = "On" if enhanced else "Off" if enhanced is not None else "?"
            self.win_speed_label.configure(text=f"Speed: {speed} / 20  ·  Enhance pointer precision: {enh}")
        self.win_apply_hint = ctk.CTkLabel(
            win,
            text="Drag to change Windows mouse speed (does not change sensor DPI).",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        )
        self.win_apply_hint.pack(anchor="w", padx=16, pady=(0, 14))

        # Device details
        det = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=12)
        det.pack(fill="both", expand=True, padx=18, pady=(8, 18))
        ctk.CTkLabel(
            det, text="DEVICE", font=ctk.CTkFont(size=11, weight="bold"), text_color=MUTED
        ).pack(anchor="w", padx=16, pady=(14, 4))
        self.detail_label = ctk.CTkLabel(
            det,
            text="USB 32C2:0066 · 2.4 GHz receiver\nFirmware: —\nButtons: 6 + scroll · Dual-mode capable",
            font=ctk.CTkFont(size=12),
            text_color="white",
            justify="left",
            anchor="w",
        )
        self.detail_label.pack(anchor="w", padx=16, pady=(0, 8))
        self.error_label = ctk.CTkLabel(
            det, text="", font=ctk.CTkFont(size=11), text_color="#E74C3C", wraplength=400, justify="left"
        )
        self.error_label.pack(anchor="w", padx=16, pady=(0, 14))

    # --------------------------------------------------------------- handlers
    def _manual_refresh(self) -> None:
        status = self.mouse.refresh()
        self._on_status(status)

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
            text=f"Speed: {speed} / 20  ·  Enhance pointer precision: {enh}"
        )

    def _on_status(self, status: MouseStatus) -> None:
        # marshal to UI thread
        try:
            self.after(0, lambda s=status: self._apply_status(s))
        except Exception:
            pass

    def _apply_status(self, status: MouseStatus) -> None:
        if self._closing:
            return

        if status.connected:
            self.conn_dot.configure(text_color=ACER_GREEN)
            self.conn_label.configure(text=f"Connected · {status.product_name}")
        else:
            self.conn_dot.configure(text_color="#E74C3C")
            self.conn_label.configure(text="Not connected — plug in the USB receiver")

        pct = status.battery_percent
        if pct is not None:
            self.bat_value.configure(text=f"{pct}%", text_color=battery_color(pct))
            self.bat_bar.set(pct / 100.0)
            self.bat_bar.configure(progress_color=battery_color(pct))
            level = status.battery_level_name
            tips = {
                "critical": "Charge soon — under 10% (LED may flash red).",
                "low": "Battery low — plan to recharge.",
                "medium": "Battery OK.",
                "good": "Battery healthy.",
                "full": "Battery full / near full.",
            }
            self.bat_status.configure(text=tips.get(level, ""))
            if pct <= 15 and self._last_low_notify_pct != pct:
                self._last_low_notify_pct = pct
                self._notify_low_battery(pct)
        else:
            self.bat_value.configure(text="—", text_color=MUTED)
            self.bat_bar.set(0)
            self.bat_status.configure(
                text=status.last_error or "No battery data yet"
            )

        # DPI display
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
                f"USB VID:PID  32C2:0066  (OnMicro 2.4G)\n"
                f"Firmware tag: {fw}\n"
                f"Sensor DPI steps: {', '.join(str(d) for d in DPI_LEVELS)}\n"
                f"Battery pack: {BATTERY_CAPACITY_MAH} mAh rechargeable\n"
                f"Last update: {self._fmt_age(status.last_update)}"
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
        try:
            # simple Windows toast via powershell (no extra deps)
            import subprocess

            title = "Acer PopGo battery low"
            msg = f"Mouse battery is at {pct}%. Plug in to charge."
            ps = (
                f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; '
                f'$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent('
                f'[Windows.UI.Notifications.ToastTemplateType]::ToastText02); '
                f'$text = $template.GetElementsByTagName("text"); '
                f'$text.Item(0).AppendChild($template.CreateTextNode("{title}")) > $null; '
                f'$text.Item(1).AppendChild($template.CreateTextNode("{msg}")) > $null; '
                f'$toast = [Windows.UI.Notifications.ToastNotification]::new($template); '
                f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Acer PopGo Companion").Show($toast);'
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
        # Close to tray if available; otherwise quit
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
