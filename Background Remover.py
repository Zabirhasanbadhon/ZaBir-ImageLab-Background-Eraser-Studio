#!/usr/bin/env python3
"""
ZaBir ImageLab AI — Background Remover & Image Editor
=====================================================

Single-file desktop application (Python + Tkinter + Pillow + rembg).

SETUP (Windows PowerShell, inside VS Code terminal)
---------------------------------------------------
    py -3.11 -m venv .venv
    .\\.venv\\Scripts\\Activate.ps1
    python -m pip install --upgrade pip
    pip install pillow numpy requests "rembg[cpu]" onnxruntime
    pip install tkinterdnd2          # optional: drag & drop support
    python "AI-STYLE BACKGROUND REMOVER.py"

If PowerShell blocks activation run once:
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

On first background removal rembg downloads the model (~170 MB for u2net)
to  C:\\Users\\<you>\\.u2net  — an internet connection is needed once.

Run the built-in self-tests (no AI model needed):
    python "AI-STYLE BACKGROUND REMOVER.py" --selftest
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageOps, ImageTk

try:  # optional drag & drop
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    HAS_DND = True
except Exception:  # pragma: no cover
    HAS_DND = False

APP_NAME = "ZaBir ImageLab"
WINDOW_TITLE = "ZaBir - ImageLab Background Eraser Studio"
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "imagelab.log"
LANCZOS = Image.Resampling.LANCZOS
BILINEAR = Image.Resampling.BILINEAR
NEAREST = Image.Resampling.NEAREST

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
log = logging.getLogger("imagelab")


def setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(LOG_PATH, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# --------------------------------------------------------------------------- #
# Theme / constants
# --------------------------------------------------------------------------- #
C = {
    "bg": "#10131B", "side": "#171B26", "panel": "#1E2432", "elev": "#272F40",
    "elev2": "#323C52", "accent": "#7C5CFC", "accent2": "#42D6C5",
    "accent_hi": "#9378FF", "text": "#F5F7FF", "muted": "#AAB3C5",
    "border": "#343D50", "ok": "#36C98F", "err": "#FF657A",
}
FONT = "Segoe UI"
GLOW = 0.28  # strength of the accent glow on the welcome screen

PRESETS: dict[str, Optional[tuple[int, int]]] = {
    "Instagram Square (1080×1080)": (1080, 1080),
    "Instagram Portrait (1080×1350)": (1080, 1350),
    "Instagram Story/Reel (1080×1920)": (1080, 1920),
    "YouTube Thumbnail (1280×720)": (1280, 720),
    "Facebook Cover (820×312)": (820, 312),
    "LinkedIn Post (1200×627)": (1200, 627),
    "Custom": None,
}
PRESET_FITS = {"Cover (crop to fill)": "cover", "Contain (fit, pad)": "contain",
               "Stretch": "stretch"}
BG_FITS = ["cover", "contain", "stretch", "crop"]
PALETTE = ["#FFFFFF", "#000000", "#F5F5F5", "#FF657A", "#FFB347", "#FFE066",
           "#36C98F", "#42D6C5", "#4DA3FF", "#7C5CFC", "#E879F9", "#94A3B8"]
ASPECTS = {"Free": None, "1:1": 1.0, "4:5": 0.8, "3:2": 1.5, "4:3": 4 / 3,
           "16:9": 16 / 9, "9:16": 9 / 16}
MODELS = ["u2net", "u2netp", "isnet-general-use", "silueta", "u2net_human_seg"]
MAX_DIM = 16000

DEFAULT_CONFIG: dict[str, Any] = {
    "last_export_dir": "", "output_format": "PNG", "jpeg_quality": 92,
    "webp_quality": 90, "keep_aspect": True, "model": "u2net",
    "checker_size": 12, "window": [1440, 900], "recent": [], "sidebar_width": 220,
}


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def load_config() -> dict[str, Any]:
    """Load config.json, validating every value; fall back to defaults."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("config is not an object")
    except FileNotFoundError:
        return cfg
    except Exception as exc:
        log.warning("Invalid config ignored: %s", exc)
        return cfg

    def pick(key: str, ok: Callable[[Any], bool]) -> None:
        if key in data and ok(data[key]):
            cfg[key] = data[key]

    pick("last_export_dir", lambda v: isinstance(v, str))
    pick("output_format", lambda v: v in ("PNG", "JPEG", "WEBP"))
    pick("jpeg_quality", lambda v: isinstance(v, int) and 1 <= v <= 100)
    pick("webp_quality", lambda v: isinstance(v, int) and 1 <= v <= 100)
    pick("keep_aspect", lambda v: isinstance(v, bool))
    pick("model", lambda v: v in MODELS)
    pick("checker_size", lambda v: isinstance(v, int) and 4 <= v <= 64)
    pick("window", lambda v: isinstance(v, list) and len(v) == 2
         and all(isinstance(i, int) and 600 <= i <= 10000 for i in v))
    pick("sidebar_width", lambda v: isinstance(v, int) and not isinstance(v, bool) and 150 <= v <= 420)
    pick("recent", lambda v: isinstance(v, list) and all(isinstance(i, str) for i in v))
    cfg["recent"] = cfg["recent"][:10]
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), "utf-8")
    except OSError as exc:
        log.warning("Could not save config: %s", exc)


# --------------------------------------------------------------------------- #
# Pure image-processing helpers (UI independent, unit-testable)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BgSettings:
    """Replacement-background description (kept separate from the cutout)."""
    mode: str = "none"          # none | color | image
    color: str = "#FFFFFF"
    image_path: str = ""
    fit: str = "cover"
    x: float = 0.5
    y: float = 0.5


def parse_hex(value: str) -> Optional[tuple[int, int, int]]:
    """Parse #RGB / #RRGGBB (with or without '#'). Returns None when invalid."""
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return None
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return None


def load_image(path: str | Path) -> tuple[Image.Image, Optional[bytes]]:
    """Open an image, fix EXIF orientation, return (RGBA image, exif bytes)."""
    with Image.open(path) as im:
        im.load()
        oriented = ImageOps.exif_transpose(im)
        exif: Optional[bytes] = None
        try:
            ex = oriented.getexif()
            if len(ex):
                exif = ex.tobytes()
        except Exception:
            exif = None
        return oriented.convert("RGBA"), exif


_checker_cache: dict[tuple, Image.Image] = {}


def make_checkerboard(w: int, h: int, sq: int = 12,
                      c1: tuple = (205, 205, 205), c2: tuple = (150, 150, 150)) -> Image.Image:
    """Reusable light/medium-gray checkerboard (preview only, never exported)."""
    key = (w, h, sq, c1, c2)
    if key in _checker_cache:
        return _checker_cache[key]
    tile = Image.new("RGBA", (sq * 2, sq * 2), c1 + (255,))
    d = ImageDraw.Draw(tile)
    d.rectangle([sq, 0, sq * 2 - 1, sq - 1], fill=c2 + (255,))
    d.rectangle([0, sq, sq - 1, sq * 2 - 1], fill=c2 + (255,))
    board = Image.new("RGBA", (w, h))
    for y in range(0, h, sq * 2):
        for x in range(0, w, sq * 2):
            board.paste(tile, (x, y))
    _checker_cache.clear()
    _checker_cache[key] = board
    return board


def hex_mix(c1: str, c2: str, t: float) -> str:
    """Blend two hex colours (t=0 -> c1, t=1 -> c2)."""
    a = parse_hex(c1) or (0, 0, 0)
    b = parse_hex(c2) or (0, 0, 0)
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_gradient(w: int, h: int, c1: str, c2: str, horizontal: bool = True) -> Image.Image:
    """Linear two-colour gradient image (left->right or top->bottom)."""
    w, h = max(1, w), max(1, h)
    mask = Image.linear_gradient("L")  # black (top) -> white (bottom)
    if horizontal:
        mask = mask.rotate(90, expand=True)  # black (left) -> white (right)
    mask = mask.resize((w, h), BILINEAR)
    a = Image.new("RGB", (w, h), parse_hex(c1) or (0, 0, 0))
    b = Image.new("RGB", (w, h), parse_hex(c2) or (0, 0, 0))
    return Image.composite(b, a, mask)


def make_glow(w: int, h: int, bg: str, glow: str) -> Image.Image:
    """Soft radial glow: `glow` colour in the centre fading to `bg` at the edges."""
    w, h = max(1, w), max(1, h)
    mask = Image.radial_gradient("L").resize((w, h), BILINEAR)  # black centre -> white edge
    return Image.composite(Image.new("RGB", (w, h), parse_hex(bg) or (0, 0, 0)),
                           Image.new("RGB", (w, h), parse_hex(glow) or (0, 0, 0)), mask)


def rounded_points(x0: float, y0: float, x1: float, y1: float, r: float) -> list[float]:
    """Point list for a rounded rectangle (use with create_polygon(smooth=True))."""
    return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
            x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]


