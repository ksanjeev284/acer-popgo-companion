"""
Acer PopGo Companion — battery, charge status, DPI, and mouse info.

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

from mouse_device import (
    DPI_LEVELS,
    BATTERY_CAPACITY_MAH,
    LOW_BATTERY_PERCENT,
    MouseStatus,
    PopGoMouse,
    PowerMode,
    StatusPoller,
)

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
APP_VERSION = "1.3.3"

# Outer window is fixed; content scrolls so every control is reachable
WINDOW_W = 500
WINDOW_H = 640

ACER_GREEN = "#83B81A"
ACER_DARK = "#161616"
CARD_BG = "#222222"
MUTED = "#9A9A9A"
BLUE = "#3498DB"
YELLOW = "#F1C40F"
RED = "#E74C3C"


def app_base_dir() -> Path:
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
    return {
        "dpi_index": None,
        "poll_seconds": 1.0,
        # Always start Auto in a safe default; user can lock Charging manually
        "power_mode": "auto",
        "start_minimized": False,
    }


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

            ctypes.windll.user32.SystemParametersInfoW(0x0071, 0, speed, 0x01 | 0x02)
        except Exception:
            pass
        return True
    except OSError:
        return False


def battery_color(percent: Optional[int]) -> str:
    if percent is None:
        return MUTED
    if percent <= 10:
        return RED
    if percent <= 20:
        return "#E67E22"
    if percent <= 50:
        return YELLOW
    return ACER_GREEN


def make_tray_image(
    percent: Optional[int], charging: Optional[bool] = False
) -> "Image.Image":
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = BLUE if charging else battery_color(percent)
    draw.rounded_rectangle((10, 18, 48, 46), radius=6, outline=color, width=3)
    draw.rectangle((48, 26, 54, 38), fill=color)
    if percent is not None:
        fill_w = int(30 * max(0, min(100, percent)) / 100)
        if fill_w > 0:
            draw.rounded_rectangle((14, 22, 14 + fill_w, 42), radius=3, fill=color)
    if charging:
        draw.polygon([(30, 16), (22, 34), (29, 34), (26, 48), (38, 28), (31, 28)], fill=color)
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
        # Never restore a stuck "charging" lock from disk — always start On battery
        self.cfg["power_mode"] = "auto"
        save_config(self.cfg)
        self.mouse.set_power_override("auto")

        self._tray = None
        self._tray_thread: Optional[threading.Thread] = None
        self._closing = False
        self._low_battery_notified = False
        self._power_btns: dict[str, ctk.CTkButton] = {}

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        interval = float(self.cfg.get("poll_seconds", 1.5))
        self.poller = StatusPoller(self.mouse, self._on_status, interval=interval)
        self.poller.start()

        self.after(80, lambda: self._on_status(self.mouse.refresh()))
        self.after(40, lambda: center_window(self, WINDOW_W, WINDOW_H))

        if HAS_TRAY:
            self.after(300, self._start_tray)

    # ------------------------------------------------------------------- UI
    def _card(self, parent, **pack_kw) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=10)
        defaults = {"fill": "x", "padx": 10, "pady": 4}
        defaults.update(pack_kw)
        f.pack(**defaults)
        return f

    def _h(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=MUTED,
        ).pack(anchor="w", padx=10, pady=(8, 0))

    def _build_ui(self) -> None:
        # Fixed header (always visible)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 0))
        ctk.CTkLabel(
            top,
            text="Acer PopGo",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="white",
        ).pack(side="left")
        ctk.CTkLabel(
            top,
            text=f"v{APP_VERSION}",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        ).pack(side="left", padx=(8, 0), pady=(4, 0))

        # Scrollable body — guarantees every option is reachable
        self.scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color="#333333",
            scrollbar_button_hover_color="#555555",
        )
        self.scroll.pack(fill="both", expand=True, padx=4, pady=(6, 8))
        body = self.scroll

        # Connection
        conn = self._card(body, pady=(2, 4))
        inner = ctk.CTkFrame(conn, fg_color="transparent")
        inner.pack(fill="x", padx=8, pady=6)
        self.conn_dot = ctk.CTkLabel(
            inner, text="●", font=ctk.CTkFont(size=12), text_color=RED, width=14
        )
        self.conn_dot.pack(side="left")
        self.conn_label = ctk.CTkLabel(
            inner,
            text="Searching…",
            font=ctk.CTkFont(size=12),
            text_color="white",
            anchor="w",
        )
        self.conn_label.pack(side="left", fill="x", expand=True, padx=6)
        ctk.CTkButton(
            inner,
            text="Refresh",
            width=70,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color=ACER_GREEN,
            hover_color="#6FA016",
            text_color="black",
            command=self._manual_refresh,
        ).pack(side="right")

        # Battery + charge
        bat = self._card(body)
        self._h(bat, "BATTERY & CHARGING")
        row = ctk.CTkFrame(bat, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(2, 0))
        self.bat_value = ctk.CTkLabel(
            row, text="—", font=ctk.CTkFont(size=32, weight="bold"), text_color="white"
        )
        self.bat_value.pack(side="left")
        right = ctk.CTkFrame(row, fg_color="transparent")
        right.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.charge_badge = ctk.CTkLabel(
            right,
            text="—",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=MUTED,
            anchor="w",
        )
        self.charge_badge.pack(anchor="w")
        ctk.CTkLabel(
            right,
            text=f"{BATTERY_CAPACITY_MAH} mAh pack",
            font=ctk.CTkFont(size=10),
            text_color=MUTED,
            anchor="w",
        ).pack(anchor="w")

        self.bat_bar = ctk.CTkProgressBar(
            bat, height=12, progress_color=ACER_GREEN, fg_color="#333333"
        )
        self.bat_bar.pack(fill="x", padx=10, pady=(6, 4))
        self.bat_bar.set(0)

        self.charge_status = ctk.CTkLabel(
            bat,
            text="Power: detecting…",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="white",
            anchor="w",
        )
        self.charge_status.pack(anchor="w", padx=10, pady=(0, 0))
        self.charge_detail = ctk.CTkLabel(
            bat,
            text="",
            font=ctk.CTkFont(size=10),
            text_color=MUTED,
            anchor="w",
            wraplength=WINDOW_W - 50,
            justify="left",
        )
        self.charge_detail.pack(anchor="w", padx=10, pady=(0, 2))
        self.bat_status = ctk.CTkLabel(
            bat,
            text="Waiting for HID…",
            font=ctk.CTkFont(size=10),
            text_color=MUTED,
            anchor="w",
            wraplength=WINDOW_W - 50,
            justify="left",
        )
        self.bat_status.pack(anchor="w", padx=10, pady=(0, 4))

        # Power mode override (fixes wrong auto detection)
        ctk.CTkLabel(
            bat,
            text="Power status (mouse has no charge sensor over 2.4G — pick manually):",
            font=ctk.CTkFont(size=10),
            text_color=MUTED,
            anchor="w",
        ).pack(anchor="w", padx=10, pady=(2, 2))
        mode_row = ctk.CTkFrame(bat, fg_color="transparent")
        mode_row.pack(fill="x", padx=8, pady=(0, 4))
        for key, label in (
            ("battery", "On battery"),
            ("charging", "I'm charging"),
            ("full", "Full"),
        ):
            btn = ctk.CTkButton(
                mode_row,
                text=label,
                height=32,
                font=ctk.CTkFont(size=12, weight="bold"),
                fg_color="#333333",
                hover_color="#444444",
                text_color="white",
                command=lambda m=key: self._set_power_mode(m),  # type: ignore[misc]
            )
            btn.pack(side="left", expand=True, fill="x", padx=2)
            self._power_btns[key] = btn
        ctk.CTkLabel(
            bat,
            text="Unplugged → leave On battery. Plug USB-C → press I'm charging. "
            "Unplug again → press On battery.",
            font=ctk.CTkFont(size=10),
            text_color=MUTED,
            anchor="w",
            wraplength=WINDOW_W - 50,
            justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 8))
        self._highlight_power_mode(self.mouse.get_power_override())

        # DPI
        dpi = self._card(body)
        self._h(dpi, "SENSITIVITY (DPI)")
        self.dpi_value = ctk.CTkLabel(
            dpi,
            text="Set level below",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="white",
            anchor="w",
        )
        self.dpi_value.pack(anchor="w", padx=10, pady=(0, 2))
        ctk.CTkLabel(
            dpi,
            text="Press the mouse DPI button, then mark the active step:",
            font=ctk.CTkFont(size=10),
            text_color=MUTED,
            anchor="w",
            wraplength=WINDOW_W - 50,
        ).pack(anchor="w", padx=10, pady=(0, 4))

        grid = ctk.CTkFrame(dpi, fg_color="transparent")
        grid.pack(fill="x", padx=6, pady=(0, 2))
        self._dpi_btns: list[ctk.CTkButton] = []
        for i, level in enumerate(DPI_LEVELS):
            btn = ctk.CTkButton(
                grid,
                text=str(level),
                height=28,
                font=ctk.CTkFont(size=11),
                fg_color="#333333",
                hover_color="#444444",
                text_color="white",
                command=lambda idx=i: self._select_dpi(idx),
            )
            btn.grid(row=i // 4, column=i % 4, padx=2, pady=2, sticky="ew")
            self._dpi_btns.append(btn)
        for c in range(4):
            grid.grid_columnconfigure(c, weight=1)

        ctk.CTkButton(
            dpi,
            text="I pressed DPI button  →  next level",
            height=30,
            font=ctk.CTkFont(size=12),
            fg_color=ACER_GREEN,
            hover_color="#6FA016",
            text_color="black",
            command=self._cycle_dpi,
        ).pack(fill="x", padx=10, pady=(4, 8))

        # Pointer speed
        win = self._card(body)
        self._h(win, "POINTER SPEED (OS)")
        if IS_WINDOWS:
            self.win_speed_label = ctk.CTkLabel(
                win,
                text="Speed: — / 20",
                font=ctk.CTkFont(size=12),
                text_color="white",
                anchor="w",
            )
            self.win_speed_label.pack(anchor="w", padx=10, pady=(2, 0))
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
            self.win_slider.pack(fill="x", padx=10, pady=6)
            speed, enhanced = windows_mouse_speed()
            if speed is not None:
                self.win_slider.set(speed)
                enh = "On" if enhanced else "Off" if enhanced is not None else "?"
                self.win_speed_label.configure(
                    text=f"Speed: {speed} / 20  ·  Enhance precision: {enh}"
                )
            ctk.CTkLabel(
                win,
                text="Windows pointer speed only — not sensor DPI.",
                font=ctk.CTkFont(size=10),
                text_color=MUTED,
                anchor="w",
            ).pack(anchor="w", padx=10, pady=(0, 8))
        else:
            os_name = "macOS" if IS_MAC else "Linux" if IS_LINUX else platform.system()
            ctk.CTkLabel(
                win,
                text=f"Managed by {os_name} system settings.",
                font=ctk.CTkFont(size=11),
                text_color=MUTED,
                anchor="w",
            ).pack(anchor="w", padx=10, pady=(4, 8))

        # Device
        det = self._card(body, pady=(4, 10))
        self._h(det, "DEVICE")
        self.detail_label = ctk.CTkLabel(
            det,
            text=f"USB 32C2:0066 · {platform.system()}",
            font=ctk.CTkFont(size=11),
            text_color="white",
            justify="left",
            anchor="w",
            wraplength=WINDOW_W - 50,
        )
        self.detail_label.pack(anchor="w", padx=10, pady=(2, 2))
        self.error_label = ctk.CTkLabel(
            det,
            text="",
            font=ctk.CTkFont(size=10),
            text_color=RED,
            wraplength=WINDOW_W - 50,
            justify="left",
            anchor="w",
        )
        self.error_label.pack(anchor="w", padx=10, pady=(0, 8))

    # --------------------------------------------------------------- handlers
    def _manual_refresh(self) -> None:
        self._on_status(self.mouse.refresh())

    def _set_power_mode(self, mode: str) -> None:
        m: PowerMode = mode  # type: ignore[assignment]
        # Instant status update (does not wait on HID) so the UI always flips
        status = self.mouse.set_power_override(m)
        # Don't persist "charging" across restarts — only session lock
        self.cfg["power_mode"] = "auto" if mode == "charging" else mode
        save_config(self.cfg)
        self._highlight_power_mode(m)
        self._apply_status(status)
        try:
            self._on_status(self.mouse.refresh())
        except Exception:
            pass

    def _highlight_power_mode(self, mode: str) -> None:
        # Map auto → battery button for highlight
        active = "battery" if mode in ("auto", "battery") else mode
        for key, btn in self._power_btns.items():
            if key == active:
                color = BLUE if key == "charging" else ACER_GREEN
                btn.configure(fg_color=color, text_color="black", hover_color=color)
            else:
                btn.configure(fg_color="#333333", text_color="white", hover_color="#444444")

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
            if len(name) > 34:
                name = name[:31] + "…"
            self.conn_label.configure(text=f"Connected · {name}")
        else:
            self.conn_dot.configure(text_color=RED)
            self.conn_label.configure(text="Not connected — plug in USB receiver")

        pct = status.battery_percent
        self._update_charge_ui(status)

        if pct is not None:
            self.bat_value.configure(text=f"{pct}%", text_color=battery_color(pct))
            self.bat_bar.set(pct / 100.0)
            self.bat_bar.configure(
                progress_color=BLUE if status.is_charging else battery_color(pct)
            )
            tips = {
                "critical": f"Critical ≤{LOW_BATTERY_PERCENT}% — plug in USB-C.",
                "low": "Battery low — plan to recharge.",
                "medium": "Battery OK.",
                "good": "Battery healthy.",
                "high": "Battery strong.",
                "full": "Battery full.",
            }
            tip = tips.get(status.battery_level_name, "")
            if status.is_charging:
                tip = "Charging — leave USB-C plugged in."
            elif status.is_full:
                tip = "Full — safe to unplug."
            self.bat_status.configure(text=tip)
            self._maybe_notify_low_battery(status)
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
        raw = ""
        if status.raw_status and len(status.raw_status) >= 4:
            raw = "  raw[" + " ".join(f"{b:02X}" for b in status.raw_status[:8]) + "]"
        self.detail_label.configure(
            text=(
                f"USB 32C2:0066 · {platform.system()} {platform.machine()}\n"
                f"Firmware {fw} · {BATTERY_CAPACITY_MAH} mAh · "
                f"Updated {self._fmt_age(status.last_update)}{raw}\n"
                f"DPI: {', '.join(str(d) for d in DPI_LEVELS)}"
            )
        )
        self.error_label.configure(text=status.last_error or "")
        # Button highlight handled in _update_charge_ui

        if HAS_TRAY and self._tray is not None:
            try:
                self._tray.icon = make_tray_image(pct, status.is_charging)
                self._tray.title = (
                    f"PopGo {pct}% · {status.charge_label}"
                    if pct is not None
                    else f"PopGo · {status.charge_label}"
                )
            except Exception:
                pass

    def _update_charge_ui(self, status: MouseStatus) -> None:
        # Prefer is_charging flag so we never show CHARGING by accident
        if status.is_charging:
            self.charge_badge.configure(text="CHARGING", text_color=BLUE)
            self.charge_status.configure(
                text="Power: CHARGING (you marked this)", text_color=BLUE
            )
        elif status.power_source == "full" or status.is_full:
            self.charge_badge.configure(text="FULL", text_color=ACER_GREEN)
            self.charge_status.configure(text="Power: FULLY CHARGED", text_color=ACER_GREEN)
        else:
            self.charge_badge.configure(text="ON BATTERY", text_color=YELLOW)
            self.charge_status.configure(
                text="Power: ON BATTERY — not charging", text_color=YELLOW
            )

        detail = status.charge_detail or ""
        self.charge_detail.configure(text=detail)

        # Keep button highlight in sync (battery is default)
        mode = status.override_mode
        if mode == "auto":
            mode = "battery"
        self._highlight_power_mode(mode)

    def _maybe_notify_low_battery(self, status: MouseStatus) -> None:
        pct = status.battery_percent
        if pct is None:
            return
        if pct > LOW_BATTERY_PERCENT + 5 or status.is_charging:
            self._low_battery_notified = False
            return
        if pct <= LOW_BATTERY_PERCENT and not status.is_charging:
            if not self._low_battery_notified:
                self._low_battery_notified = True
                self._notify_low_battery(pct)

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
        title = "Acer PopGo — battery almost empty"
        msg = (
            f"Battery is at {pct}% (critical ≤{LOW_BATTERY_PERCENT}%). "
            "Not charging — plug in USB-C."
        )
        try:
            self.bat_status.configure(text=msg)
        except Exception:
            pass

        if IS_WINDOWS:
            try:
                import subprocess

                t = title.replace("'", "''")
                m = msg.replace("'", "''")
                ps = (
                    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                    "ContentType = WindowsRuntime] > $null; "
                    "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
                    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                    '$text = $template.GetElementsByTagName("text"); '
                    f"$text.Item(0).AppendChild($template.CreateTextNode('{t}')) > $null; "
                    f"$text.Item(1).AppendChild($template.CreateTextNode('{m}')) > $null; "
                    "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
                    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                    "'Acer PopGo Companion').Show($toast);"
                )
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", ps],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:
                pass
        elif HAS_TRAY and self._tray is not None:
            try:
                self._tray.notify(msg, title)
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
            make_tray_image(
                self.mouse.status.battery_percent,
                self.mouse.status.is_charging,
            ),
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