def has_transparency(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA"):
        return img.getchannel("A").getextrema()[0] < 255
    return "transparency" in img.info


def fit_image(img: Image.Image, size: tuple[int, int], mode: str,
              pos: tuple[float, float] = (0.5, 0.5)) -> Image.Image:
    """Fit `img` into an RGBA canvas of exactly `size` (cover/contain/stretch/crop)."""
    tw, th = size
    iw, ih = img.size
    src = img.convert("RGBA")
    if mode == "stretch":
        return src.resize(size, LANCZOS)
    if mode == "crop":
        scaled = src
    else:
        s = max(tw / iw, th / ih) if mode == "cover" else min(tw / iw, th / ih)
        scaled = src.resize((max(1, round(iw * s)), max(1, round(ih * s))), LANCZOS)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    sw, sh = scaled.size
    canvas.paste(scaled, (round((tw - sw) * pos[0]), round((th - sh) * pos[1])))
    return canvas


def composite(fg: Image.Image, bg: BgSettings, bg_img: Optional[Image.Image]) -> Image.Image:
    """Alpha-composite the foreground cutout over the chosen background layer."""
    if bg.mode == "color":
        rgb = parse_hex(bg.color) or (255, 255, 255)
        base = Image.new("RGBA", fg.size, rgb + (255,))
    elif bg.mode == "image" and bg_img is not None:
        base = fit_image(bg_img, fg.size, bg.fit, (bg.x, bg.y))
    else:
        return fg
    return Image.alpha_composite(base, fg)


def apply_adjustments(img: Image.Image, brightness: float, contrast: float,
                      saturation: float, sharpness: float) -> Image.Image:
    """Adjust RGB channels while leaving alpha untouched."""
    rgba = img.convert("RGBA")
    r, g, b, a = rgba.split()
    rgb = Image.merge("RGB", (r, g, b))
    for cls, v in ((ImageEnhance.Brightness, brightness), (ImageEnhance.Contrast, contrast),
                   (ImageEnhance.Color, saturation), (ImageEnhance.Sharpness, sharpness)):
        if abs(v - 1.0) > 1e-3:
            rgb = cls(rgb).enhance(v)
    rgb.putalpha(a)
    return rgb


def flatten(img: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """Flatten transparency onto a solid colour and return RGB."""
    base = Image.new("RGBA", img.size, color + (255,))
    return Image.alpha_composite(base, img.convert("RGBA")).convert("RGB")


def save_image(img: Image.Image, path: Path, fmt: str, quality: int = 92,
               jpeg_bg: tuple[int, int, int] = (255, 255, 255),
               exif: Optional[bytes] = None) -> None:
    """Save as PNG (RGBA kept), JPEG (flattened on jpeg_bg) or WEBP (RGBA kept)."""
    fmt = fmt.upper()
    params: dict[str, Any] = {}
    if fmt == "PNG":
        out, params = img.convert("RGBA"), {"optimize": True}
    elif fmt in ("JPEG", "JPG"):
        fmt, out = "JPEG", flatten(img, jpeg_bg)
        params = {"quality": quality, "optimize": True}
    elif fmt == "WEBP":
        out, params = img.convert("RGBA"), {"quality": quality}
    else:
        raise ValueError(f"Unsupported format: {fmt}")
    if exif:
        try:
            out.save(path, fmt, exif=exif, **params)
            return
        except Exception as exc:
            log.warning("Saving with metadata failed (%s); retrying without", exc)
    out.save(path, fmt, **params)


def open_folder(path: Path) -> None:
    folder = str(path.parent)
    try:
        if sys.platform.startswith("win"):
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception as exc:
        log.warning("Cannot open folder: %s", exc)


# --------------------------------------------------------------------------- #
# History (snapshots; images are treated as immutable, so references are cheap)
# --------------------------------------------------------------------------- #
@dataclass
class Snapshot:
    fg: Image.Image
    bg: BgSettings
    label: str


class HistoryManager:
    """Bounded undo/redo stack."""

    def __init__(self, limit: int = 30) -> None:
        self.limit = limit
        self.states: list[Snapshot] = []
        self.idx = -1

    def reset(self, snap: Snapshot) -> None:
        self.states, self.idx = [snap], 0

    def push(self, snap: Snapshot) -> None:
        del self.states[self.idx + 1:]
        self.states.append(snap)
        if len(self.states) > self.limit:
            self.states.pop(0)
        self.idx = len(self.states) - 1

    @property
    def can_undo(self) -> bool:
        return self.idx > 0

    @property
    def can_redo(self) -> bool:
        return 0 <= self.idx < len(self.states) - 1

    def undo(self) -> Optional[Snapshot]:
        if not self.can_undo:
            return None
        self.idx -= 1
        return self.states[self.idx]

    def redo(self) -> Optional[Snapshot]:
        if not self.can_redo:
            return None
        self.idx += 1
        return self.states[self.idx]


# --------------------------------------------------------------------------- #
# AI background removal (rembg, local inference)
# --------------------------------------------------------------------------- #
class BackgroundRemovalError(Exception):
    """User-presentable background-removal failure."""


class BackgroundRemover:
    """Lazy rembg wrapper. The model is configured via `self.model`."""

    def __init__(self, model: str = "u2net") -> None:
        self.model = model
        self._session: Any = None
        self._session_model: Optional[str] = None
        self._lock = threading.Lock()

    def remove(self, img: Image.Image) -> Image.Image:
        with self._lock:
            try:
                from rembg import new_session, remove  # type: ignore
            except Exception as exc:
                raise BackgroundRemovalError(
                    "The AI engine (rembg / onnxruntime) is not installed or failed to load.\n\n"
                    "Install it in your virtual environment:\n"
                    '    pip install "rembg[cpu]" onnxruntime\n\n'
                    f"Technical detail: {exc}") from exc
            try:
                if self._session is None or self._session_model != self.model:
                    self._session = new_session(self.model)
                    self._session_model = self.model
                out = remove(img.convert("RGB"), session=self._session)
            except Exception as exc:
                raise BackgroundRemovalError(
                    f"AI model '{self.model}' could not run.\n\n"
                    "On first use rembg downloads the model weights (needs internet) into\n"
                    "C:\\Users\\<you>\\.u2net . Check your connection or firewall and try again,\n"
                    "or choose another model under Edit → AI Model.\n\n"
                    f"Technical detail: {exc}") from exc
            if not isinstance(out, Image.Image):
                raise BackgroundRemovalError("rembg returned an unexpected result type.")
            return out.convert("RGBA")


class TaskRunner:
    """Run one job in a worker thread; deliver results on the Tk thread via after()."""

    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self._q: queue.Queue = queue.Queue()
        self._token = 0
        self._after: Optional[str] = None
        self.active = False

    def submit(self, fn: Callable[[], Any], on_done: Callable[[Any], None],
               on_error: Callable[[BaseException], None]) -> None:
        self._token += 1
        token = self._token
        self.active = True

        def work() -> None:
            try:
                self._q.put((token, fn(), on_done))
            except BaseException as exc:  # noqa: BLE001
                log.exception("Worker failed")
                self._q.put((token, exc, on_error))

        threading.Thread(target=work, daemon=True, name="imagelab-worker").start()
        self._schedule()

    def cancel(self) -> None:
        """Discard the pending result (a running inference cannot be interrupted)."""
        self._token += 1
        self.active = False

    def shutdown(self) -> None:
        self.cancel()
        if self._after:
            try:
                self.root.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def _schedule(self) -> None:
        if self._after is None:
            self._after = self.root.after(80, self._poll)

    def _poll(self) -> None:
        self._after = None
        try:
            while True:
                token, payload, cb = self._q.get_nowait()
                if token != self._token:
                    continue
                self.active = False
                try:
                    cb(payload)
                except Exception:
                    log.exception("Callback failed")
        except queue.Empty:
            pass
        if self.active:
            self._schedule()


# --------------------------------------------------------------------------- #
# Application (Tkinter)
# --------------------------------------------------------------------------- #
import tkinter as tk  # noqa: E402
from tkinter import colorchooser, filedialog, messagebox, ttk  # noqa: E402


import tkinter.font as tkfont  # noqa: E402


class RoundButton(tk.Canvas):
    """Rounded, hover-animated button (kinds: normal, accent, side)."""

    KINDS = {"normal": ("elev", "elev2", "accent"),
             "accent": ("accent", "accent_hi", "accent2"),
             "side": ("side", "elev", "accent")}

    def __init__(self, parent, text: str, command: Callable[[], None], kind: str = "normal",
                 bg: Optional[str] = None, width: Optional[int] = None, height: int = 34,
                 anchor: str = "center", radius: int = 10) -> None:
        super().__init__(parent, height=height, bg=bg or C["panel"], highlightthickness=0,
                         bd=0, cursor="hand2")
        self._text, self._cmd, self._kind = text, command, kind
        self._anchor, self._radius = anchor, radius
        self._font = tkfont.Font(family=FONT, size=10, weight="bold" if kind == "accent" else "normal")
        self._disabled = self._hover = self._down = False
        self.configure(width=width or (self._font.measure(text) + 34))
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, down=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(down=True))
        self.bind("<ButtonRelease-1>", self._release)

    def _set(self, hover: Optional[bool] = None, down: Optional[bool] = None) -> None:
        if hover is not None:
            self._hover = hover
        if down is not None:
            self._down = down
        self._draw()

    def _release(self, e) -> None:
        was_down = self._down
        self._set(down=False)
        if was_down and not self._disabled and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self._cmd()

    def state(self, spec=None):
        """ttk-compatible state(): accepts ['disabled'] / ['!disabled']."""
        if spec is None:
            return ("disabled",) if self._disabled else ()
        for s in spec:
            if s == "disabled":
                self._disabled = True
            elif s == "!disabled":
                self._disabled = False
        self.configure(cursor="arrow" if self._disabled else "hand2")
        self._draw()
        return ()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()

    def _draw(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            w, h = int(self.cget("width")), int(self.cget("height"))
        self.delete("all")
        base, hov, press = (C[k] for k in self.KINDS[self._kind])
        if self._disabled:
            fill = C["side"] if self._kind == "side" else hex_mix(C["elev"], C["panel"], 0.6)
            fg = "#5E6779"
        elif self._down:
            fill, fg = press, "#FFFFFF"
        elif self._hover:
            fill, fg = hov, "#FFFFFF"
        else:
            fill, fg = base, (C["text"] if self._kind != "side" else C["muted"])
        if self._kind == "accent" and not self._disabled:
            fg = "#FFFFFF"
        self.create_polygon(rounded_points(1, 1, w - 1, h - 1, self._radius), smooth=True, fill=fill, outline="")
        if self._anchor == "w":
            self.create_text(16, h / 2, text=self._text, fill=fg, font=self._font, anchor="w")
        else:
            self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)


class Swatch(tk.Canvas):
    """Round colour swatch button with hover ring."""

    def __init__(self, parent, color: str, command: Callable[[], None], size: int = 28,
                 bg: Optional[str] = None) -> None:
        super().__init__(parent, width=size, height=size, bg=bg or C["panel"], highlightthickness=0,
                         bd=0, cursor="hand2")
        self._color, self._cmd, self._size = color, command, size
        self._hover = self._disabled = False
        self.bind("<Enter>", lambda e: self._set(True))
        self.bind("<Leave>", lambda e: self._set(False))
        self.bind("<ButtonRelease-1>", lambda e: None if self._disabled else self._cmd())
        self._draw()

    def _set(self, hover: bool) -> None:
        self._hover = hover
        self._draw()

    def state(self, spec=None):
        if spec is None:
            return ("disabled",) if self._disabled else ()
        for s in spec:
            if s == "disabled":
                self._disabled = True
            elif s == "!disabled":
                self._disabled = False
        self._draw()
        return ()

    def _draw(self) -> None:
        s = self._size
        self.delete("all")
        ring = C["accent2"] if (self._hover and not self._disabled) else C["border"]
        self.create_oval(3, 3, s - 3, s - 3, fill=self._color, outline=ring,
                         width=2 if self._hover and not self._disabled else 1)


class ScrollableFrame(ttk.Frame):
    """Vertically scrollable container (scrollbar auto-hides); put content into `.body`."""

    def __init__(self, parent, bg: Optional[str] = None, style: str = "Panel.TFrame",
                 padding: tuple = (14, 12)) -> None:
        super().__init__(parent, style=style)
        self._canvas = tk.Canvas(self, bg=bg or C["panel"], highlightthickness=0, bd=0)
        self._bar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._bar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self._canvas, style=style, padding=padding)
        self._win = self._canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._sync)
        self._canvas.bind("<Configure>", self._on_canvas)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def _on_canvas(self, e) -> None:
        self._canvas.itemconfigure(self._win, width=e.width)
        self._sync()

    def _sync(self, _e=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        need = self.body.winfo_reqheight() > self._canvas.winfo_height() > 1
        if need and not self._bar.winfo_ismapped():
            self._bar.pack(side="right", fill="y", before=self._canvas)
        elif not need and self._bar.winfo_ismapped():
            self._bar.pack_forget()
            self._canvas.yview_moveto(0)

    def _enter(self, _e) -> None:
        self._canvas.bind_all("<MouseWheel>", self._wheel)

    def _leave(self, _e) -> None:
        x, y = self.winfo_pointerxy()
        w = self.winfo_containing(x, y)
        if w is not None and str(w).startswith(str(self)):
            return
        self._canvas.unbind_all("<MouseWheel>")

    def _wheel(self, e) -> None:
        if self.body.winfo_reqheight() > self._canvas.winfo_height():
            self._canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")


class App:
    """Main window and application state."""

    def __init__(self) -> None:
        self.cfg = load_config()
        self.root: tk.Tk = TkinterDnD.Tk() if HAS_DND else tk.Tk()
        self.root.title(WINDOW_TITLE)
        w, h = self.cfg["window"]
        self.root.geometry(f"{w}x{h}")
        self.root.minsize(1100, 700)
        self.root.configure(bg=C["bg"])
        self.root.report_callback_exception = self._on_tk_error

        # image state
        self.original: Optional[Image.Image] = None
        self.exif: Optional[bytes] = None
        self.fg: Optional[Image.Image] = None
        self.bg = BgSettings()
        self.bg_cache: dict[str, Image.Image] = {}
        self.path: Optional[Path] = None
        self.history = HistoryManager(30)
        self.remover = BackgroundRemover(self.cfg["model"])
        self.runner = TaskRunner(self.root)
        self.busy = False

        # view state
        self.zoom: Optional[float] = None
        self.pan = [0.0, 0.0]
        self._view = (0.0, 0.0, 1.0)
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._render_job: Optional[str] = None
        self._last_mouse = (0, 0)
        self.crop_mode = False
        self.crop_rect: Optional[tuple[float, float, float, float]] = None
        self._crop_drag: Optional[tuple] = None
        self.adj_preview: Optional[Image.Image] = None
        self._adj_proxy: Optional[Image.Image] = None
        self._adj_job: Optional[str] = None
        self._resize_guard = False
        self._bg_thumb: Optional[ImageTk.PhotoImage] = None
        self.f_small = tkfont.Font(family=FONT, size=9, weight="bold")
        self.f_info = tkfont.Font(family=FONT, size=9)
        self._toast_msg: Optional[tuple[str, str]] = None
        self._toast_job: Optional[str] = None
        self._welcome_key: Optional[tuple[int, int]] = None
        self._welcome_photo: Optional[ImageTk.PhotoImage] = None
        self._strip_photo: Optional[ImageTk.PhotoImage] = None
        self._grip_drag: Optional[tuple[int, int]] = None
        self._side_hidden = False

        # widget groups for enable/disable
        self.w_open: list = []
        self.w_img: list = []
        self.w_undo: list = []
        self.w_redo: list = []

        self._build_vars()
        self._build_style()
        self._build_layout()
        self._build_menu()
        self._bind_keys()
        self._update_controls()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(50, self.render)

    # ----------------------------------------------------------------- setup
    def _build_vars(self) -> None:
        r = self.root
        self.v_before = tk.BooleanVar(r, False)
        self.v_split = tk.BooleanVar(r, False)
        self.split_frac = 0.5
        self.v_bgmode = tk.StringVar(r, "none")
        self.v_hex = tk.StringVar(r, "#FFFFFF")
        self.v_fit = tk.StringVar(r, "cover")
        self.v_px = tk.DoubleVar(r, 50)
        self.v_py = tk.DoubleVar(r, 50)
        self.v_bgpath = tk.StringVar(r, "No background image selected")
        self.adj = {k: tk.DoubleVar(r, 1.0) for k in ("Brightness", "Contrast", "Saturation", "Sharpness")}
        self.adj_lbl: dict[str, tk.StringVar] = {k: tk.StringVar(r, "1.00") for k in self.adj}
        self.v_rw = tk.StringVar(r, "")
        self.v_rh = tk.StringVar(r, "")
        self.v_lock = tk.BooleanVar(r, self.cfg["keep_aspect"])
        self.v_resize_info = tk.StringVar(r, "")
        self.v_preset = tk.StringVar(r, list(PRESETS)[0])
        self.v_pw = tk.StringVar(r, "1080")
        self.v_ph = tk.StringVar(r, "1080")
        self.v_pfit = tk.StringVar(r, list(PRESET_FITS)[0])
        self.v_aspect = tk.StringVar(r, "Free")
        self.v_crop_info = tk.StringVar(r, "")
        self.v_status = tk.StringVar(r, "Ready — open an image to begin.")
        self.v_right = tk.StringVar(r, "")
        self.v_zoom = tk.StringVar(r, "Fit")
        self.v_info = tk.StringVar(r, "No image loaded.")
        self.v_rw.trace_add("write", lambda *_: self._on_resize_edit("w"))
        self.v_rh.trace_add("write", lambda *_: self._on_resize_edit("h"))

    def _build_style(self) -> None:
        r = self.root
        s = ttk.Style(r)
        s.theme_use("clam")
        s.configure(".", background=C["bg"], foreground=C["text"], font=(FONT, 10),
                    bordercolor=C["border"], lightcolor=C["border"], darkcolor=C["border"],
                    troughcolor=C["elev"], focuscolor=C["accent"])
        s.configure("TFrame", background=C["bg"])
        s.configure("Side.TFrame", background=C["side"])
        s.configure("Panel.TFrame", background=C["panel"])
        s.configure("Card.TFrame", background=C["elev"])
        s.configure("Top.TFrame", background=C["side"])
        s.configure("TLabel", background=C["panel"], foreground=C["text"])
        s.configure("Muted.TLabel", background=C["panel"], foreground=C["muted"], font=(FONT, 9))
        s.configure("Head.TLabel", background=C["panel"], foreground=C["accent2"], font=(FONT, 9, "bold"))
        s.configure("Card.TLabel", background=C["elev"], foreground=C["text"], font=(FONT, 10))
        s.configure("SideHead.TLabel", background=C["side"], foreground=C["muted"], font=(FONT, 8, "bold"))
        s.configure("SideNote.TLabel", background=C["side"], foreground=C["muted"], font=(FONT, 9))
        s.configure("LogoA.TLabel", background=C["side"], foreground=C["accent"], font=(FONT, 20, "bold"))
        s.configure("LogoB.TLabel", background=C["side"], foreground=C["text"], font=(FONT, 14, "bold"))
        s.configure("LogoC.TLabel", background=C["side"], foreground=C["accent2"], font=(FONT, 14, "bold"))
        s.configure("Status.TLabel", background=C["side"], foreground=C["muted"], font=(FONT, 9))
        for name in ("TCheckbutton", "TRadiobutton"):
            s.configure(name, background=C["panel"], foreground=C["text"], padding=3)
            s.map(name, background=[("active", C["panel"])], foreground=[("disabled", "#5E6779")],
                  indicatorcolor=[("selected", C["accent"]), ("!selected", C["elev"])])
        s.configure("Top.TCheckbutton", background=C["side"])
        s.map("Top.TCheckbutton", background=[("active", C["side"])])
        s.configure("TEntry", fieldbackground=C["elev"], foreground=C["text"], insertcolor=C["text"], padding=5)
        s.configure("TSpinbox", fieldbackground=C["elev"], foreground=C["text"], arrowcolor=C["text"], padding=4)
        s.configure("TCombobox", fieldbackground=C["elev"], background=C["elev2"], foreground=C["text"],
                    arrowcolor=C["text"], padding=5)
        s.map("TCombobox", fieldbackground=[("readonly", C["elev"])], foreground=[("readonly", C["text"])])
        r.option_add("*TCombobox*Listbox.background", C["elev"])
        r.option_add("*TCombobox*Listbox.foreground", C["text"])
        r.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
        s.configure("Horizontal.TScale", background=C["accent"], troughcolor=C["elev"], bordercolor=C["panel"])
        s.map("Horizontal.TScale", background=[("active", C["accent_hi"]), ("disabled", C["elev2"])])
        s.configure("TNotebook", background=C["side"], borderwidth=0, tabmargins=(4, 6, 4, 0))
        s.configure("TNotebook.Tab", background=C["side"], foreground=C["muted"], padding=(10, 8),
                    font=(FONT, 9, "bold"), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", C["panel"]), ("active", C["elev"])],
              foreground=[("selected", C["accent2"]), ("active", C["text"])])
        s.configure("Horizontal.TProgressbar", background=C["accent2"], troughcolor=C["elev"], thickness=6)
        s.configure("Vertical.TScrollbar", background=C["elev2"], troughcolor=C["panel"], arrowcolor=C["muted"],
                    bordercolor=C["panel"], relief="flat")
        s.map("Vertical.TScrollbar", background=[("active", C["accent"])])

    def _btn(self, parent, text, cmd, style="TButton", group: Optional[list] = None,
             bg: Optional[str] = None, width: Optional[int] = None, height: int = 34, **_ignored):
        kind = {"Accent.TButton": "accent", "Side.TButton": "side"}.get(style, "normal")
        if bg is None:
            bg = C["side"] if kind == "side" else C["panel"]
        b = RoundButton(parent, text, cmd, kind=kind, bg=bg, width=width, height=height,
                        anchor="w" if kind == "side" else "center")
        if group is not None:
            group.append(b)
        return b

    def _section(self, parent, title: str, first: bool = False) -> None:
        f = ttk.Frame(parent, style="Panel.TFrame")
        f.pack(fill="x", pady=(0 if first else 16, 6))
        tk.Frame(f, bg=C["accent2"], width=3, height=14).pack(side="left", padx=(0, 8))
        ttk.Label(f, text=title, style="Head.TLabel").pack(side="left")
        tk.Frame(f, bg=C["border"], height=1).pack(side="left", fill="x", expand=True, padx=(10, 0))

    def _build_layout(self) -> None:
        r = self.root
        S = C["side"]
        # ---- top bar
        top = ttk.Frame(r, style="Top.TFrame")
        top.pack(side="top", fill="x")
        ttk.Label(top, text="◆", style="LogoA.TLabel").pack(side="left", padx=(16, 6), pady=6)
        ttk.Label(top, text="ZaBir", style="LogoB.TLabel").pack(side="left")
        ttk.Label(top, text=" ImageLab", style="LogoC.TLabel").pack(side="left", padx=(0, 24))
        self._btn(top, "▣  Open", self.open_dialog, group=self.w_open, bg=S).pack(side="left", padx=3, pady=8)
        self._btn(top, "⤓  Export", self.export_image, group=self.w_img, bg=S).pack(side="left", padx=3)
        self._btn(top, "↶  Undo", self.undo, group=self.w_undo, bg=S).pack(side="left", padx=(16, 3))
        self._btn(top, "↷  Redo", self.redo, group=self.w_redo, bg=S).pack(side="left", padx=3)
        self._btn(top, "✨  Remove BG", self.remove_background, "Accent.TButton", self.w_img,
                  bg=S, width=210).pack(side="left", padx=(20, 3))
        c1 = ttk.Checkbutton(top, text="Before", variable=self.v_before, style="Top.TCheckbutton", command=self.render)
        c2 = ttk.Checkbutton(top, text="Split compare", variable=self.v_split, style="Top.TCheckbutton",
                             command=self.render)
        self.w_img += [c1, c2]
        c1.pack(side="left", padx=(20, 4))
        c2.pack(side="left", padx=4)
        ttk.Label(top, textvariable=self.v_zoom, style="Status.TLabel", width=11, anchor="e").pack(side="right", padx=12)
        for text, cmd, wd in (("100%", self.actual_size, 56), ("Fit", self.fit_view, 48),
                              ("−", lambda: self._zoom_by(0.8), 38), ("+", lambda: self._zoom_by(1.25), 38)):
            self._btn(top, text, cmd, group=self.w_img, bg=S, width=wd).pack(side="right", padx=2)
        # gradient accent strip under the top bar
        self.strip = tk.Canvas(r, height=3, bg=C["accent"], highlightthickness=0, bd=0)
        self.strip.pack(side="top", fill="x")
        self.strip.bind("<Configure>", self._strip_resize)

        # ---- status bar
        bar = ttk.Frame(r, style="Top.TFrame")
        bar.pack(side="bottom", fill="x")
        self.dot = tk.Canvas(bar, width=12, height=12, bg=S, highlightthickness=0, bd=0)
        self._dot_item = self.dot.create_oval(2, 2, 10, 10, fill=C["accent2"], outline="")
        self.dot.pack(side="left", padx=(14, 4), pady=8)
        self.status_lbl = ttk.Label(bar, textvariable=self.v_status, style="Status.TLabel")
        self.status_lbl.pack(side="left", pady=6)
        ttk.Label(bar, textvariable=self.v_right, style="Status.TLabel").pack(side="right", padx=14)
        self.cancel_btn = self._btn(bar, "Cancel", self.cancel_task, bg=S, width=80, height=26)
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=170)

        # ---- sidebar (user-resizable: drag the edge, use -/+ or View menu)
        self.side = ttk.Frame(r, style="Side.TFrame", width=self.cfg["sidebar_width"])
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)
        self.grip = tk.Frame(r, width=6, bg=C["border"], cursor="sb_h_double_arrow")
        self.grip.pack(side="left", fill="y")
        self.grip.bind("<Enter>", lambda e: self.grip.configure(bg=C["accent"]))
        self.grip.bind("<Leave>", lambda e: None if self._grip_drag else self.grip.configure(bg=C["border"]))
        self.grip.bind("<ButtonPress-1>", self._grip_press)
        self.grip.bind("<B1-Motion>", self._grip_move)
        self.grip.bind("<ButtonRelease-1>", self._grip_release)
        self.grip.bind("<Double-Button-1>", lambda e: self.set_sidebar_width(DEFAULT_CONFIG["sidebar_width"]))
        sf = ScrollableFrame(self.side, bg=S, style="Side.TFrame", padding=(0, 0))
        sf.pack(fill="both", expand=True)
        body = sf.body
        head = ttk.Frame(body, style="Side.TFrame")
        head.pack(fill="x", padx=14, pady=(14, 0))
        ttk.Label(head, text="TOOLS", style="LogoB.TLabel").pack(side="left")
        self._btn(head, "+", lambda: self.set_sidebar_width(self.side.winfo_width() + 30), bg=S,
                  width=30, height=26).pack(side="right", padx=(4, 0))
        self._btn(head, "−", lambda: self.set_sidebar_width(self.side.winfo_width() - 30), bg=S,
                  width=30, height=26).pack(side="right")
        ttk.Label(body, text="Drag the edge to resize", style="SideNote.TLabel").pack(anchor="w", padx=16, pady=(2, 0))
        groups = [
            ("FILE", [("▣  Open Image", self.open_dialog, self.w_open, "Side.TButton"),
                      ("⤓  Export…", self.export_image, self.w_img, "Side.TButton")]),
            ("AI", [("✨  Remove BG", self.remove_background, self.w_img, "Accent.TButton"),
                    ("⟳  Reprocess (original)", lambda: self.remove_background(True), self.w_img, "Side.TButton"),
                    ("↺  Restore Original", self.restore_original, self.w_img, "Side.TButton")]),
            ("TRANSFORM", [("✂  Crop", self.toggle_crop, self.w_img, "Side.TButton"),
                           ("⟲  Rotate Left 90°", lambda: self.geometry("rot90"), self.w_img, "Side.TButton"),
                           ("⟳  Rotate Right 90°", lambda: self.geometry("rot270"), self.w_img, "Side.TButton"),
                           ("↻  Rotate 180°", lambda: self.geometry("rot180"), self.w_img, "Side.TButton"),
                           ("⇋  Flip Horizontal", lambda: self.geometry("fliph"), self.w_img, "Side.TButton"),
                           ("⇵  Flip Vertical", lambda: self.geometry("flipv"), self.w_img, "Side.TButton")]),
        ]
        for title, items in groups:
            ttk.Label(body, text=title, style="SideHead.TLabel").pack(anchor="w", padx=18, pady=(16, 5))
            for text, cmd, grp, st in items:
                self._btn(body, text, cmd, st, grp, bg=S).pack(fill="x", padx=10, pady=1)
        ttk.Label(body, text="Rotate/flip keep transparency.\n“Restore Original” resets all\nedits and backgrounds.",
                  style="SideNote.TLabel", justify="left").pack(anchor="w", padx=18, pady=16)

        # ---- right panel
        right = ttk.Frame(r, style="Panel.TFrame", width=348)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True)
        for title, builder in (("◐ Background", self._tab_background), ("☀ Adjust", self._tab_adjust),
                               ("⤢ Resize", self._tab_resize), ("▭ Presets", self._tab_presets),
                               ("ⓘ Info", self._tab_info)):
            sf = ScrollableFrame(nb)
            nb.add(sf, text=title)
            builder(sf.body)

        # ---- centre workspace
        self.centre = centre = ttk.Frame(r)
        centre.pack(side="left", fill="both", expand=True)
        self.crop_bar = ttk.Frame(centre, style="Top.TFrame")
        ttk.Label(self.crop_bar, text="Crop ratio:", style="Status.TLabel").pack(side="left", padx=(12, 6), pady=6)
        cb = ttk.Combobox(self.crop_bar, textvariable=self.v_aspect, values=list(ASPECTS), width=7, state="readonly")
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._crop_ratio_changed())
        ttk.Label(self.crop_bar, textvariable=self.v_crop_info, style="Status.TLabel").pack(side="left", padx=14)
        self._btn(self.crop_bar, "Cancel", self.end_crop, bg=S, width=80, height=30).pack(side="right", padx=8, pady=4)
        self._btn(self.crop_bar, "Apply Crop", self.apply_crop, "Accent.TButton", bg=S, width=110,
                  height=30).pack(side="right", pady=4)
        self.canvas = tk.Canvas(centre, bg=C["bg"], highlightthickness=0, cursor="arrow")
        self.canvas.pack(fill="both", expand=True)
        self.welcome_btn = RoundButton(self.canvas, "▣  Open Image", self.open_dialog, "accent",
                                       bg=hex_mix(C["bg"], C["accent"], GLOW), width=200, height=46, radius=14)
        self.w_open.append(self.welcome_btn)
        c = self.canvas
        c.bind("<Configure>", lambda e: self._schedule_render())
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Button-4>", lambda e: self._zoom_by(1.1))
        c.bind("<Button-5>", lambda e: self._zoom_by(1 / 1.1))
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        for b in ("2", "3"):
            c.bind(f"<ButtonPress-{b}>", self._pan_start)
            c.bind(f"<B{b}-Motion>", self._pan_move)
        if HAS_DND:
            try:
                c.drop_target_register(DND_FILES)
                c.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                log.warning("Drag & drop registration failed")

    def _sidebar_limits(self) -> tuple[int, int]:
        room = self.root.winfo_width() - 348 - 420  # keep space for properties panel + workspace
        return 150, max(150, min(420, room))

    def set_sidebar_width(self, w: int) -> None:
        """Set the Tools sidebar width (clamped) and remember it."""
        lo, hi = self._sidebar_limits()
        w = int(min(max(w, lo), hi))
        self.cfg["sidebar_width"] = w
        if self._side_hidden:
            self.toggle_sidebar()
        self.side.configure(width=w)

    def toggle_sidebar(self) -> None:
        if self._side_hidden:
            self.side.pack(side="left", fill="y", before=self.centre)
            self.grip.pack(side="left", fill="y", before=self.centre)
        else:
            self.side.pack_forget()
            self.grip.pack_forget()
        self._side_hidden = not self._side_hidden

    def _grip_press(self, e) -> None:
        self._grip_drag = (e.x_root, self.side.winfo_width())
        self.grip.configure(bg=C["accent"])

    def _grip_move(self, e) -> None:
        if self._grip_drag:
            x0, w0 = self._grip_drag
            self.set_sidebar_width(w0 + e.x_root - x0)

    def _grip_release(self, _e) -> None:
        self._grip_drag = None
        self.grip.configure(bg=C["border"])
        save_config(self.cfg)

    def _strip_resize(self, e) -> None:
        if e.width < 2:
            return
        self._strip_photo = ImageTk.PhotoImage(make_gradient(e.width, 3, C["accent"], C["accent2"]))
        self.strip.delete("all")
        self.strip.create_image(0, 0, image=self._strip_photo, anchor="nw")

    # ---- tabs
    def _lbl(self, parent, text, style="TLabel", **kw):
        return ttk.Label(parent, text=text, style=style, **kw)

    def _tab_background(self, f: ttk.Frame) -> None:
        self._section(f, "BACKGROUND MODE", first=True)
        for text, val in (("Transparent", "none"), ("Solid color", "color"), ("Image", "image")):
            rb = ttk.Radiobutton(f, text=text, value=val, variable=self.v_bgmode, command=self._bg_mode_changed)
            rb.pack(anchor="w", pady=1)
            self.w_img.append(rb)
        self._section(f, "SOLID COLOR")
        row = ttk.Frame(f, style="Panel.TFrame")
        row.pack(fill="x")
        self.swatch = tk.Label(row, width=3, bg="#FFFFFF", relief="flat")
        self.swatch.pack(side="left", padx=(0, 8), ipady=4)
        e = ttk.Entry(row, textvariable=self.v_hex, width=9)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: self._apply_hex())
        self.w_img.append(e)
        self._btn(row, "Apply", self._apply_hex, group=self.w_img, width=60, height=30).pack(side="left", padx=5)
        self._btn(row, "Pick…", self._pick_color, group=self.w_img, width=60, height=30).pack(side="left")
        grid = ttk.Frame(f, style="Panel.TFrame")
        grid.pack(anchor="w", pady=8)
        for i, col in enumerate(PALETTE):
            sw = Swatch(grid, col, lambda c=col: self._bg_set("Background color", mode="color", color=c))
            sw.grid(row=i // 6, column=i % 6, padx=2, pady=2)
            self.w_img.append(sw)
        self._section(f, "BACKGROUND IMAGE")
        self._btn(f, "Browse background image…", self._browse_bg, group=self.w_img).pack(fill="x")
        self.thumb = tk.Label(f, bg=C["elev"], height=5, fg=C["muted"], text="(no preview)")
        self.thumb.pack(fill="x", pady=6)
        self._lbl(f, "", "Muted.TLabel", textvariable=self.v_bgpath, wraplength=290).pack(anchor="w")
        fr = ttk.Frame(f, style="Panel.TFrame")
        fr.pack(fill="x", pady=6)
        self._lbl(fr, "Fit mode").pack(side="left")
        cb = ttk.Combobox(fr, textvariable=self.v_fit, values=BG_FITS, width=10, state="readonly")
        cb.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda e: self._bg_set("Background fit", fit=self.v_fit.get()))
        self.w_img.append(cb)
        for label, var, key in (("Position X", self.v_px, "x"), ("Position Y", self.v_py, "y")):
            r = ttk.Frame(f, style="Panel.TFrame")
            r.pack(fill="x", pady=2)
            self._lbl(r, label, "Muted.TLabel", width=11).pack(side="left")
            sc = ttk.Scale(r, from_=0, to=100, variable=var, command=lambda v, k=key: self._bg_pos_live(k))
            sc.pack(side="left", fill="x", expand=True)
            sc.bind("<ButtonRelease-1>", lambda e: self._bg_pos_commit())
            self.w_img.append(sc)
        self._btn(f, "Remove background image", lambda: self._bg_set("Remove bg image", mode="none"),
                  group=self.w_img).pack(fill="x", pady=(10, 4))
        self._btn(f, "Reset Background", self.reset_background, group=self.w_img).pack(fill="x")

    def _tab_adjust(self, f: ttk.Frame) -> None:
        self._section(f, "BASIC ADJUSTMENTS", first=True)
        for name, var in self.adj.items():
            r = ttk.Frame(f, style="Panel.TFrame")
            r.pack(fill="x", pady=5)
            self._lbl(r, name, width=11).pack(side="left")
            ttk.Label(r, textvariable=self.adj_lbl[name], style="Muted.TLabel", width=5).pack(side="right")
            sc = ttk.Scale(r, from_=0.0, to=2.0, variable=var, command=lambda v, n=name: self._adj_changed(n))
            sc.pack(side="left", fill="x", expand=True)
            self.w_img.append(sc)
        self._btn(f, "Apply Adjustments", self.apply_adjust, "Accent.TButton", self.w_img).pack(fill="x", pady=(16, 6))
        self._btn(f, "Reset Adjustments", self.reset_adjust, group=self.w_img).pack(fill="x")
        self._lbl(f, "The preview uses a reduced copy for speed;\nApply renders at full resolution from the\ncurrent image, so quality is not lost by\nmoving sliders repeatedly.",
                  "Muted.TLabel", justify="left").pack(anchor="w", pady=14)

    def _tab_resize(self, f: ttk.Frame) -> None:
        self._section(f, "RESIZE IMAGE", first=True)
        for label, var in (("Width (px)", self.v_rw), ("Height (px)", self.v_rh)):
            r = ttk.Frame(f, style="Panel.TFrame")
            r.pack(fill="x", pady=4)
            self._lbl(r, label, width=11).pack(side="left")
            e = ttk.Entry(r, textvariable=var, width=10)
            e.pack(side="left")
            self.w_img.append(e)
        ck = ttk.Checkbutton(f, text="Lock aspect ratio", variable=self.v_lock)
        ck.pack(anchor="w", pady=6)
        self.w_img.append(ck)
        card = tk.Frame(f, bg=C["elev"], padx=12, pady=10)
        card.pack(fill="x", pady=6)
        ttk.Label(card, textvariable=self.v_resize_info, style="Card.TLabel", justify="left").pack(anchor="w")
        self._btn(f, "Apply Resize (Lanczos)", self.apply_resize, "Accent.TButton", self.w_img).pack(fill="x", pady=8)

    def _tab_presets(self, f: ttk.Frame) -> None:
        self._section(f, "SOCIAL MEDIA PRESETS", first=True)
        cb = ttk.Combobox(f, textvariable=self.v_preset, values=list(PRESETS), state="readonly")
        cb.pack(fill="x")
        cb.bind("<<ComboboxSelected>>", lambda e: self._preset_changed())
        self.w_img.append(cb)
        r = ttk.Frame(f, style="Panel.TFrame")
        r.pack(fill="x", pady=10)
        for label, var in (("W", self.v_pw), ("H", self.v_ph)):
            self._lbl(r, label, "Muted.TLabel").pack(side="left", padx=(0, 4))
            e = ttk.Entry(r, textvariable=var, width=7)
            e.pack(side="left", padx=(0, 12))
            self.w_img.append(e)
        self._section(f, "FIT MODE")
        cb2 = ttk.Combobox(f, textvariable=self.v_pfit, values=list(PRESET_FITS), state="readonly")
        cb2.pack(fill="x", pady=2)
        self.w_img.append(cb2)
        self._btn(f, "Apply Preset", self.apply_preset, "Accent.TButton", self.w_img).pack(fill="x", pady=12)
        self._lbl(f, "Sizes are editable. Transparency is kept;\n“Contain” pads with transparent pixels\n(fill them via the Background tab).\nUse Export to save the result.",
                  "Muted.TLabel", justify="left").pack(anchor="w")

    def _tab_info(self, f: ttk.Frame) -> None:
        self._section(f, "IMAGE INFORMATION", first=True)
        card = tk.Frame(f, bg=C["elev"], padx=14, pady=12)
        card.pack(fill="x")
        ttk.Label(card, textvariable=self.v_info, style="Card.TLabel", justify="left", wraplength=280).pack(anchor="w")

    # ---- menu
    def _build_menu(self) -> None:
        m = tk.Menu(self.root)
        self.root.config(menu=m)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="Open Image…", accelerator="Ctrl+O", command=self.open_dialog)
        self.recent_menu = tk.Menu(fm, tearoff=0)
        fm.add_cascade(label="Recent Files", menu=self.recent_menu)
        fm.add_command(label="Export…", accelerator="Ctrl+S", command=self.export_image)
        fm.add_separator()
        fm.add_command(label="Quit", command=self._on_close)
        m.add_cascade(label="File", menu=fm)
        em = tk.Menu(m, tearoff=0)
        em.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo)
        em.add_command(label="Redo", accelerator="Ctrl+Y", command=self.redo)
        em.add_separator()
        em.add_command(label="Remove Background", command=self.remove_background)
        em.add_command(label="Reprocess from Original", command=lambda: self.remove_background(True))
        em.add_command(label="Restore Original", command=self.restore_original)
        em.add_separator()
        em.add_command(label="AI Model & Preview Settings…", command=self.open_settings)
        m.add_cascade(label="Edit", menu=em)
        self.edit_menu = em
        vm = tk.Menu(m, tearoff=0)
        vm.add_command(label="Fit to Window", accelerator="Ctrl+0", command=self.fit_view)
        vm.add_command(label="Zoom In", accelerator="Ctrl++", command=lambda: self._zoom_by(1.25))
        vm.add_command(label="Zoom Out", accelerator="Ctrl+-", command=lambda: self._zoom_by(0.8))
        vm.add_command(label="Actual Size (100%)", command=self.actual_size)
        vm.add_separator()
        vm.add_command(label="Toggle Tools Sidebar", accelerator="Ctrl+B", command=self.toggle_sidebar)
        sm = tk.Menu(vm, tearoff=0)
        for label, wpx in (("Compact (170 px)", 170), ("Normal (220 px)", 220),
                           ("Wide (300 px)", 300), ("Extra wide (380 px)", 380)):
            sm.add_command(label=label, command=lambda v=wpx: self.set_sidebar_width(v))
        vm.add_cascade(label="Tools Sidebar Width", menu=sm)
        m.add_cascade(label="View", menu=vm)
        hm = tk.Menu(m, tearoff=0)
        hm.add_command(label="About", command=self._about)
        hm.add_command(label="Show Log File Location", command=lambda: messagebox.showinfo(APP_NAME, f"Log file:\n{LOG_PATH}"))
        m.add_cascade(label="Help", menu=hm)
        self._rebuild_recent()

    def _rebuild_recent(self) -> None:
        self.recent_menu.delete(0, "end")
        items = [p for p in self.cfg["recent"] if Path(p).exists()]
        if not items:
            self.recent_menu.add_command(label="(empty)", state="disabled")
        for p in items:
            self.recent_menu.add_command(label=p, command=lambda q=p: self.open_path(q))

    def _bind_keys(self) -> None:
        r = self.root
        r.bind("<Control-o>", lambda e: self.open_dialog())
        r.bind("<Control-s>", lambda e: self.export_image())
        r.bind("<Control-z>", lambda e: self.undo())
        r.bind("<Control-y>", lambda e: self.redo())
        r.bind("<Control-Shift-Z>", lambda e: self.redo())
        r.bind("<Control-0>", lambda e: self.fit_view())
        r.bind("<Control-plus>", lambda e: self._zoom_by(1.25))
        r.bind("<Control-equal>", lambda e: self._zoom_by(1.25))
        r.bind("<Control-minus>", lambda e: self._zoom_by(0.8))
        r.bind("<Escape>", lambda e: self.end_crop())
        r.bind("<Control-b>", lambda e: self.toggle_sidebar())

    # ------------------------------------------------------------- utilities
    def _on_tk_error(self, exc, val, tb) -> None:
        log.error("Unhandled UI error", exc_info=(exc, val, tb))
        messagebox.showerror(APP_NAME, "An unexpected error occurred. Details were written to:\n"
                             f"{LOG_PATH}\n\n{val}")

    def status(self, msg: str, kind: str = "info") -> None:
        self.v_status.set(msg)
        col = {"ok": C["ok"], "err": C["err"]}.get(kind, C["accent2"])
        self.status_lbl.configure(foreground=C["text"] if kind == "info" else col)
        self.dot.itemconfigure(self._dot_item, fill=col)
        if kind in ("ok", "err"):
            self._toast(msg, kind)

    def _need_image(self) -> bool:
        if self.fg is None:
            messagebox.showinfo(APP_NAME, "Please open an image first.")
            return False
        return True

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self.busy = busy
        if busy:
            self.progress.pack(side="right", padx=6)
            self.cancel_btn.pack(side="right", padx=4)
            self.progress.start(12)
            self.root.configure(cursor="watch")
            self.status(msg)
        else:
            self.progress.stop()
            self.progress.pack_forget()
            self.cancel_btn.pack_forget()
            self.root.configure(cursor="")
        self._update_controls()

    def _update_controls(self) -> None:
        has, busy = self.fg is not None, self.busy

        def setw(w, en: bool) -> None:
            try:
                if isinstance(w, tk.Button):
                    w.configure(state="normal" if en else "disabled")
                else:
                    w.state(["!disabled"] if en else ["disabled"])
            except tk.TclError:
                pass

        for w in self.w_open:
            setw(w, not busy)
        for w in self.w_img:
            setw(w, has and not busy)
        for w in self.w_undo:
            setw(w, self.history.can_undo and not busy)
        for w in self.w_redo:
            setw(w, self.history.can_redo and not busy)
        for label, en in (("Undo", self.history.can_undo), ("Redo", self.history.can_redo)):
            self.edit_menu.entryconfig(label, state="normal" if en and not busy else "disabled")
        for label in ("Remove Background", "Reprocess from Original", "Restore Original"):
            self.edit_menu.entryconfig(label, state="normal" if has and not busy else "disabled")

    def _update_info(self) -> None:
        if self.fg is None or self.original is None:
            self.v_info.set("No image loaded.")
            self.v_right.set("")
            return
        fw, fh = self.fg.size
        ow, oh = self.original.size
        self.v_info.set(
            f"File: {self.path.name if self.path else '-'}\n"
            f"Original: {ow} × {oh} px, RGBA (converted)\n"
            f"Working: {fw} × {fh} px, mode {self.fg.mode}\n"
            f"Has transparency: {'yes' if has_transparency(self.fg) else 'no'}\n"
            f"Background layer: {self.bg.mode}\n"
            f"History states: {len(self.history.states)}")
        self.v_right.set(f"{fw} × {fh} px  •  {self.fg.mode}")
        self._sync_resize_fields()

    # ------------------------------------------------------------ open image
    def open_dialog(self) -> None:
        if self.busy:
            return
        p = filedialog.askopenfilename(
            title="Open Image", filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff"), ("All files", "*.*")])
        if p:
            self.open_path(p)

    def open_path(self, p: str) -> None:
        if self.busy:
            return
        path = Path(p)
        try:
            if not path.is_file():
                raise FileNotFoundError(str(path))
            self.root.configure(cursor="watch")
            self.root.update_idletasks()
            img, exif = load_image(path)
        except FileNotFoundError:
            messagebox.showerror(APP_NAME, f"File not found:\n{path}")
            return
        except MemoryError:
            messagebox.showerror(APP_NAME, "Not enough memory to open this image.")
            return
        except Exception as exc:
            log.exception("Open failed")
            messagebox.showerror(APP_NAME, f"This file could not be opened as an image.\n\n{path.name}\n{exc}")
            return
        finally:
            self.root.configure(cursor="")
        self.original, self.exif, self.fg, self.path = img, exif, img, path
        self.bg = BgSettings()
        self.end_crop()
        self._clear_adjust_preview()
        self.history.reset(Snapshot(img, self.bg, "Open"))
        self.v_before.set(False)
        self.v_split.set(False)
        self.fit_view(render=False)
        self._sync_bg_widgets()
        recent = [str(path)] + [r for r in self.cfg["recent"] if r != str(path)]
        self.cfg["recent"] = recent[:10]
        self._rebuild_recent()
        self.root.title(f"{path.name} — {WINDOW_TITLE}")
        self._update_info()
        self._update_controls()
        self.render()
        self.status(f"Opened {path.name} ({img.width} × {img.height})", "ok")

    def _on_drop(self, event) -> None:
        files = self.root.tk.splitlist(event.data)
        if files:
            self.open_path(files[0])

    # ------------------------------------------------------------- history
    def commit(self, label: str) -> None:
        assert self.fg is not None
        self._clear_adjust_preview()
        self.history.push(Snapshot(self.fg, self.bg, label))
        self._update_info()
        self._update_controls()
        self.render()
        self.status(label, "ok")

    def _restore(self, snap: Snapshot, verb: str) -> None:
        self.fg, self.bg = snap.fg, snap.bg
        self.end_crop()
        self._clear_adjust_preview()
        self._sync_bg_widgets()
        self._update_info()
        self._update_controls()
        self.render()
        self.status(f"{verb}: {snap.label}")

    def undo(self) -> None:
        if self.busy:
            return
        s = self.history.undo()
        if s:
            self._restore(s, "Undo")

    def redo(self) -> None:
        if self.busy:
            return
        s = self.history.redo()
        if s:
            self._restore(s, "Redo")

    def restore_original(self) -> None:
        if self.original is None or self.busy:
            return
        self.fg, self.bg = self.original, BgSettings()
        self._sync_bg_widgets()
        self.commit("Restored original")

    # ------------------------------------------------------ AI background removal
    def remove_background(self, from_original: bool = False) -> None:
        if not self._need_image() or self.busy:
            return
        self.end_crop()
        src = self.original if from_original else self.fg
        assert src is not None
        self._set_busy(True, "Running AI background removal… (first run may download the model)")
        self.runner.submit(lambda: self.remover.remove(src),
                           lambda res: self._bg_done(res, src, from_original), self._bg_error)

    def _bg_done(self, result: Image.Image, src: Image.Image, from_original: bool) -> None:
        self._set_busy(False)
        if result.getchannel("A").getextrema()[0] == 255:
            messagebox.showwarning(APP_NAME, "The AI model did not find any background to remove, "
                                   "so the image was left unchanged.\nTry another model under Edit → AI Model.")
            self.status("No background detected — image unchanged.", "err")
            return
        self.fg = result
        self.commit("Background removed")

    def _bg_error(self, exc: BaseException) -> None:
        self._set_busy(False)
        msg = str(exc) if isinstance(exc, BackgroundRemovalError) else f"Background removal failed:\n{exc}"
        self.status("Background removal failed.", "err")
        messagebox.showerror(APP_NAME, msg)

    def cancel_task(self) -> None:
        self.runner.cancel()
        self._set_busy(False)
        self.status("Cancelled — the running model call will finish silently and its result is discarded.")

    def open_settings(self) -> None:
        d = tk.Toplevel(self.root)
        d.title("AI Model & Preview Settings")
        d.configure(bg=C["panel"])
        d.transient(self.root)
        d.resizable(False, False)
        f = ttk.Frame(d, style="Panel.TFrame", padding=16)
        f.pack()
        model = tk.StringVar(d, self.remover.model)
        chk = tk.IntVar(d, self.cfg["checker_size"])
        ttk.Label(f, text="Background-removal model").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=model, values=MODELS, state="readonly", width=22).grid(row=0, column=1, padx=8)
        ttk.Label(f, text="Checkerboard square size").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=4, to=64, textvariable=chk, width=6).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(f, text="u2net = best quality, u2netp = fastest, silueta = small,\n"
                          "isnet-general-use = general purpose, u2net_human_seg = people.",
                  style="Muted.TLabel", justify="left").grid(row=2, column=0, columnspan=2, sticky="w", pady=6)

        def ok() -> None:
            try:
                size = int(chk.get())
            except (tk.TclError, ValueError):
                size = 12
            self.cfg["model"] = model.get()
            self.cfg["checker_size"] = max(4, min(64, size))
            self.remover.model = self.cfg["model"]
            save_config(self.cfg)
            d.destroy()
            self.render()
            self.status(f"AI model set to {self.remover.model}", "ok")

        self._btn(f, "Save", ok, "Accent.TButton", width=90).grid(row=3, column=1, sticky="e", pady=8)

    # ------------------------------------------------------- geometry edits
    def geometry(self, op: str) -> None:
        if not self._need_image() or self.busy:
            return
        self.end_crop()
        T = Image.Transpose
        ops = {"rot90": (T.ROTATE_90, "Rotated left 90°"), "rot270": (T.ROTATE_270, "Rotated right 90°"),
               "rot180": (T.ROTATE_180, "Rotated 180°"), "fliph": (T.FLIP_LEFT_RIGHT, "Flipped horizontally"),
               "flipv": (T.FLIP_TOP_BOTTOM, "Flipped vertically")}
        method, label = ops[op]
        self.fg = self.fg.transpose(method)  # type: ignore[union-attr]
        self.commit(label)

    def apply_resize(self) -> None:
        if not self._need_image() or self.busy:
            return
        try:
            w, h = int(self.v_rw.get()), int(self.v_rh.get())
            if not (1 <= w <= MAX_DIM and 1 <= h <= MAX_DIM):
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_NAME, f"Width and height must be whole numbers between 1 and {MAX_DIM}.")
            return
        assert self.fg is not None
        if (w, h) == self.fg.size:
            self.status("Image already has that size.")
            return
        self.end_crop()
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            self.fg = self.fg.resize((w, h), LANCZOS)
        except MemoryError:
            messagebox.showerror(APP_NAME, "Not enough memory for that size.")
            return
        finally:
            self.root.configure(cursor="")
        self.commit(f"Resized to {w} × {h}")

    def _sync_resize_fields(self) -> None:
        if self.fg is None:
            return
        self._resize_guard = True
        self.v_rw.set(str(self.fg.width))
        self.v_rh.set(str(self.fg.height))
        self._resize_guard = False
        self.v_resize_info.set(f"Original: {self.fg.width} × {self.fg.height}\nNew: {self.fg.width} × {self.fg.height}")

    def _on_resize_edit(self, which: str) -> None:
        if self._resize_guard or self.fg is None:
            return
        try:
            w, h = self.fg.size
            if self.v_lock.get():
                self._resize_guard = True
                if which == "w" and self.v_rw.get().strip():
                    self.v_rh.set(str(max(1, round(int(self.v_rw.get()) * h / w))))
                elif which == "h" and self.v_rh.get().strip():
                    self.v_rw.set(str(max(1, round(int(self.v_rh.get()) * w / h))))
                self._resize_guard = False
            self.v_resize_info.set(f"Original: {w} × {h}\nNew: {self.v_rw.get()} × {self.v_rh.get()}")
        except ValueError:
            self._resize_guard = False
            self.v_resize_info.set("Please enter whole numbers.")

    def _preset_changed(self) -> None:
        size = PRESETS.get(self.v_preset.get())
        if size:
            self.v_pw.set(str(size[0]))
            self.v_ph.set(str(size[1]))

    def apply_preset(self) -> None:
        if not self._need_image() or self.busy:
            return
        try:
            w, h = int(self.v_pw.get()), int(self.v_ph.get())
            if not (1 <= w <= MAX_DIM and 1 <= h <= MAX_DIM):
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_NAME, f"Preset width/height must be whole numbers between 1 and {MAX_DIM}.")
            return
        self.end_crop()
        mode = PRESET_FITS[self.v_pfit.get()]
        self.fg = fit_image(self.fg, (w, h), mode)  # type: ignore[arg-type]
        self.commit(f"Preset {w} × {h} ({mode})")

    # --------------------------------------------------------------- crop
    def toggle_crop(self) -> None:
        if not self._need_image() or self.busy:
            return
        if self.crop_mode:
            self.end_crop()
            return
        self.v_split.set(False)
        self.v_before.set(False)
        self.crop_mode = True
        self.crop_rect = (0.0, 0.0, float(self.fg.width), float(self.fg.height))  # type: ignore[union-attr]
        self.crop_bar.pack(side="top", fill="x", before=self.canvas)
        self.canvas.configure(cursor="crosshair")
        self._crop_ratio_changed()
        self.status("Drag to draw a crop box; drag corners to resize, drag inside to move.")

    def end_crop(self) -> None:
        if self.crop_mode:
            self.crop_mode = False
            self.crop_rect = None
            self.crop_bar.pack_forget()
            self.canvas.configure(cursor="arrow")
            self.render()

    def _crop_ratio_changed(self) -> None:
        if not self.crop_mode or self.crop_rect is None:
            return
        ratio = ASPECTS[self.v_aspect.get()]
        x0, y0, x1, y1 = self.crop_rect
        if ratio:
            w, h = x1 - x0, y1 - y0
            if w > h * ratio:
                w = h * ratio
            else:
                h = w / ratio
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            x0, x1, y0, y1 = cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2
        self.crop_rect = (x0, y0, x1, y1)
        self.render()

    def _rect_from(self, ax: float, ay: float, mx: float, my: float) -> tuple[float, float, float, float]:
        W, H = self.fg.size  # type: ignore[union-attr]
        mx, my = min(max(mx, 0), W), min(max(my, 0), H)
        dx, dy = mx - ax, my - ay
        ratio = ASPECTS[self.v_aspect.get()]
        if ratio:
            sx, sy = (1 if dx >= 0 else -1), (1 if dy >= 0 else -1)
            w = min(abs(dx), (W - ax) if sx > 0 else ax)
            h = min(abs(dy), (H - ay) if sy > 0 else ay)
            if w > h * ratio:
                w = h * ratio
            else:
                h = w / ratio
            mx, my = ax + sx * w, ay + sy * h
        return min(ax, mx), min(ay, my), max(ax, mx), max(ay, my)

    def _crop_corners_canvas(self) -> dict[str, tuple[float, float]]:
        x0, y0, x1, y1 = self.crop_rect  # type: ignore[misc]
        L, T, z = self._view
        return {"tl": (L + x0 * z, T + y0 * z), "tr": (L + x1 * z, T + y0 * z),
                "bl": (L + x0 * z, T + y1 * z), "br": (L + x1 * z, T + y1 * z)}

    def _crop_press(self, e) -> None:
        ix, iy = self._to_img(e.x, e.y)
        r = self.crop_rect
        self._crop_drag = None
        if r:
            for name, (cx, cy) in self._crop_corners_canvas().items():
                if abs(e.x - cx) <= 12 and abs(e.y - cy) <= 12:
                    x0, y0, x1, y1 = r
                    anchor = {"tl": (x1, y1), "tr": (x0, y1), "bl": (x1, y0), "br": (x0, y0)}[name]
                    self._crop_drag = ("rect", anchor)
                    return
            if r[0] <= ix <= r[2] and r[1] <= iy <= r[3]:
                self._crop_drag = ("move", (ix, iy, r))
                return
        W, H = self.fg.size  # type: ignore[union-attr]
        self._crop_drag = ("rect", (min(max(ix, 0), W), min(max(iy, 0), H)))
        self.crop_rect = (ix, iy, ix, iy)

    def _crop_motion(self, e) -> None:
        if not self._crop_drag:
            return
        ix, iy = self._to_img(e.x, e.y)
        kind, data = self._crop_drag
        if kind == "rect":
            self.crop_rect = self._rect_from(data[0], data[1], ix, iy)
        else:
            sx, sy, r0 = data
            W, H = self.fg.size  # type: ignore[union-attr]
            w, h = r0[2] - r0[0], r0[3] - r0[1]
            nx = min(max(r0[0] + ix - sx, 0), W - w)
            ny = min(max(r0[1] + iy - sy, 0), H - h)
            self.crop_rect = (nx, ny, nx + w, ny + h)
        self.render()

    def apply_crop(self) -> None:
        if not self.crop_mode or self.crop_rect is None or self.fg is None:
            return
        x0, y0, x1, y1 = (round(v) for v in self.crop_rect)
        if x1 - x0 < 1 or y1 - y0 < 1:
            messagebox.showwarning(APP_NAME, "Please draw a crop area first.")
            return
        self.crop_mode = False
        self.crop_rect = None
        self.crop_bar.pack_forget()
        self.canvas.configure(cursor="arrow")
        self.fg = self.fg.crop((x0, y0, x1, y1))
        self.fit_view(render=False)
        self.commit(f"Cropped to {x1 - x0} × {y1 - y0}")

    # ------------------------------------------------------------ adjustments
    def _adj_changed(self, name: str) -> None:
        self.adj_lbl[name].set(f"{self.adj[name].get():.2f}")
        if self.fg is None:
            return
        if self._adj_job:
            self.root.after_cancel(self._adj_job)
        self._adj_job = self.root.after(50, self._adj_preview)

    def _adj_values(self) -> tuple[float, float, float, float]:
        return tuple(round(v.get(), 2) for v in self.adj.values())  # type: ignore[return-value]

    def _adj_preview(self) -> None:
        self._adj_job = None
        if self.fg is None:
            return
        if self._adj_proxy is None:
            proxy = self.fg.copy()
            proxy.thumbnail((1200, 1200), LANCZOS)
            self._adj_proxy = proxy
        self.adj_preview = apply_adjustments(self._adj_proxy, *self._adj_values())
        self.render()

    def _clear_adjust_preview(self) -> None:
        if self._adj_job:
            self.root.after_cancel(self._adj_job)
            self._adj_job = None
        self.adj_preview = None
        self._adj_proxy = None
        for k, v in self.adj.items():
            v.set(1.0)
            self.adj_lbl[k].set("1.00")

    def reset_adjust(self) -> None:
        self._clear_adjust_preview()
        self.render()
        self.status("Adjustments reset.")

    def apply_adjust(self) -> None:
        if not self._need_image() or self.busy:
            return
        vals = self._adj_values()
        if all(abs(v - 1) < 0.005 for v in vals):
            self.status("No adjustments to apply.")
            return
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            self.fg = apply_adjustments(self.fg, *vals)  # type: ignore[arg-type]
        finally:
            self.root.configure(cursor="")
        self.commit("Adjustments applied")

    # ----------------------------------------------------------- background
    def _bg_image(self) -> Optional[Image.Image]:
        if self.bg.mode != "image" or not self.bg.image_path:
            return None
        p = self.bg.image_path
        if p not in self.bg_cache:
            try:
                self.bg_cache[p] = load_image(p)[0]
            except Exception as exc:
                log.warning("Background image unavailable: %s", exc)
                return None
        return self.bg_cache[p]

    def _bg_set(self, label: str, **changes: Any) -> None:
        if self.fg is None or self.busy:
            return
        self.bg = dataclasses.replace(self.bg, **changes)
        self._sync_bg_widgets()
        self.commit(label)

    def _bg_mode_changed(self) -> None:
        if self.fg is None:
            return
        mode = self.v_bgmode.get()
        if mode == "image" and not self.bg.image_path:
            if not self._browse_bg():
                self.v_bgmode.set(self.bg.mode)
            return
        self._bg_set(f"Background: {mode}", mode=mode)

    def _apply_hex(self) -> None:
        rgb = parse_hex(self.v_hex.get())
        if rgb is None:
            messagebox.showerror(APP_NAME, "Enter a valid hex color such as #FFFFFF or #1AF.")
            return
        self._bg_set("Background color", mode="color", color="#%02X%02X%02X" % rgb)

    def _pick_color(self) -> None:
        rgb, hexv = colorchooser.askcolor(color=self.bg.color, parent=self.root, title="Background color")
        if hexv:
            self._bg_set("Background color", mode="color", color=hexv.upper())

    def _browse_bg(self) -> bool:
        p = filedialog.askopenfilename(title="Background image", filetypes=[
            ("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff"), ("All files", "*.*")])
        if not p:
            return False
        try:
            self.bg_cache[p] = load_image(p)[0]
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not open that background image.\n\n{exc}")
            return False
        self._bg_set("Background image", mode="image", image_path=p)
        return True

    def _bg_pos_live(self, key: str) -> None:
        if self.fg is None:
            return
        val = (self.v_px if key == "x" else self.v_py).get() / 100.0
        self.bg = dataclasses.replace(self.bg, **{key: val})
        self.schedule_light_render()

    def _bg_pos_commit(self) -> None:
        if self.fg is not None and self.bg.mode == "image":
            self.commit("Background position")

    def reset_background(self) -> None:
        self._bg_set("Background reset", **dataclasses.asdict(BgSettings()))

    def _sync_bg_widgets(self) -> None:
        b = self.bg
        self.v_bgmode.set(b.mode)
        self.v_hex.set(b.color)
        self.v_fit.set(b.fit)
        self.v_px.set(b.x * 100)
        self.v_py.set(b.y * 100)
        rgb = parse_hex(b.color) or (255, 255, 255)
        self.swatch.configure(bg="#%02X%02X%02X" % rgb)
        img = self.bg_cache.get(b.image_path) if b.image_path else None
        if img is not None:
            t = img.copy()
            t.thumbnail((250, 80), LANCZOS)
            self._bg_thumb = ImageTk.PhotoImage(flatten(t, (60, 60, 60)))
            self.thumb.configure(image=self._bg_thumb, text="", height=t.height)
            self.v_bgpath.set(Path(b.image_path).name)
        else:
            self._bg_thumb = None
            self.thumb.configure(image="", text="(no preview)", height=5)
            self.v_bgpath.set("No background image selected")

    # ---------------------------------------------------------------- export
    def export_image(self) -> None:
        if not self._need_image() or self.busy:
            return
        assert self.fg is not None
        comp = composite(self.fg, self.bg, self._bg_image())
        transparent = has_transparency(comp)
        opts = self._export_dialog(comp.size, transparent)
        if not opts:
            return
        fmt, w, h, quality, jbg, keep_meta = opts
        if fmt == "JPEG" and transparent:
            if not messagebox.askyesno(
                    APP_NAME, "This image contains transparency, but JPEG cannot store it.\n\n"
                    f"Transparent areas will be filled with {'#%02X%02X%02X' % jbg}.\n"
                    "Choose PNG or WEBP to keep transparency.\n\nContinue with JPEG?"):
                return
        ext = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[fmt]
        init_dir = self.cfg["last_export_dir"] if Path(self.cfg["last_export_dir"] or ".").is_dir() else \
            str(self.path.parent if self.path else Path.home())
        stem = (self.path.stem if self.path else "image") + "_edit"
        out = filedialog.asksaveasfilename(
            title="Export Image", initialdir=init_dir, initialfile=stem + ext, defaultextension=ext,
            filetypes=[(f"{fmt} image", f"*{ext}"), ("All files", "*.*")])
        if not out:
            return
        path = Path(out)
        if path.suffix.lower() not in ((".jpg", ".jpeg") if fmt == "JPEG" else (ext,)):
            path = path.with_suffix(ext)
        if not path.parent.is_dir():
            messagebox.showerror(APP_NAME, f"The folder does not exist:\n{path.parent}")
            return
        exif = self.exif if keep_meta else None

        def job() -> Path:
            img = comp if (w, h) == comp.size else comp.resize((w, h), LANCZOS)
            save_image(img, path, fmt, quality, jbg, exif)
            return path

        self._set_busy(True, f"Exporting {path.name}…")
        self.runner.submit(job, lambda p: self._export_done(p, fmt, quality), self._export_error)

    def _export_done(self, path: Path, fmt: str, quality: int) -> None:
        self._set_busy(False)
        self.cfg["last_export_dir"] = str(path.parent)
        self.cfg["output_format"] = fmt
        if fmt == "JPEG":
            self.cfg["jpeg_quality"] = quality
        elif fmt == "WEBP":
            self.cfg["webp_quality"] = quality
        save_config(self.cfg)
        self.status(f"Exported {path}", "ok")
        if messagebox.askyesno(APP_NAME, f"Export successful:\n{path}\n\nOpen the containing folder?"):
            open_folder(path)

    def _export_error(self, exc: BaseException) -> None:
        self._set_busy(False)
        self.status("Export failed.", "err")
        if isinstance(exc, PermissionError):
            msg = "Permission denied — choose a folder you can write to, or close the file if it is open elsewhere."
        elif isinstance(exc, OSError):
            msg = f"The file could not be written:\n{exc}"
        else:
            msg = f"Export failed:\n{exc}"
        messagebox.showerror(APP_NAME, msg)

    def _export_dialog(self, size: tuple[int, int], transparent: bool):
        d = tk.Toplevel(self.root)
        d.title("Export Options")
        d.configure(bg=C["panel"])
        d.transient(self.root)
        d.resizable(False, False)
        f = ttk.Frame(d, style="Panel.TFrame", padding=16)
        f.pack()
        fmt = tk.StringVar(d, "PNG" if transparent else self.cfg["output_format"])
        vw, vh = tk.StringVar(d, str(size[0])), tk.StringVar(d, str(size[1]))
        lock = tk.BooleanVar(d, True)
        qual = tk.IntVar(d, self.cfg["jpeg_quality"])
        jbg = tk.StringVar(d, self.bg.color if self.bg.mode == "color" else "#FFFFFF")
        meta = tk.BooleanVar(d, False)
        guard = [False]
        result: list = []

        def sync_q(*_):
            qual.set(self.cfg["webp_quality"] if fmt.get() == "WEBP" else self.cfg["jpeg_quality"])
            hint.configure(text="Transparency kept." if fmt.get() != "JPEG" else
                           "⚠ JPEG has no transparency — background colour below is used.")

        ttk.Label(f, text="Format").grid(row=0, column=0, sticky="w")
        fr = ttk.Frame(f, style="Panel.TFrame")
        fr.grid(row=0, column=1, sticky="w", padx=8)
        for t, v in (("PNG", "PNG"), ("JPEG", "JPEG"), ("WEBP", "WEBP")):
            ttk.Radiobutton(fr, text=t, value=v, variable=fmt, command=sync_q).pack(side="left", padx=4)
        hint = ttk.Label(f, text="", style="Muted.TLabel")
        hint.grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(f, text="Size (px)").grid(row=2, column=0, sticky="w", pady=6)
        sr = ttk.Frame(f, style="Panel.TFrame")
        sr.grid(row=2, column=1, sticky="w", padx=8)
        ttk.Entry(sr, textvariable=vw, width=7).pack(side="left")
        ttk.Label(sr, text=" × ").pack(side="left")
        ttk.Entry(sr, textvariable=vh, width=7).pack(side="left")
        ttk.Checkbutton(sr, text="Lock", variable=lock).pack(side="left", padx=8)

        def on_edit(which):
            if guard[0] or not lock.get():
                return
            try:
                guard[0] = True
                if which == "w":
                    vh.set(str(max(1, round(int(vw.get()) * size[1] / size[0]))))
                else:
                    vw.set(str(max(1, round(int(vh.get()) * size[0] / size[1]))))
            except ValueError:
                pass
            finally:
                guard[0] = False

        vw.trace_add("write", lambda *_: on_edit("w"))
        vh.trace_add("write", lambda *_: on_edit("h"))
        ttk.Label(f, text="Quality (JPEG/WEBP)").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Scale(f, from_=1, to=100, variable=qual, length=180).grid(row=3, column=1, sticky="w", padx=8)
        ttk.Label(f, text="Background for JPEG").grid(row=4, column=0, sticky="w", pady=6)
        br = ttk.Frame(f, style="Panel.TFrame")
        br.grid(row=4, column=1, sticky="w", padx=8)
        ttk.Entry(br, textvariable=jbg, width=10).pack(side="left")

        def pick():
            _, hx = colorchooser.askcolor(color=parse_hex(jbg.get()) and jbg.get(), parent=d)
            if hx:
                jbg.set(hx.upper())

        self._btn(br, "Pick…", pick, width=64, height=30).pack(side="left", padx=6)
        ttk.Checkbutton(f, text="Preserve metadata (EXIF) where possible", variable=meta).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=6)

        def ok():
            try:
                w, h = int(vw.get()), int(vh.get())
                if not (1 <= w <= MAX_DIM and 1 <= h <= MAX_DIM):
                    raise ValueError
            except ValueError:
                messagebox.showerror(APP_NAME, f"Size must be whole numbers between 1 and {MAX_DIM}.", parent=d)
                return
            rgb = parse_hex(jbg.get())
            if rgb is None:
                messagebox.showerror(APP_NAME, "Invalid JPEG background colour.", parent=d)
                return
            result.append((fmt.get(), w, h, int(qual.get()), rgb, meta.get()))
            d.destroy()

        bf = ttk.Frame(f, style="Panel.TFrame")
        bf.grid(row=6, column=0, columnspan=2, sticky="e", pady=(10, 0))
        self._btn(bf, "Cancel", d.destroy, width=84).pack(side="right", padx=4)
        self._btn(bf, "Choose file & Export…", ok, "Accent.TButton", width=200).pack(side="right")
        sync_q()
        d.update_idletasks()
        d.geometry(f"+{self.root.winfo_rootx() + 200}+{self.root.winfo_rooty() + 120}")
        d.grab_set()
        self.root.wait_window(d)
        return result[0] if result else None

    # ----------------------------------------------------------------- view
    def _effective_zoom(self, cw: int, ch: int) -> float:
        if self.zoom is not None:
            return self.zoom
        if self.fg is None:
            return 1.0
        lw, lh = self.fg.size
        return max(0.01, min((cw - 40) / lw, (ch - 40) / lh, 8.0))

    def fit_view(self, render: bool = True) -> None:
        self.zoom = None
        self.pan = [0.0, 0.0]
        if render:
            self.render()

    def actual_size(self) -> None:
        if self.fg is not None:
            self.zoom = 1.0
            self.pan = [0.0, 0.0]
            self.render()

    def _zoom_by(self, factor: float) -> None:
        if self.fg is None:
            return
        cur = self._view[2]
        new = min(max(cur * factor, 0.02), 16.0)
        self.pan = [p * new / cur for p in self.pan]
        self.zoom = new
        self.render()

    def _on_wheel(self, e) -> None:
        self._zoom_by(1.1 if e.delta > 0 else 1 / 1.1)

    def _to_img(self, x: float, y: float) -> tuple[float, float]:
        L, T, z = self._view
        return (x - L) / z, (y - T) / z

    def _pan_start(self, e) -> None:
        self._last_mouse = (e.x, e.y)

    def _pan_move(self, e) -> None:
        if self.fg is None:
            return
        self.pan[0] += e.x - self._last_mouse[0]
        self.pan[1] += e.y - self._last_mouse[1]
        self._last_mouse = (e.x, e.y)
        self.render()

    def _on_press(self, e) -> None:
        if self.fg is None or self.busy:
            return
        self._last_mouse = (e.x, e.y)
        if self.crop_mode:
            self._crop_press(e)
        elif self.v_split.get():
            self._split_move(e)

    def _on_drag(self, e) -> None:
        if self.fg is None or self.busy:
            return
        if self.crop_mode:
            self._crop_motion(e)
        elif self.v_split.get():
            self._split_move(e)
        else:
            self._pan_move(e)

    def _on_release(self, e) -> None:
        self._crop_drag = None

    def _split_move(self, e) -> None:
        L, _, z = self._view
        self.split_frac = min(max((e.x - L) / (self.fg.width * z), 0.0), 1.0)  # type: ignore[union-attr]
        self.render()

    # --------------------------------------------------------------- render
    def _schedule_render(self) -> None:
        if self._render_job is None:
            self._render_job = self.root.after(30, self._do_scheduled)

    schedule_light_render = _schedule_render

    def _do_scheduled(self) -> None:
        self._render_job = None
        self.render()

    def _region(self, img: Image.Image, lw: int, box_canvas: tuple[int, int, int, int],
                left: float, top: float, z: float) -> Image.Image:
        """Render the visible part of `img` (logical size lw) with checkerboard behind."""
        vx0, vy0, vx1, vy1 = box_canvas
        r = img.width / lw
        box = [(vx0 - left) / z * r, (vy0 - top) / z * r, (vx1 - left) / z * r, (vy1 - top) / z * r]
        box = [min(max(box[0], 0), img.width), min(max(box[1], 0), img.height),
               min(max(box[2], 0), img.width), min(max(box[3], 0), img.height)]
        if box[2] - box[0] < 1e-3 or box[3] - box[1] < 1e-3:
            return Image.new("RGBA", (vx1 - vx0, vy1 - vy0))
        resample = NEAREST if z >= 6 else BILINEAR
        reg = img.resize((vx1 - vx0, vy1 - vy0), resample, box=tuple(box))  # type: ignore[arg-type]
        if reg.getchannel("A").getextrema()[0] < 255:
            board = make_checkerboard(reg.width, reg.height, self.cfg["checker_size"])
            reg = Image.alpha_composite(board, reg)
        return reg.convert("RGB")

    def _pill(self, x: float, y: float, text: str, anchor: str = "nw", accent: bool = False,
              font: Optional[tkfont.Font] = None) -> None:
        """Draw a small rounded badge on the canvas."""
        font = font or self.f_small
        w, h = font.measure(text) + 26, 26
        if anchor == "se":
            x, y = x - w, y - h
        elif anchor == "sw":
            y -= h
        self.canvas.create_polygon(rounded_points(x, y, x + w, y + h, 13), smooth=True,
                                   fill=C["elev"], outline=C["border"])
        self.canvas.create_text(x + w / 2, y + h / 2, text=text, font=font,
                                fill=C["accent2"] if accent else C["muted"])

    def render(self) -> None:
        c = self.canvas
        c.delete("all")
        cw, ch = c.winfo_width(), c.winfo_height()
        if cw < 20 or ch < 20:
            return
        if self.fg is None:
            self._draw_welcome(cw, ch)
            self._draw_toast()
            return
        lw, lh = self.fg.size
        z = self._effective_zoom(cw, ch)
        sw, sh = lw * z, lh * z
        left = (cw - sw) / 2 + self.pan[0]
        top = (ch - sh) / 2 + self.pan[1]
        self._view = (left, top, z)
        self.v_zoom.set(f"{z * 100:.0f}%" + (" (fit)" if self.zoom is None else ""))
        vx0, vy0 = max(0, int(left)), max(0, int(top))
        vx1, vy1 = min(cw, int(left + sw + 0.999)), min(ch, int(top + sh + 0.999))
        if vx1 <= vx0 or vy1 <= vy0:
            return
        # soft drop shadow behind the image
        for i in range(7, 0, -1):
            col = hex_mix(C["bg"], "#000000", 0.9 * (8 - i) / 8)
            c.create_rectangle(left - i * 2, top - i * 2 + 6, left + sw + i * 2, top + sh + i * 2 + 6,
                               fill=col, outline="")
        base = self.adj_preview if self.adj_preview is not None else self.fg
        after = composite(base, self.bg, self._bg_image())
        show_before = self.v_before.get() and not self.crop_mode
        splitting = self.v_split.get() and not self.crop_mode
        label = "AFTER"
        before_img = None
        if show_before or splitting:
            before_img = self.original
            assert before_img is not None
            if before_img.size != (lw, lh):
                before_img = fit_image(before_img, (lw, lh), "contain")
        box = (vx0, vy0, vx1, vy1)
        if show_before:
            reg = self._region(before_img, lw, box, left, top, z)  # type: ignore[arg-type]
            label = "BEFORE  ·  original"
        else:
            reg = self._region(after, lw, box, left, top, z)
            if splitting:
                divx = left + self.split_frac * sw
                cut = int(min(max(divx - vx0, 0), reg.width))
                if cut > 0:
                    breg = self._region(before_img, lw, box, left, top, z)  # type: ignore[arg-type]
                    reg.paste(breg.crop((0, 0, cut, reg.height)), (0, 0))
                label = "BEFORE  |  AFTER"
        self._photo = ImageTk.PhotoImage(reg)
        c.create_image(vx0, vy0, image=self._photo, anchor="nw")
        c.create_rectangle(left, top, left + sw, top + sh, outline=C["border"])
        if splitting and not show_before:
            divx = left + self.split_frac * sw
            my = (vy0 + vy1) / 2
            c.create_line(divx, max(top, 0), divx, min(top + sh, ch), fill=C["accent2"], width=2)
            c.create_oval(divx - 12, my - 12, divx + 12, my + 12, fill=C["accent2"], outline=C["bg"], width=2)
            c.create_text(divx, my, text="⇄", fill=C["bg"], font=(FONT, 10, "bold"))
        self._pill(14, 14, label, "nw", accent=True)
        self._pill(cw - 14, ch - 14, f"Original {self.original.width}×{self.original.height}   •   "  # type: ignore[union-attr]
                   f"Working {lw}×{lh}   •   Preview {int(sw)}×{int(sh)}", "se", font=self.f_info)
        if self.crop_mode and self.crop_rect:
            self._draw_crop(left, top, sw, sh, z)
        self._draw_toast()

    def _draw_crop(self, left: float, top: float, sw: float, sh: float, z: float) -> None:
        c = self.canvas
        x0, y0, x1, y1 = self.crop_rect  # type: ignore[misc]
        cx0, cy0, cx1, cy1 = left + x0 * z, top + y0 * z, left + x1 * z, top + y1 * z
        shade = dict(fill="#000000", stipple="gray50", outline="")
        c.create_rectangle(left, top, left + sw, cy0, **shade)
        c.create_rectangle(left, cy1, left + sw, top + sh, **shade)
        c.create_rectangle(left, cy0, cx0, cy1, **shade)
        c.create_rectangle(cx1, cy0, left + sw, cy1, **shade)
        # rule-of-thirds guides
        for k in (1, 2):
            c.create_line(cx0 + (cx1 - cx0) * k / 3, cy0, cx0 + (cx1 - cx0) * k / 3, cy1, fill="#FFFFFF", dash=(2, 4))
            c.create_line(cx0, cy0 + (cy1 - cy0) * k / 3, cx1, cy0 + (cy1 - cy0) * k / 3, fill="#FFFFFF", dash=(2, 4))
        c.create_rectangle(cx0, cy0, cx1, cy1, outline=C["accent2"], width=2, dash=(6, 3))
        for cx, cy in self._crop_corners_canvas().values():
            c.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, fill=C["accent"], outline="#FFFFFF", width=2)
        w, h = round(x1 - x0), round(y1 - y0)
        self.v_crop_info.set(f"Selection: {w} × {h} px")
        self._pill(cx0, cy0 - 6 if cy0 > 40 else cy0 + 32, f"{w} × {h}", "sw", accent=True)

    def _draw_welcome(self, cw: int, ch: int) -> None:
        c = self.canvas
        if self._welcome_key != (cw, ch):
            glow = make_glow(cw, ch, C["bg"], hex_mix(C["bg"], C["accent"], GLOW))
            self._welcome_photo = ImageTk.PhotoImage(glow)
            self._welcome_key = (cw, ch)
        c.create_image(0, 0, image=self._welcome_photo, anchor="nw")
        cx, cy = cw / 2, ch / 2
        c.create_oval(cx - 52, cy - 190, cx + 52, cy - 86, outline=C["accent"], width=2)
        c.create_oval(cx - 40, cy - 178, cx + 40, cy - 98, fill=C["accent"], outline="")
        c.create_text(cx, cy - 138, text="◆", fill="#FFFFFF", font=(FONT, 30, "bold"))
        #c.create_text(cx - 2, cy - 48, text="ZaBir", anchor="e", fill=C["text"], font=(FONT, 32, "bold"))
        #c.create_text(cx + 2, cy - 48, text="ImageLab", anchor="w", fill=C["accent2"], font=(FONT, 32, "bold"))
        c.create_text(cx, cy - 4, text="Background Remover & Image Editor", fill=C["muted"], font=(FONT, 13))
        c.create_window(cx, cy + 58, window=self.welcome_btn)
        chips = ["✨ Local AI cutout", "▦ Checkerboard preview", "◐ Background replace", "⤓ PNG · JPG · WEBP"]
        widths = [self.f_info.measure(t) + 30 for t in chips]
        x = cx - (sum(widths) + 12 * (len(chips) - 1)) / 2
        for t, w in zip(chips, widths):
            c.create_polygon(rounded_points(x, cy + 112, x + w, cy + 140, 14), smooth=True,
                             fill=C["panel"], outline=C["border"])
            c.create_text(x + w / 2, cy + 126, text=t, fill=C["muted"], font=self.f_info)
            x += w + 12
        hint = "Drag & drop an image anywhere here, or press Ctrl+O" if HAS_DND else \
            "Press Ctrl+O to open an image   (pip install tkinterdnd2 enables drag & drop)"
        c.create_text(cx, cy + 176, text=hint, fill=C["muted"], font=(FONT, 10))
        self.v_zoom.set("")

    # ---- toast notifications
    def _toast(self, msg: str, kind: str) -> None:
        self._toast_msg = (msg if len(msg) <= 80 else msg[:77] + "…", kind)
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        self._toast_job = self.root.after(2600, self._toast_clear)
        self._draw_toast()

    def _toast_clear(self) -> None:
        self._toast_job = None
        self._toast_msg = None
        self.canvas.delete("toast")

    def _draw_toast(self) -> None:
        c = self.canvas
        c.delete("toast")
        if not self._toast_msg:
            return
        msg, kind = self._toast_msg
        cw, ch = c.winfo_width(), c.winfo_height()
        col = C["err"] if kind == "err" else C["ok"]
        w, h = self.f_small.measure(msg) + 62, 36
        x0, y1 = (cw - w) / 2, ch - 56
        y0 = y1 - h
        c.create_polygon(rounded_points(x0, y0, x0 + w, y1, 18), smooth=True, fill=C["elev"],
                         outline=col, width=1, tags="toast")
        c.create_oval(x0 + 16, y0 + h / 2 - 5, x0 + 26, y0 + h / 2 + 5, fill=col, outline="", tags="toast")
        c.create_text(x0 + 38, y0 + h / 2, text=msg, anchor="w", fill=C["text"], font=self.f_small, tags="toast")

    # ------------------------------------------------------------------ misc
    def _about(self) -> None:
        messagebox.showinfo(
            APP_NAME, f"{APP_NAME}\n\nLocal AI background removal (rembg + ONNX Runtime),\n"
            "Pillow image processing and a Tkinter interface.\n\n"
            f"Config: {CONFIG_PATH}\nLog: {LOG_PATH}\nDrag & drop: {'enabled' if HAS_DND else 'not installed'}")

    def _on_close(self) -> None:
        try:
            self.runner.shutdown()
            if self._toast_job:
                self.root.after_cancel(self._toast_job)
            if self._render_job:
                self.root.after_cancel(self._render_job)
            if self._adj_job:
                self.root.after_cancel(self._adj_job)
            self.cfg["window"] = [max(600, self.root.winfo_width()), max(600, self.root.winfo_height())]
            save_config(self.cfg)
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


# --------------------------------------------------------------------------- #
# Self-tests (python "AI-STYLE BACKGROUND REMOVER.py" --selftest)
# --------------------------------------------------------------------------- #
def run_selftests() -> int:
    ok = 0

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok += 0 if cond else 1

    cb = make_checkerboard(48, 48, 12)
    check("checkerboard size/colours", cb.size == (48, 48) and cb.getpixel((0, 0)) != cb.getpixel((12, 0)))
    check("parse_hex", parse_hex("#1af") == (17, 170, 255) and parse_hex("zzz") is None)

    fg = Image.new("RGBA", (40, 20), (255, 0, 0, 255))
    fg.paste((0, 0, 0, 0), (0, 0, 20, 20))
    check("has_transparency", has_transparency(fg) and not has_transparency(Image.new("RGB", (4, 4))))
    out = composite(fg, BgSettings(mode="color", color="#00FF00"), None)
    check("composite colour", out.getpixel((5, 5)) == (0, 255, 0, 255) and out.getpixel((30, 5)) == (255, 0, 0, 255))
    check("composite none keeps alpha", composite(fg, BgSettings(), None).getpixel((5, 5))[3] == 0)
    bgimg = Image.new("RGB", (100, 100), (0, 0, 255))
    for mode in BG_FITS:
        check(f"fit_image {mode}", fit_image(bgimg, (40, 20), mode).size == (40, 20))
    adj = apply_adjustments(fg, 1.2, 1.0, 1.0, 1.0)
    check("adjustments keep alpha", adj.getpixel((5, 5))[3] == 0 and adj.getpixel((30, 5))[3] == 255)
    check("flatten", flatten(fg, (1, 2, 3)).getpixel((5, 5)) == (1, 2, 3))

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.png"
        save_image(fg, p, "PNG")
        re = Image.open(p)
        check("PNG keeps alpha", re.mode == "RGBA" and re.getchannel("A").getextrema()[0] == 0)
        j = Path(td) / "t.jpg"
        save_image(fg, j, "JPEG", 90, (255, 255, 255))
        rj = Image.open(j)
        check("JPEG is RGB flattened", rj.mode == "RGB" and sum(rj.getpixel((5, 5))) > 700)
        w = Path(td) / "t.webp"
        save_image(fg, w, "WEBP", 80)
        check("WEBP keeps alpha", Image.open(w).mode == "RGBA")
        check("load_image RGBA", load_image(p)[0].mode == "RGBA")
        bad = Path(td) / "bad.png"
        bad.write_bytes(b"not an image")
        try:
            load_image(bad)
            check("invalid image raises", False)
        except Exception:
            check("invalid image raises", True)

    g = make_gradient(64, 4, "#000000", "#FFFFFF")
    check("gradient left->right", g.size == (64, 4) and g.getpixel((0, 0))[0] < 10 and g.getpixel((63, 0))[0] > 245)
    glow = make_glow(80, 60, "#000000", "#FFFFFF")
    check("glow centre brighter than edge", glow.getpixel((40, 30))[0] > glow.getpixel((0, 0))[0])
    check("hex_mix", hex_mix("#000000", "#FFFFFF", 0.5) in ("#7f7f7f", "#808080"))
    check("rounded_points", len(rounded_points(0, 0, 10, 10, 3)) == 24)

    h = HistoryManager(3)
    h.reset(Snapshot(fg, BgSettings(), "a"))
    for n in "bcd":
        h.push(Snapshot(fg, BgSettings(), n))
    check("history limit", len(h.states) == 3 and h.can_undo and not h.can_redo)
    h.undo()
    check("history redo", h.can_redo and h.redo().label == "d")
    print("\nAll tests passed." if ok == 0 else f"\n{ok} test(s) FAILED.")
    return 1 if ok else 0


def main() -> None:
    setup_logging()
    if "--selftest" in sys.argv:
        sys.exit(run_selftests())
    try:  # crisp text on Windows high-DPI displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        pass
    App().run()


if __name__ == "__main__":
    main()