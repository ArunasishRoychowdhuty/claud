import os, time, math, random
import tkinter as tk
from collections import deque
from PIL import Image, ImageTk, ImageDraw
import sys
from pathlib import Path

from core.python_runtime import build_restart_command, ensure_windows_runtime_env
from memory.config_manager import (
    has_permission_consent,
    is_configured,
    load_api_keys,
    load_permission_state,
    save_api_keys as save_api_keys_config,
    save_permission_state,
)
from core.llm_settings import normalize_provider

ensure_windows_runtime_env()


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR   = get_base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

SYSTEM_NAME = "J.A.R.V.I.S"
MODEL_BADGE = "DESKTOP COMMAND CORE"

C_BG       = "#04070c"
C_BG2      = "#0b141c"
C_PRI      = "#79f0ff"
C_MID      = "#12475b"
C_DIM      = "#18394a"
C_DIMMER   = "#0a1219"
C_ACC      = "#ffad62"
C_ACC2     = "#ffe2ad"
C_TEXT     = "#ecfbff"
C_TEXT_DIM = "#94c8d2"
C_PANEL    = "#0b1218"
C_PANEL2   = "#101a22"
C_LINE     = "#1a3646"
C_GREEN    = "#00ff9c"
C_RED      = "#ff5c6f"

PROVIDER_PROFILES = {
    "gemini": {
        "label": "Gemini Live",
        "key_field": "gemini_api_key",
        "key_label": "GEMINI API KEY",
        "default_model": "gemini-2.5-flash",
        "live_provider": "gemini",
        "llm_provider": "gemini",
        "note": "Native low-latency live audio session.",
    },
    "openai": {
        "label": "OpenAI Realtime",
        "key_field": "openai_api_key",
        "key_label": "OPENAI API KEY",
        "default_model": "gpt-4o-realtime-preview",
        "live_provider": "openai",
        "llm_provider": "openai",
        "note": "Realtime voice session over the OpenAI Realtime API.",
    },
    "anthropic": {
        "label": "Claude via Fallback",
        "key_field": "anthropic_api_key",
        "key_label": "ANTHROPIC API KEY",
        "default_model": "claude-3-7-sonnet-latest",
        "live_provider": "fallback",
        "llm_provider": "anthropic",
        "note": "Fallback speech loop with Claude for reasoning.",
    },
    "openrouter": {
        "label": "OpenRouter",
        "key_field": "openrouter_api_key",
        "key_label": "OPENROUTER API KEY",
        "default_model": "anthropic/claude-3.7-sonnet",
        "live_provider": "fallback",
        "llm_provider": "openrouter",
        "note": "Fallback speech loop with OpenRouter as the text backend.",
    },
    "github": {
        "label": "GitHub Models",
        "key_field": "github_api_key",
        "key_label": "GITHUB TOKEN",
        "default_model": "openai/gpt-4.1",
        "live_provider": "fallback",
        "llm_provider": "github",
        "note": "Fallback speech loop with GitHub Models as the text backend.",
    },
}


def _provider_profile(provider: str) -> dict:
    key = normalize_provider(provider)
    return dict(PROVIDER_PROFILES.get(key, PROVIDER_PROFILES["gemini"]))


def build_api_binding_payload(provider: str, api_key: str, model: str = "") -> dict:
    profile = _provider_profile(provider)
    provider_key = normalize_provider(provider)
    cleaned_key = str(api_key or "").strip()
    cleaned_model = str(model or "").strip() or profile["default_model"]
    payload = {
        profile["key_field"]: cleaned_key,
        "llm_provider": profile["llm_provider"],
        "llm_model": cleaned_model,
    }

    live_provider = profile["live_provider"]
    payload["live_provider"] = live_provider
    if live_provider == "openai":
        payload["openai_realtime_model"] = cleaned_model
    elif live_provider == "fallback":
        payload["live_provider_llm"] = provider_key
    return payload


class JarvisUI:
    def __init__(self, face_path, size=None):
        self.root = tk.Tk()
        self.root.title("J.A.R.V.I.S — MARK XXX")
        self.root.resizable(False, False)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        W  = min(sw, 1180)
        H  = min(sh, 860)
        self.root.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}")
        self.root.configure(bg=C_BG)

        self.W = W
        self.H = H

        self.FACE_SZ = min(int(H * 0.42), 360)
        self.FCX     = W // 2
        self.FCY     = int(H * 0.34)

        self.speaking     = False
        self.scale        = 1.0
        self.target_scale = 1.0
        self.halo_a       = 78.0
        self.target_halo  = 78.0
        self.last_t       = time.time()
        self.tick         = 0
        self.scan_angle   = 0.0
        self.scan2_angle  = 180.0
        self.rings_spin   = [0.0, 120.0, 240.0]
        self.pulse_r      = [0.0, self.FACE_SZ * 0.24, self.FACE_SZ * 0.48]
        self.status_text  = "INITIALISING"
        self.status_blink = True
        self.primary_signal = "SYSTEMS BOOTING"
        self.secondary_signal = "Awaiting live link"
        self.access_mode = "NORMAL"
        self.admin_state = "STANDARD"
        self.directive_text = "No directive captured yet."
        self.reply_text = "No response generated yet."
        self.capability_summary = "Capability scan pending."
        self.precheck_state = None
        self.precheck_text = "Awaiting live precheck."
        self.reload_state = "SYNC"
        self.reload_text = "Session sync stable."
        self.task_status_text = "Queue idle."
        self.task_focus_text = "No active background task."
        self.provider_binding_text = "Unbound session"
        self.observer_enabled = None
        self.vision_state = "PAUSED"
        self.observer_mode_text = "always_on"
        self.observer_last_update_ts = 0.0
        self.observer_last_scene_text = "No live observer scene yet."
        self.observer_last_action_text = "No autonomous action yet."
        self.capability_chips = [
            ("VOICE", None),
            ("DESKTOP", None),
            ("BROWSER", None),
            ("SCREEN", None),
            ("CAMERA", None),
        ]
        self.system_events = deque(
            [
                "Awaiting startup sequence.",
                "Telemetry grid aligned.",
                "Core visual surface online.",
            ],
            maxlen=5,
        )

        self.typing_queue = deque()
        self.is_typing    = False
        self._stars = [
            (
                random.randint(20, W - 20),
                random.randint(70, H - 40),
                random.uniform(0.8, 2.2),
                random.uniform(0, math.tau),
            )
            for _ in range(40)
        ]

        self._face_pil         = None
        self._has_face         = False
        self._face_scale_cache = None
        self._setup_edit_mode = False
        self._load_face(face_path)

        self.bg = tk.Canvas(self.root, width=W, height=H,
                            bg=C_BG, highlightthickness=0)
        self.bg.place(x=0, y=0)

        LW = W - 84
        LH = 188
        self.log_frame = tk.Frame(self.root, bg="#0b1218",
                                  highlightbackground=C_LINE,
                                  highlightthickness=1)
        self.log_frame.place(x=42, y=H - LH - 28, width=LW, height=LH)
        self.log_head = tk.Frame(self.log_frame, bg="#111b24", height=38)
        self.log_head.pack(fill="x")
        self.log_head.pack_propagate(False)
        tk.Label(
            self.log_head,
            text="MISSION FEED",
            fg=C_PRI,
            bg="#111b24",
            font=("Bahnschrift SemiBold", 12),
        ).pack(side="left", padx=12)
        self.console_mode_label = tk.Label(
            self.log_head,
            text="NORMAL SURFACE",
            fg=C_ACC2,
            bg="#111b24",
            font=("Consolas", 9, "bold"),
        )
        self.console_mode_label.pack(side="right", padx=12)
        self.provider_label = tk.Label(
            self.log_head,
            text="UNBOUND",
            fg=C_TEXT_DIM,
            bg="#111b24",
            font=("Consolas", 9, "bold"),
        )
        self.provider_label.pack(side="right", padx=(0, 8))
        self._logout_btn = tk.Button(
            self.log_head,
            text="LOGOUT",
            command=self._open_rebind_ui,
            bg="#2a1018",
            fg="#ffb6c4",
            activebackground="#4b1724",
            activeforeground="#ffd7de",
            font=("Consolas", 8, "bold"),
            borderwidth=0,
            padx=8,
            pady=4,
            cursor="hand2",
        )
        self._logout_btn.pack(side="right", padx=(0, 6))
        # ── Dashboard toggle button ───────────────────────────────────────────
        self._dash_btn = tk.Button(
            self.log_head,
            text="📊 METRICS",
            command=self._toggle_dashboard,
            bg=C_DIM,
            fg=C_ACC2,
            activebackground=C_MID,
            activeforeground=C_PRI,
            font=("Consolas", 8, "bold"),
            borderwidth=0,
            padx=8,
            pady=4,
            cursor="hand2",
        )
        self._dash_btn.pack(side="right", padx=(0, 6))
        self.precheck_label = tk.Label(
            self.log_head,
            text="PRECHECK IDLE",
            fg=C_TEXT_DIM,
            bg="#111b24",
            font=("Consolas", 9, "bold"),
        )
        self.precheck_label.pack(side="right", padx=(0, 8))
        self.log_text = tk.Text(self.log_frame, fg=C_TEXT, bg="#0c141c",
                                insertbackground=C_TEXT, borderwidth=0,
                                wrap="word", font=("Consolas", 10), padx=14, pady=12)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")
        self.log_text.tag_config("you", foreground="#f7fcff")
        self.log_text.tag_config("ai",  foreground=C_PRI)
        self.log_text.tag_config("sys", foreground=C_ACC2)
        self.log_text.tag_config("precheck", foreground=C_GREEN)
        self.log_text.tag_config("observe", foreground=C_PRI)
        self.log_text.tag_config("auto", foreground=C_GREEN)
        self.log_text.tag_config("autoblocked", foreground=C_RED)

        self._api_key_ready = self._api_keys_exist()
        self._refresh_provider_binding()
        self._refresh_permission_state()
        self.set_access_state(
            self._permission_state.get("trusted_mode", False),
            False,
        )
        if not self._api_key_ready:
            self._show_setup_ui()
        elif not self._permission_ready:
            self._show_permission_ui()
        else:
            self.status_text = "ONLINE"
            self.primary_signal = "DIRECTIVE SURFACE READY"
            self.secondary_signal = "Awaiting mission input"

        self._animate()
        self.root.after(900, self._poll_task_queue)
        self.root.after(5000, self._poll_metrics)
        self.root.protocol("WM_DELETE_WINDOW", lambda: os._exit(0))
        
        # Phase 9: Wired Operator Daemon to HUD
        try:
            from core.operator_daemon import get_daemon
            get_daemon()._on_step_event = self._on_daemon_step
        except ImportError:
            pass

        # Dashboard state
        self._dash_win = None
        self._dash_visible = False

    def _load_face(self, path):
        FW = self.FACE_SZ
        try:
            img  = Image.open(path).convert("RGBA").resize((FW, FW), Image.LANCZOS)
            mask = Image.new("L", (FW, FW), 0)
            ImageDraw.Draw(mask).ellipse((2, 2, FW - 2, FW - 2), fill=255)
            img.putalpha(mask)
            self._face_pil = img
            self._has_face = True
        except Exception:
            self._has_face = False

    @staticmethod
    def _ac(r, g, b, a):
        f = a / 255.0
        return f"#{int(r*f):02x}{int(g*f):02x}{int(b*f):02x}"

    def _rounded_rect(self, canvas, x1, y1, x2, y2, radius=18, **kwargs):
        radius = max(4, min(int(radius), int((x2 - x1) / 2), int((y2 - y1) / 2)))
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]
        return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)

    def _draw_glow_ring(self, canvas, x, y, radius, color_rgb, strength=5):
        red, green, blue = color_rgb
        for step in range(max(2, strength), 0, -1):
            spread = radius + step * 10
            alpha = max(8, 22 - step * 3)
            canvas.create_oval(
                x - spread, y - spread, x + spread, y + spread,
                outline=self._ac(red, green, blue, alpha),
                width=2,
            )

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    def set_access_state(self, trusted_mode: bool, admin_session: bool):
        self.access_mode = "TRUSTED" if trusted_mode else "NORMAL"
        self.admin_state = "ADMIN" if admin_session else "STANDARD"
        self.console_mode_label.configure(
            text=f"{self.access_mode} SURFACE",
            fg=C_GREEN if trusted_mode else C_ACC2,
        )

    def set_capabilities(self, capabilities: dict):
        tools = (capabilities or {}).get("tools", {})
        hardware = (capabilities or {}).get("hardware", {})
        self.capability_chips = [
            ("VOICE", tools.get("voice_pipeline")),
            ("DESKTOP", tools.get("desktop_automation")),
            ("BROWSER", tools.get("browser_automation")),
            ("SCREEN", tools.get("screen_capture")),
            ("CAMERA", hardware.get("camera")),
        ]
        active = [name for name, value in self.capability_chips if value]
        if active:
            self.capability_summary = "Live surfaces: " + ", ".join(active)
        else:
            self.capability_summary = "No runtime surfaces are currently online."

    def _draw_panel(self, canvas, x, y, w, h, title, accent):
        self._rounded_rect(canvas, x, y, x + w, y + h, radius=24, fill=self._ac(12, 18, 26, 240), outline=self._ac(121, 240, 255, 34), width=1)
        self._rounded_rect(canvas, x + 1, y + 1, x + w - 1, y + 42, radius=22, fill=self._ac(18, 28, 38, 250), outline="")
        canvas.create_line(x + 20, y + 43, x + w - 20, y + 43, fill=self._ac(121, 240, 255, 32), width=1)
        self._rounded_rect(canvas, x + 14, y + 13, x + 54, y + 23, radius=6, fill=accent, outline="")
        canvas.create_text(
            x + 68,
            y + 20,
            text=title,
            fill="#f4fbff",
            font=("Bahnschrift SemiBold", 11),
            anchor="w",
        )
        canvas.create_text(
            x + w - 16,
            y + 20,
            text="LIVE",
            fill=C_TEXT_DIM,
            font=("Consolas", 8, "bold"),
            anchor="e",
        )

    def _draw_badge(self, canvas, x, y, label, value, width=100):
        if isinstance(value, str):
            state = value.strip().upper() or "SYNC"
            if state in {"SYNC", "READY", "OK"}:
                fill = self._ac(15, 45, 34, 255)
                edge = self._ac(0, 255, 156, 96)
                text_fill = C_GREEN
            elif state in {"RELOAD", "PENDING", "REFRESH"}:
                fill = self._ac(52, 38, 18, 255)
                edge = self._ac(255, 214, 107, 96)
                text_fill = C_ACC2
            else:
                fill = self._ac(50, 18, 28, 255)
                edge = self._ac(255, 92, 111, 96)
                text_fill = C_RED
        elif value is True:
            fill = self._ac(15, 45, 34, 255)
            edge = self._ac(0, 255, 156, 96)
            state = "ON"
            text_fill = C_GREEN
        elif value is False:
            fill = self._ac(50, 18, 28, 255)
            edge = self._ac(255, 92, 111, 96)
            state = "OFF"
            text_fill = C_RED
        else:
            fill = self._ac(22, 32, 44, 255)
            edge = self._ac(148, 200, 210, 72)
            state = "SCAN"
            text_fill = C_TEXT_DIM

        self._rounded_rect(canvas, x, y, x + width, y + 28, radius=12, fill=fill, outline=edge, width=1)
        canvas.create_text(
            x + 8,
            y + 14,
            text=label,
            fill=C_TEXT_DIM,
            font=("Consolas", 8, "bold"),
            anchor="w",
        )
        canvas.create_text(
            x + width - 8,
            y + 14,
            text=state,
            fill=text_fill,
            font=("Consolas", 8, "bold"),
            anchor="e",
        )

    def _animate(self):
        self.tick += 1
        t   = self.tick
        now = time.time()

        if now - self.last_t > (0.14 if self.speaking else 0.55):
            if self.speaking:
                self.target_scale = random.uniform(1.05, 1.11)
                self.target_halo  = random.uniform(138, 182)
            else:
                self.target_scale = random.uniform(1.001, 1.007)
                self.target_halo  = random.uniform(50, 68)
            self.last_t = now

        sp = 0.35 if self.speaking else 0.16
        self.scale  += (self.target_scale - self.scale) * sp
        self.halo_a += (self.target_halo  - self.halo_a) * sp

        for i, spd in enumerate([1.2, -0.8, 1.9] if self.speaking else [0.5, -0.3, 0.82]):
            self.rings_spin[i] = (self.rings_spin[i] + spd) % 360

        self.scan_angle  = (self.scan_angle  + (2.8 if self.speaking else 1.2)) % 360
        self.scan2_angle = (self.scan2_angle + (-1.7 if self.speaking else -0.68)) % 360

        pspd  = 3.8 if self.speaking else 1.8
        limit = self.FACE_SZ * 0.72
        new_p = [r + pspd for r in self.pulse_r if r + pspd < limit]
        if len(new_p) < 3 and random.random() < (0.06 if self.speaking else 0.022):
            new_p.append(0.0)
        self.pulse_r = new_p

        if t % 40 == 0:
            self.status_blink = not self.status_blink

        self._draw()
        self.root.after(16, self._animate)

    def _draw(self):
        c = self.bg
        W, H = self.W, self.H
        t = self.tick
        FCX = self.FCX
        FCY = self.FCY
        FW = self.FACE_SZ
        c.delete("all")

        for y in range(0, H, 8):
            frac = y / max(1, H)
            r = 4 + int(14 * (1.0 - frac))
            g = 8 + int(18 * (1.0 - frac))
            b = 14 + int(32 * (1.0 - frac))
            c.create_rectangle(0, y, W, y + 8, fill=f"#{r:02x}{g:02x}{b:02x}", outline="")

        c.create_oval(-220, -120, int(W * 0.42), int(H * 0.58), fill=self._ac(35, 144, 180, 36), outline="")
        c.create_oval(int(W * 0.56), -80, W + 180, int(H * 0.44), fill=self._ac(255, 166, 86, 24), outline="")
        c.create_oval(int(W * 0.18), int(H * 0.48), int(W * 0.82), H + 160, fill=self._ac(64, 144, 255, 16), outline="")

        for offset in range(-H, W + H, 92):
            c.create_line(offset, 90, offset + H, H, fill=self._ac(32, 94, 114, 12), width=1)

        for x in range(26, W, 88):
            c.create_line(x, 84, x, H - 50, fill=self._ac(18, 40, 52, 24), width=1)

        for sx, sy, size, phase in self._stars:
            pulse = 0.45 + 0.55 * math.sin(t * 0.03 + phase)
            radius = size * pulse
            alpha = int(25 + 55 * pulse)
            c.create_oval(
                sx - radius,
                sy - radius,
                sx + radius,
                sy + radius,
                fill=self._ac(90, 232, 255, alpha),
                outline="",
            )

        self._rounded_rect(c, 14, 12, W - 14, 72, radius=24, fill=self._ac(10, 18, 26, 248), outline=self._ac(121, 240, 255, 30), width=1)
        self._rounded_rect(c, 22, 20, 182, 48, radius=14, fill=self._ac(18, 34, 42, 255), outline="", width=0)
        c.create_text(34, 34, text=MODEL_BADGE, fill=C_TEXT_DIM, font=("Consolas", 9, "bold"), anchor="w")
        c.create_text(W // 2, 30, text=SYSTEM_NAME, fill=C_PRI, font=("Bahnschrift SemiBold", 24))
        c.create_text(W // 2, 52, text="Adaptive Desktop Intelligence Surface", fill=C_TEXT_DIM, font=("Consolas", 10))
        c.create_text(W - 24, 30, text=time.strftime("%H:%M:%S"), fill=C_PRI, font=("Bahnschrift SemiBold", 18), anchor="e")
        c.create_text(
            W - 18,
            52,
            text=f"{self.access_mode} / {self.admin_state}",
            fill=C_GREEN if self.access_mode == "TRUSTED" else C_ACC2,
            font=("Consolas", 9, "bold"),
            anchor="e",
        )

        plate_w = int(FW * 1.74)
        plate_h = int(FW * 1.18)
        self._rounded_rect(
            c,
            FCX - plate_w // 2,
            FCY - plate_h // 2,
            FCX + plate_w // 2,
            FCY + plate_h // 2,
            radius=34,
            fill=self._ac(10, 16, 24, 235),
            outline=self._ac(121, 240, 255, 22),
            width=1,
        )
        self._rounded_rect(
            c,
            FCX - plate_w // 2 + 12,
            FCY - plate_h // 2 + 12,
            FCX + plate_w // 2 - 12,
            FCY + plate_h // 2 - 12,
            radius=30,
            fill=self._ac(12, 22, 32, 228),
            outline=self._ac(255, 173, 98, 18),
            width=1,
        )

        self._draw_glow_ring(c, FCX, FCY, int(FW * 0.42), (121, 240, 255), strength=6)
        self._draw_glow_ring(c, FCX, FCY, int(FW * 0.30), (255, 173, 98), strength=4)

        for pr in self.pulse_r:
            pa = max(0, int(220 * (1.0 - pr / (FW * 0.72))))
            r  = int(pr)
            c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r,
                          outline=self._ac(121, 240, 255, pa), width=2)

        for idx, (r_frac, w_ring, arc_l, gap) in enumerate([
                (0.47, 3, 124, 64), (0.39, 2, 82, 48), (0.31, 1, 60, 32)]):
            ring_r = int(FW * r_frac)
            base_a = self.rings_spin[idx]
            a_val  = max(0, min(255, int(self.halo_a * (1.0 - idx * 0.18))))
            col    = self._ac(121, 240, 255, a_val)
            for s in range(360 // (arc_l + gap)):
                start = (base_a + s * (arc_l + gap)) % 360
                c.create_arc(FCX-ring_r, FCY-ring_r, FCX+ring_r, FCY+ring_r,
                             start=start, extent=arc_l,
                             outline=col, width=w_ring, style="arc")

        sr      = int(FW * 0.58)
        scan_a  = min(255, int(self.halo_a * 1.4))
        arc_ext = 94 if self.speaking else 54
        c.create_arc(FCX-sr, FCY-sr, FCX+sr, FCY+sr,
                     start=self.scan_angle, extent=arc_ext,
                     outline=self._ac(121, 240, 255, scan_a), width=4, style="arc")
        c.create_arc(FCX-sr, FCY-sr, FCX+sr, FCY+sr,
                     start=self.scan2_angle, extent=arc_ext,
                     outline=self._ac(255, 173, 98, scan_a // 2), width=2, style="arc")

        t_out = int(FW * 0.495)
        t_in  = int(FW * 0.472)
        a_mk  = self._ac(121, 240, 255, 105)
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 5
            c.create_line(FCX + t_out * math.cos(rad), FCY - t_out * math.sin(rad),
                          FCX + inn  * math.cos(rad), FCY - inn  * math.sin(rad),
                          fill=a_mk, width=1)

        ch_r = int(FW * 0.50)
        gap  = int(FW * 0.15)
        ch_a = self._ac(121, 240, 255, int(self.halo_a * 0.30))
        for x1, y1, x2, y2 in [
                (FCX - ch_r, FCY, FCX - gap, FCY), (FCX + gap, FCY, FCX + ch_r, FCY),
                (FCX, FCY - ch_r, FCX, FCY - gap), (FCX, FCY + gap, FCX, FCY + ch_r)]:
            c.create_line(x1, y1, x2, y2, fill=ch_a, width=1)

        blen = 22
        bc   = self._ac(0, 212, 255, 200)
        hl = FCX - FW // 2; hr = FCX + FW // 2
        ht = FCY - FW // 2; hb = FCY + FW // 2
        for bx, by, sdx, sdy in [(hl, ht, 1, 1), (hr, ht, -1, 1),
                                   (hl, hb, 1, -1), (hr, hb, -1, -1)]:
            c.create_line(bx, by, bx + sdx * blen, by,            fill=bc, width=2)
            c.create_line(bx, by, bx,               by + sdy * blen, fill=bc, width=2)

        if self._has_face:
            fw = int(FW * self.scale)
            if (self._face_scale_cache is None or
                    abs(self._face_scale_cache[0] - self.scale) > 0.004):
                scaled = self._face_pil.resize((fw, fw), Image.BILINEAR)
                tk_img = ImageTk.PhotoImage(scaled)
                self._face_scale_cache = (self.scale, tk_img)
            c.create_image(FCX, FCY, image=self._face_scale_cache[1])
        else:
            orb_r = int(FW * 0.27 * self.scale)
            for i in range(7, 0, -1):
                r2   = int(orb_r * i / 7)
                frac = i / 7
                ga   = max(0, min(255, int(self.halo_a * 1.1 * frac)))
                c.create_oval(FCX-r2, FCY-r2, FCX+r2, FCY+r2,
                              fill=self._ac(0, int(65*frac), int(120*frac), ga),
                              outline="")
            c.create_text(FCX, FCY, text=SYSTEM_NAME,
                          fill=self._ac(0, 212, 255, min(255, int(self.halo_a * 2))),
                          font=("Courier", 14, "bold"))

        self._draw_side_panels(c)

        self._rounded_rect(
            c,
            FCX - 210,
            FCY + FW // 2 + 12,
            FCX + 210,
            FCY + FW // 2 + 82,
            radius=24,
            fill=self._ac(10, 18, 26, 228),
            outline=self._ac(121, 240, 255, 18),
            width=1,
        )
        c.create_text(FCX, FCY + FW // 2 + 32, text="SYNTHETIC COGNITION CORE",
                      fill=C_TEXT_DIM, font=("Consolas", 8, "bold"))
        c.create_text(FCX, FCY + FW // 2 + 56, text=self._truncate(self.primary_signal, 42),
                      fill=C_ACC2 if self.speaking else C_PRI, font=("Bahnschrift SemiBold", 15))

        sy = FCY + FW // 2 + 98
        if self.speaking:
            stat, sc = "LIVE SYNTHESIS", C_ACC
        else:
            sym = "●" if self.status_blink else "○"
            stat, sc = f"{sym} {self.status_text}", C_PRI

        c.create_text(W // 2, sy, text=stat,
                      fill=sc, font=("Bahnschrift SemiBold", 14))
        c.create_text(W // 2, sy + 22, text=self._truncate(self.secondary_signal, 68),
                      fill=C_TEXT_DIM, font=("Consolas", 9))

        wy = sy + 38
        N  = 36
        BH = 24
        bw = 8
        total_w = N * bw
        wx0 = (W - total_w) // 2
        for i in range(N):
            hb  = random.randint(3, BH) if self.speaking else int(3 + 2 * math.sin(t * 0.08 + i * 0.55))
            col = (C_PRI if hb > BH * 0.6 else C_MID) if self.speaking else self._ac(90, 232, 255, 64 if i % 2 == 0 else 40)
            bx  = wx0 + i * bw
            c.create_rectangle(bx, wy + BH - hb, bx + bw - 1, wy + BH,
                                fill=col, outline="")

        c.create_line(18, H - 30, W - 18, H - 30, fill=self._ac(121, 240, 255, 28), width=1)
        c.create_text(16, H - 14, fill=C_TEXT_DIM, font=("Consolas", 8),
                      text="Mission-grade desktop shell // live voice + control", anchor="w")
        c.create_text(W - 16, H - 14, fill=C_TEXT_DIM, font=("Consolas", 8),
                      text=self._truncate(self.provider_binding_text, 54), anchor="e")

    def _draw_side_panels(self, canvas):
        left_x = 28
        right_x = self.W - 296
        upper_y = 96
        lower_y = 386
        panel_w = 268
        upper_h = 258
        lower_h = 238

        self._draw_panel(canvas, left_x, upper_y, panel_w, upper_h, "SYSTEM STATE", C_PRI)
        self._draw_panel(canvas, right_x, upper_y, panel_w, upper_h, "LIVE CONVERSATION", C_ACC2)
        self._draw_panel(canvas, left_x, lower_y, panel_w, lower_h, "SYSTEM EVENTS", C_GREEN)
        self._draw_panel(canvas, right_x, lower_y, panel_w, lower_h, "SESSION STATUS", C_PRI)

        canvas.create_text(left_x + 20, upper_y + 62, text="Access Profile",
                           fill=C_TEXT_DIM, font=("Consolas", 9), anchor="w")
        self._draw_badge(canvas, left_x + 20, upper_y + 74, "MODE", self.access_mode == "TRUSTED", width=108)
        self._draw_badge(canvas, left_x + 138, upper_y + 74, "ADMIN", self.admin_state == "ADMIN", width=108)

        canvas.create_text(left_x + 20, upper_y + 122, text="Capabilities",
                           fill=C_TEXT_DIM, font=("Consolas", 9), anchor="w")
        chip_y = upper_y + 134
        for idx, (label, value) in enumerate(self.capability_chips):
            row = idx // 2
            col = idx % 2
            chip_x = left_x + 20 + col * 118
            self._draw_badge(canvas, chip_x, chip_y + row * 38, label, value, width=108)

        canvas.create_text(left_x + 20, upper_y + 222, text="Provider Binding",
                           fill=C_TEXT_DIM, font=("Consolas", 9), anchor="w")
        canvas.create_text(left_x + 20, upper_y + 240,
                           text=self._truncate(self.provider_binding_text, 136),
                           fill=C_ACC2, font=("Consolas", 8, "bold"), anchor="w", width=panel_w - 40)
        canvas.create_text(left_x + 20, upper_y + 260, text="Summary",
                           fill=C_TEXT_DIM, font=("Consolas", 9), anchor="w")
        canvas.create_text(left_x + 20, upper_y + 278,
                           text=self._truncate(self.capability_summary, 120),
                           fill=C_TEXT, font=("Consolas", 9), anchor="nw", width=panel_w - 40)

        canvas.create_text(right_x + 20, upper_y + 62, text="Latest Directive",
                           fill=C_TEXT_DIM, font=("Consolas", 9), anchor="w")
        canvas.create_text(right_x + 20, upper_y + 84,
                           text=self._truncate(self.directive_text, 175),
                           fill="#f5fdff", font=("Consolas", 10), anchor="nw", width=panel_w - 40)
        canvas.create_line(right_x + 20, upper_y + 150, right_x + panel_w - 20, upper_y + 150, fill=self._ac(121, 240, 255, 28), width=1)
        canvas.create_text(right_x + 20, upper_y + 170, text="Latest Response",
                           fill=C_TEXT_DIM, font=("Consolas", 9), anchor="w")
        canvas.create_text(right_x + 20, upper_y + 192,
                           text=self._truncate(self.reply_text, 175),
                           fill=C_PRI, font=("Consolas", 10), anchor="nw", width=panel_w - 40)

        for idx, line in enumerate(list(self.system_events)[:4]):
            top = lower_y + 62 + idx * 28
            canvas.create_text(left_x + 20, top, text=f"{idx + 1:02d}",
                               fill=C_ACC2, font=("Consolas", 8, "bold"), anchor="w")
            canvas.create_text(left_x + 50, top,
                               text=self._truncate(line, 160),
                               fill=C_TEXT, font=("Consolas", 9), anchor="w")

        canvas.create_text(right_x + 20, lower_y + 62, text="Primary Signal",
                           fill=C_TEXT_DIM, font=("Consolas", 9), anchor="w")
        self._draw_badge(canvas, right_x + 20, lower_y + 74, "PRECHECK", self.precheck_state, width=108)
        self._draw_badge(canvas, right_x + 138, lower_y + 74, "SYNC", self.reload_state, width=108)
        self._draw_badge(canvas, right_x + 20, lower_y + 110, "OBSERVER", self.observer_enabled, width=108)
        self._draw_badge(canvas, right_x + 138, lower_y + 110, "VISION", self.vision_state, width=108)
        canvas.create_text(right_x + 20, lower_y + 154,
                           text=self._truncate(self.primary_signal, 148),
                           fill=C_ACC2 if self.speaking else C_PRI,
                           font=("Bahnschrift SemiBold", 12), anchor="w")
        canvas.create_text(right_x + 20, lower_y + 178, text="Secondary Signal",
                           fill=C_TEXT_DIM, font=("Consolas", 9), anchor="w")
        canvas.create_text(right_x + 20, lower_y + 196,
                           text=self._truncate(self.secondary_signal, 150),
                           fill=C_TEXT, font=("Consolas", 9), anchor="w", width=panel_w - 40)
        canvas.create_text(right_x + 20, lower_y + 216, text="Task Queue",
                           fill=C_TEXT_DIM, font=("Consolas", 9), anchor="w")
        canvas.create_text(right_x + 20, lower_y + 234,
                           text=self._truncate(self.task_status_text, 150),
                           fill=C_TEXT, font=("Consolas", 9), anchor="w", width=panel_w - 40)
        canvas.create_text(right_x + 20, lower_y + 252,
                           text=self._truncate(self.task_focus_text, 150),
                           fill=C_ACC2, font=("Consolas", 9), anchor="w", width=panel_w - 40)
        age_text = "No scene update yet."
        if self.observer_last_update_ts:
            age_text = f"Last scene update {max(0, int(time.time() - self.observer_last_update_ts))}s ago"
        canvas.create_text(right_x + 20, lower_y + 270,
                           text=self._truncate(age_text, 150),
                           fill=C_TEXT_DIM, font=("Consolas", 8), anchor="w", width=panel_w - 40)
        canvas.create_text(right_x + 20, lower_y + 288,
                           text=self._truncate(self.observer_last_action_text, 150),
                           fill=C_GREEN if "blocked" not in self.observer_last_action_text.lower() else C_RED,
                           font=("Consolas", 8), anchor="w", width=panel_w - 40)

    def write_log(self, text: str):
        line = (text or "").strip()
        self.typing_queue.append(text)
        tl = line.lower()

        if tl.startswith("you:"):
            content = line.split(":", 1)[1].strip()
            if content:
                self.directive_text = content
                self.primary_signal = "DIRECTIVE LOCKED"
                self.secondary_signal = "Routing task through mission core"
            self.precheck_state = None
            self.precheck_text = "Awaiting live precheck."
            self.precheck_label.configure(text="PRECHECK IDLE", fg=C_TEXT_DIM)
            self.status_text = "PROCESSING"
        elif tl.startswith("jarvis:") or tl.startswith("ai:"):
            content = line.split(":", 1)[1].strip()
            if content:
                self.reply_text = content
                if not self.speaking:
                    self.primary_signal = "RESPONSE COMMITTED"
                    self.secondary_signal = "Awaiting next directive"
            self.status_text = "RESPONDING"
        elif tl.startswith("precheck:"):
            content = line.split(":", 1)[1].strip()
            if content:
                self.precheck_state = True
                self.precheck_text = content
                self.primary_signal = "PRECHECK COMPLETE"
                self.secondary_signal = self._truncate(content, 68)
                self.system_events.appendleft(f"PRECHECK: {content}")
                self.precheck_label.configure(text="PRECHECK READY", fg=C_GREEN)
            self.status_text = "PROCESSING"
        elif tl.startswith("observe:"):
            content = line.split(":", 1)[1].strip()
            if content:
                self.observer_last_scene_text = content
                self.secondary_signal = self._truncate(content, 68)
                self.system_events.appendleft(f"OBSERVE: {content}")
            self.status_text = "ONLINE"
        elif tl.startswith("auto blocked:"):
            content = line.split(":", 1)[1].strip()
            if content:
                self.observer_last_action_text = f"Blocked: {content}"
                self.primary_signal = "AUTO BLOCKED"
                self.secondary_signal = self._truncate(content, 68)
                self.system_events.appendleft(f"AUTO BLOCKED: {content}")
            self.status_text = "ONLINE"
        elif tl.startswith("auto:"):
            content = line.split(":", 1)[1].strip()
            if content:
                self.observer_last_action_text = content
                self.primary_signal = "AUTONOMOUS ACTION"
                self.secondary_signal = self._truncate(content, 68)
                self.system_events.appendleft(f"AUTO: {content}")
            self.status_text = "ONLINE"
        else:
            payload = line[4:].strip() if tl.startswith("sys:") else line
            if payload:
                self.system_events.appendleft(payload)
                self._ingest_system_event(payload)

        if not self.is_typing:
            self._start_typing()

    def _ingest_system_event(self, payload: str):
        lower = payload.lower()

        if "trusted mode active with administrator" in lower:
            self.set_access_state(True, True)
        elif "trusted mode active" in lower or "trusted mode enabled" in lower:
            self.set_access_state(True, self.admin_state == "ADMIN")
        elif "normal mode" in lower or "limited mode" in lower:
            self.set_access_state(False, False)

        if "voice pipeline ready" in lower:
            self.primary_signal = "VOICE LINK READY"
            self.secondary_signal = "Live microphone bridge is armed"
        elif "requesting windows administrator access" in lower:
            self.primary_signal = "UAC HANDSHAKE"
            self.secondary_signal = "Waiting for Windows privilege approval"
        elif "relaunching with administrator access" in lower:
            self.primary_signal = "PRIVILEGE ESCALATION"
            self.secondary_signal = "Restarting shell with admin surface"
        elif "administrator request was denied" in lower:
            self.primary_signal = "STANDARD PRIVILEGE SURFACE"
            self.secondary_signal = "Continuing without admin session"
        elif "connection error" in lower:
            self.primary_signal = "LINK DEGRADED"
            self.secondary_signal = "Attempting reconnection"
        elif lower.startswith("precheck:"):
            self.precheck_state = True
            self.precheck_text = payload[9:].strip() or "Live context captured."
            self.precheck_label.configure(text="PRECHECK READY", fg=C_GREEN)
        elif lower.startswith("observe:"):
            self.observer_last_scene_text = payload[8:].strip() or self.observer_last_scene_text
            self.observer_last_update_ts = time.time()
        elif lower.startswith("auto blocked:"):
            self.observer_last_action_text = f"Blocked: {payload[13:].strip()}"
        elif lower.startswith("auto:"):
            self.observer_last_action_text = payload[5:].strip() or self.observer_last_action_text

    def _start_typing(self):
        if not self.typing_queue:
            self.is_typing = False
            if not self.speaking:
                self.status_text = "ONLINE"
            return
        self.is_typing = True
        text = self.typing_queue.popleft()
        tl   = text.lower()
        tag = "you"
        if tl.startswith("ai:") or tl.startswith("jarvis:"):
            tag = "ai"
        elif tl.startswith("precheck:"):
            tag = "precheck"
        elif tl.startswith("observe:"):
            tag = "observe"
        elif tl.startswith("auto blocked:"):
            tag = "autoblocked"
        elif tl.startswith("auto:"):
            tag = "auto"
        elif not tl.startswith("you:"):
            tag = "sys"
        self.log_text.configure(state="normal")
        self._type_char(text, 0, tag)

    def _type_char(self, text, i, tag):
        if i < len(text):
            self.log_text.insert(tk.END, text[i], tag)
            self.log_text.see(tk.END)
            self.root.after(8, self._type_char, text, i + 1, tag)
        else:
            self.log_text.insert(tk.END, "\n")
            self.log_text.configure(state="disabled")
            self.root.after(25, self._start_typing)

    def start_speaking(self):
        self.speaking    = True
        self.status_text = "SPEAKING"
        self.primary_signal = "VOICE SYNTH ACTIVE"
        self.secondary_signal = "Rendering spoken response"

    def stop_speaking(self):
        self.speaking    = False
        self.status_text = "ONLINE"
        self.secondary_signal = "Awaiting next directive"

    def _schedule_ui_update(self, func, *args, **kwargs):
        try:
            self.root.after(0, lambda: func(*args, **kwargs))
        except Exception:
            pass

    def _apply_reload_state(self, active, text: str):
        if active is True:
            self.reload_state = "RELOAD"
        elif active is False:
            self.reload_state = "FAIL"
        elif active in (None, ""):
            self.reload_state = "SYNC"
        else:
            self.reload_state = active
        self.reload_text = self._truncate(text, 120) if text else "Session sync stable."
        if self.reload_state == "RELOAD":
            self.secondary_signal = self.reload_text

    def set_reload_state(self, active, text: str):
        self._schedule_ui_update(self._apply_reload_state, active, text)

    def _apply_observer_state(self, payload: dict):
        payload = payload or {}
        self.observer_enabled = payload.get("enabled")
        self.vision_state = str(payload.get("vision_state", "PAUSED") or "PAUSED").upper()
        self.observer_mode_text = str(payload.get("mode", "always_on") or "always_on")
        self.observer_last_update_ts = float(payload.get("last_scene_update_ts", 0.0) or 0.0)
        summary = str(payload.get("last_scene_summary", "") or "").strip()
        if summary:
            self.observer_last_scene_text = summary
        action_text = str(payload.get("last_autonomous_action", "") or "").strip()
        if action_text:
            self.observer_last_action_text = action_text

    def set_observer_state(self, payload: dict):
        self._schedule_ui_update(self._apply_observer_state, payload)

    def _apply_task_snapshot(self, snapshot: dict):
        snapshot = snapshot or {}
        counts = snapshot.get("counts", {}) if isinstance(snapshot, dict) else {}
        running = int(counts.get("running", 0) or 0)
        pending = int(counts.get("pending", 0) or 0)
        completed = int(counts.get("completed", 0) or 0)
        failed = int(counts.get("failed", 0) or 0)
        self.task_status_text = f"Running {running} | Pending {pending} | Done {completed} | Failed {failed}"

        active_task = snapshot.get("active_task") if isinstance(snapshot, dict) else None
        next_task = snapshot.get("next_task") if isinstance(snapshot, dict) else None
        if active_task:
            self.task_focus_text = f"[{active_task.get('task_id', '?')}] {self._truncate(active_task.get('goal', ''), 80)}"
        elif next_task:
            self.task_focus_text = f"Next [{next_task.get('task_id', '?')}] {self._truncate(next_task.get('goal', ''), 76)}"
        else:
            self.task_focus_text = "No active background task."

    def set_task_snapshot(self, snapshot: dict):
        self._schedule_ui_update(self._apply_task_snapshot, snapshot)

    def note_task_event(self, task_id: str, status: str, message: str):
        status_upper = str(status or "").upper()
        self._schedule_ui_update(
            self.write_log,
            f"SYS: Task {task_id} {status_upper}: {message}",
        )

    def _poll_task_queue(self):
        try:
            from agent.task_queue import peek_queue_snapshot

            snapshot = peek_queue_snapshot()
            self._apply_task_snapshot(snapshot)
        except Exception:
            pass
        self.root.after(1200, self._poll_task_queue)

    def _refresh_permission_state(self):
        self._permission_state = load_permission_state()
        self._permission_ready = has_permission_consent()

    def _refresh_provider_binding(self):
        config = load_api_keys()
        provider = normalize_provider(
            config.get("live_provider_llm")
            if str(config.get("live_provider") or "").strip().lower() == "fallback"
            else config.get("live_provider") or config.get("llm_provider") or "gemini"
        )
        profile = _provider_profile(provider)
        model = str(config.get("openai_realtime_model") or config.get("llm_model") or profile["default_model"]).strip()
        self.provider_binding_text = f"{profile['label']} | {model}"
        if hasattr(self, "provider_label"):
            self.provider_label.configure(text=profile["label"].upper())

    def _api_keys_exist(self):
        return is_configured()

    def wait_for_api_key(self):
        """Block until API key is saved (called from main thread before starting JARVIS)."""
        while not self._api_key_ready:
            time.sleep(0.1)

    def wait_for_startup(self):
        while not (self._api_key_ready and self._permission_ready):
            time.sleep(0.1)

    def get_permission_state(self) -> dict:
        self._refresh_permission_state()
        return dict(self._permission_state)

    def _open_rebind_ui(self):
        self._show_setup_ui(edit_mode=True)

    def _show_setup_ui(self, edit_mode: bool = False):
        existing = getattr(self, "setup_frame", None)
        if existing is not None:
            try:
                existing.destroy()
            except Exception:
                pass

        self._setup_edit_mode = bool(edit_mode)
        config = load_api_keys()
        selected_provider = normalize_provider(
            config.get("live_provider_llm")
            if str(config.get("live_provider") or "").strip().lower() == "fallback"
            else config.get("live_provider") or config.get("llm_provider") or "gemini"
        )
        if selected_provider not in PROVIDER_PROFILES:
            selected_provider = "gemini"
        profile = _provider_profile(selected_provider)

        self.setup_frame = tk.Frame(
            self.root, bg=C_PANEL,
            highlightbackground=C_PRI if not edit_mode else C_ACC2, highlightthickness=1
        )
        self.setup_frame.place(relx=0.5, rely=0.5, anchor="center")
        tk.Frame(self.setup_frame, bg=C_PANEL2, height=40).pack(fill="x")
        setup_title = "SESSION REBIND" if edit_mode else "MISSION CORE BINDING"
        setup_accent = C_ACC2 if edit_mode else C_PRI
        tk.Label(
            self.setup_frame,
            text=setup_title,
            fg=setup_accent,
            bg=C_PANEL2,
            font=("Bahnschrift SemiBold", 13),
        ).place(x=18, y=7)

        tk.Label(
            self.setup_frame,
            text=(
                "Choose the live provider, bind the matching API key, and set the primary model. "
                "OpenAI uses realtime voice. Anthropic, OpenRouter, and GitHub Models use the fallback speech loop."
            ),
            fg=C_TEXT,
            bg=C_PANEL,
            font=("Consolas", 10),
            wraplength=640,
            justify="left",
        ).pack(padx=22, pady=(22, 14), anchor="w")

        form = tk.Frame(self.setup_frame, bg=C_PANEL)
        form.pack(fill="x", padx=22, pady=(0, 12))

        self.provider_var = tk.StringVar(value=selected_provider)
        self.model_var = tk.StringVar(
            value=str(config.get("openai_realtime_model") or config.get("llm_model") or profile["default_model"]).strip()
        )

        tk.Label(form, text="LIVE PROVIDER",
                 fg=C_TEXT_DIM, bg=C_PANEL, font=("Consolas", 9, "bold")).grid(row=0, column=0, sticky="w")
        provider_menu = tk.OptionMenu(form, self.provider_var, *PROVIDER_PROFILES.keys(), command=self._apply_setup_provider)
        provider_menu.config(
            bg="#0b2330",
            fg=C_PRI,
            activebackground="#123240",
            activeforeground=C_PRI,
            font=("Consolas", 10),
            highlightthickness=0,
            borderwidth=0,
            width=18,
        )
        provider_menu["menu"].config(
            bg=C_PANEL2,
            fg=C_TEXT,
            activebackground="#123240",
            activeforeground=C_PRI,
            font=("Consolas", 10),
        )
        provider_menu.grid(row=1, column=0, sticky="w", pady=(6, 14))

        tk.Label(form, text="MODEL",
                 fg=C_TEXT_DIM, bg=C_PANEL, font=("Consolas", 9, "bold")).grid(row=0, column=1, sticky="w", padx=(22, 0))
        self.model_entry = tk.Entry(
            form, width=34, textvariable=self.model_var, fg=C_TEXT, bg="#081923",
            insertbackground=C_TEXT, borderwidth=0, font=("Consolas", 10)
        )
        self.model_entry.grid(row=1, column=1, sticky="w", padx=(22, 0), pady=(6, 14))

        self.api_key_label = tk.Label(
            form, text=profile["key_label"],
            fg=C_TEXT_DIM, bg=C_PANEL, font=("Consolas", 9, "bold")
        )
        self.api_key_label.grid(row=2, column=0, sticky="w", columnspan=2)
        self.api_entry = tk.Entry(
            form, width=68, fg=C_TEXT, bg="#081923",
            insertbackground=C_TEXT, borderwidth=0, font=("Consolas", 10), show="*"
        )
        self.api_entry.grid(row=3, column=0, sticky="w", columnspan=2, pady=(6, 8))

        self.setup_note_label = tk.Label(
            form,
            text=profile["note"],
            fg=C_TEXT_DIM,
            bg=C_PANEL,
            font=("Consolas", 9),
            justify="left",
            wraplength=640,
        )
        self.setup_note_label.grid(row=4, column=0, sticky="w", columnspan=2)

        btn_row = tk.Frame(self.setup_frame, bg=C_PANEL)
        btn_row.pack(fill="x", padx=22, pady=(6, 18))

        if edit_mode:
            tk.Button(
                btn_row, text="CANCEL",
                command=self.setup_frame.destroy,
                bg="#12202a", fg=C_TEXT_DIM,
                activebackground="#1a2d39", activeforeground=C_TEXT,
                font=("Bahnschrift SemiBold", 11),
                borderwidth=0, padx=14, pady=10
            ).pack(side="left")

        tk.Button(
            btn_row, text="BIND AND RESTART" if edit_mode else "INITIALISE MISSION CORE",
            command=self._save_api_keys, bg="#0d2833", fg=C_PRI,
            activebackground="#173848", activeforeground=C_PRI,
            font=("Bahnschrift SemiBold", 11),
            borderwidth=0, padx=14, pady=10
        ).pack(side="right")

        self._apply_setup_provider(selected_provider)

    def _apply_setup_provider(self, provider: str):
        profile = _provider_profile(provider)
        if hasattr(self, "api_key_label"):
            self.api_key_label.configure(text=profile["key_label"])
        if hasattr(self, "setup_note_label"):
            self.setup_note_label.configure(text=profile["note"])
        if hasattr(self, "model_var"):
            current = str(self.model_var.get() or "").strip()
            if not current or current in {item["default_model"] for item in PROVIDER_PROFILES.values()}:
                self.model_var.set(profile["default_model"])

    def _restart_application(self):
        self.write_log("SYS: Restarting JARVIS shell with the new provider binding.")
        self.root.update_idletasks()
        executable, argv = build_restart_command(sys.argv[1:], base_dir=BASE_DIR)
        os.execv(executable, argv)

    def _show_permission_ui(self):
        self.permission_frame = tk.Frame(
            self.root, bg=C_PANEL,
            highlightbackground=C_ACC2, highlightthickness=1
        )
        self.permission_frame.place(relx=0.5, rely=0.5, anchor="center")
        tk.Frame(self.permission_frame, bg=C_PANEL2, height=36).pack(fill="x")
        tk.Label(self.permission_frame, text="DESKTOP ACCESS AUTHORIZATION",
                 fg=C_ACC2, bg=C_PANEL2, font=("Bahnschrift SemiBold", 13)).place(x=18, y=6)

        tk.Label(
            self.permission_frame,
            text=(
                "Trusted Mode turns this console into a live desktop shell. "
                "J.A.R.V.I.S may control apps, browser tabs, files, keyboard, mouse, "
                "screen capture, webcam, reminders, system settings, code upgrades, and local commands."
            ),
            fg=C_TEXT,
            bg=C_PANEL,
            font=("Consolas", 10),
            wraplength=620,
            justify="left"
        ).pack(padx=22, pady=(20, 10), anchor="w")

        tk.Label(
            self.permission_frame,
            text=(
                "Normal Mode keeps full-system control locked, but still allows everyday actions "
                "like web search, opening apps, messaging, safe browser usage, and read-only file/system lookups. "
                "Windows administrator access is optional and still depends on the OS UAC prompt."
            ),
            fg=C_TEXT_DIM,
            bg=C_PANEL,
            font=("Consolas", 9),
            wraplength=620,
            justify="left"
        ).pack(padx=22, pady=(0, 14), anchor="w")

        self.admin_var = tk.IntVar(
            value=1 if self._permission_state.get("request_admin_on_start") else 0
        )
        tk.Checkbutton(
            self.permission_frame,
            text="Ask Windows for administrator access on startup when Trusted Mode is enabled",
            variable=self.admin_var,
            fg=C_PRI,
            bg=C_PANEL,
            activebackground=C_PANEL,
            activeforeground=C_PRI,
            selectcolor="#081923",
            font=("Consolas", 9),
            wraplength=620,
            justify="left"
        ).pack(padx=22, pady=(0, 18), anchor="w")

        btn_row = tk.Frame(self.permission_frame, bg=C_PANEL)
        btn_row.pack(fill="x", padx=22, pady=(0, 18))

        tk.Button(
            btn_row,
            text="NORMAL MODE",
            command=lambda: self._save_permission_choice("normal"),
            bg="#091820",
            fg=C_TEXT_DIM,
            activebackground="#10232d",
            activeforeground=C_TEXT,
            font=("Bahnschrift SemiBold", 11),
            borderwidth=0,
            padx=14,
            pady=10
        ).pack(side="left")

        tk.Button(
            btn_row,
            text="TRUSTED MODE",
            command=lambda: self._save_permission_choice("trusted"),
            bg="#2a1a08",
            fg=C_ACC2,
            activebackground="#4a2b0d",
            activeforeground=C_ACC2,
            font=("Bahnschrift SemiBold", 11),
            borderwidth=0,
            padx=14,
            pady=10
        ).pack(side="right")

    def _save_api_keys(self):
        provider = normalize_provider(getattr(self, "provider_var", tk.StringVar(value="gemini")).get())
        api_key = self.api_entry.get().strip() if hasattr(self, "api_entry") else ""
        model = self.model_entry.get().strip() if hasattr(self, "model_entry") else ""
        if not api_key:
            return
        payload = build_api_binding_payload(provider, api_key, model)
        save_api_keys_config(**payload)
        self._refresh_provider_binding()
        self.setup_frame.destroy()
        self._api_key_ready = True
        self.status_text = "INITIALISING"
        self.primary_signal = "SESSION BINDING SAVED"
        self.secondary_signal = self._truncate(self.provider_binding_text, 68)
        self.write_log(f"SYS: Provider binding saved: {self.provider_binding_text}")
        if self._setup_edit_mode:
            self.root.after(300, self._restart_application)
            return
        self._refresh_permission_state()
        if not self._permission_ready:
            self._show_permission_ui()
        else:
            self.status_text = "ONLINE"
            self.write_log("SYS: JARVIS online.")

    def _save_permission_choice(self, mode: str):
        trusted_mode = str(mode).strip().lower() == "trusted"
        request_admin = bool(self.admin_var.get()) if trusted_mode else False
        save_permission_state(
            mode="trusted" if trusted_mode else "normal",
            request_admin_on_start=request_admin,
        )
        self.permission_frame.destroy()
        self._refresh_permission_state()
        self.set_access_state(trusted_mode, False)
        self.status_text = "ONLINE"

        if trusted_mode:
            self.primary_signal = "TRUSTED SURFACE ARMED"
            self.secondary_signal = "Local desktop control has been authorised"
            self.write_log("SYS: Trusted mode enabled. Local desktop control authorised.")
            if request_admin:
                self.write_log("SYS: Windows administrator access will be requested on startup.")
        else:
            self.primary_signal = "NORMAL SURFACE LOCKED"
            self.secondary_signal = "Everyday safe actions remain available"
            self.write_log("SYS: Normal mode enabled. Full desktop control remains locked.")

    # ── Metrics Dashboard ─────────────────────────────────────────────────────

    def _on_daemon_step(self, event):
        """Phase 9: React to background OperatorDaemon step events in the HUD."""
        def ui_update():
            # Only log interesting non-skipped events
            if event.status != "skipped":
                self.write_log(f"SYS: DAEMON [{event.status.upper()}] Step {event.step}: {event.summary}")
            
            # Update the side-panel HUD labels
            if event.status == "success":
                self.observer_last_action_text = f"Act: {event.action} -> {event.target} ({event.confidence:.2f})"
                self.primary_signal = f"STEP {event.step} SUCCESS"
                self.secondary_signal = self._truncate(event.summary, 68)
            elif event.status == "failed":
                self.observer_last_action_text = f"Fail: {event.action} -> {event.target}"
                self.primary_signal = "STEP FAILED"
                self.secondary_signal = self._truncate(event.summary, 68)

        if hasattr(self, "root") and self.root:
            self.root.after(0, ui_update)

    def _poll_metrics(self):
        """Refresh dashboard every 30s if it's open."""
        if self._dash_visible and self._dash_win and self._dash_win.winfo_exists():
            self._refresh_dashboard()
        self.root.after(30_000, self._poll_metrics)

    def _toggle_dashboard(self):
        if self._dash_visible and self._dash_win and self._dash_win.winfo_exists():
            self._dash_win.destroy()
            self._dash_win = None
            self._dash_visible = False
            self._dash_btn.configure(fg=C_ACC2, bg=C_DIM)
        else:
            self._open_dashboard()

    def _open_dashboard(self):
        win = tk.Toplevel(self.root)
        win.title("JARVIS — Trace Metrics Dashboard")
        win.configure(bg=C_BG)
        win.geometry("780x560")
        win.resizable(True, True)
        win.protocol("WM_DELETE_WINDOW", self._toggle_dashboard)
        self._dash_win = win
        self._dash_visible = True
        self._dash_btn.configure(fg=C_GREEN, bg=C_DIMMER)

        # Header
        hdr = tk.Frame(win, bg="#041017", height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="📊  JARVIS TRACE METRICS", fg=C_PRI,
                 bg="#041017", font=("Bahnschrift SemiBold", 16)).pack(side="left", padx=18, pady=8)
        tk.Button(
            hdr, text="⟳ REFRESH", command=self._refresh_dashboard,
            bg=C_DIM, fg=C_ACC2, activebackground=C_MID,
            font=("Consolas", 9, "bold"), borderwidth=0, padx=10, pady=4, cursor="hand2",
        ).pack(side="right", padx=12, pady=8)

        # Session summary row
        self._dash_summary_frame = tk.Frame(win, bg=C_BG2)
        self._dash_summary_frame.pack(fill="x", padx=0, pady=0)

        # Main body — two columns
        body = tk.Frame(win, bg=C_BG)
        body.pack(fill="both", expand=True, padx=12, pady=8)

        # Left: top tools table
        left = tk.Frame(body, bg=C_PANEL, highlightbackground=C_LINE, highlightthickness=1)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Label(left, text="TOP TOOLS BY USAGE", fg=C_PRI, bg=C_PANEL2,
                 font=("Bahnschrift SemiBold", 11)).pack(fill="x", ipady=6)
        self._dash_tools_text = tk.Text(
            left, fg=C_TEXT, bg=C_PANEL, borderwidth=0,
            font=("Consolas", 9), padx=10, pady=8, state="disabled", wrap="none"
        )
        self._dash_tools_text.pack(fill="both", expand=True)
        self._dash_tools_text.tag_config("head", foreground=C_TEXT_DIM)
        self._dash_tools_text.tag_config("good", foreground=C_GREEN)
        self._dash_tools_text.tag_config("mid",  foreground=C_ACC2)
        self._dash_tools_text.tag_config("bad",  foreground=C_RED)

        # Right: summary + self-evolve stats
        right = tk.Frame(body, bg=C_BG, width=260)
        right.pack(side="right", fill="y", padx=(6, 0))
        right.pack_propagate(False)

        # Self-evolve panel
        se_frame = tk.Frame(right, bg=C_PANEL, highlightbackground=C_LINE, highlightthickness=1)
        se_frame.pack(fill="x", pady=(0, 8))
        tk.Label(se_frame, text="SELF-EVOLVE STATS", fg=C_ACC2, bg=C_PANEL2,
                 font=("Bahnschrift SemiBold", 11)).pack(fill="x", ipady=6)
        self._dash_se_text = tk.Text(
            se_frame, fg=C_TEXT, bg=C_PANEL, borderwidth=0,
            font=("Consolas", 9), padx=10, pady=8, state="disabled", height=8, wrap="none"
        )
        self._dash_se_text.pack(fill="x")
        self._dash_se_text.tag_config("good", foreground=C_GREEN)
        self._dash_se_text.tag_config("bad",  foreground=C_RED)
        self._dash_se_text.tag_config("mid",  foreground=C_ACC2)

        # Blocked tools panel
        bl_frame = tk.Frame(right, bg=C_PANEL, highlightbackground=C_LINE, highlightthickness=1)
        bl_frame.pack(fill="x")
        tk.Label(bl_frame, text="TOP BLOCKED TOOLS", fg=C_RED, bg=C_PANEL2,
                 font=("Bahnschrift SemiBold", 11)).pack(fill="x", ipady=6)
        self._dash_blocked_text = tk.Text(
            bl_frame, fg=C_TEXT, bg=C_PANEL, borderwidth=0,
            font=("Consolas", 9), padx=10, pady=8, state="disabled", height=6, wrap="none"
        )
        self._dash_blocked_text.pack(fill="x")

        self._refresh_dashboard()

    def _refresh_dashboard(self):
        try:
            from core.metrics_tracker import get_dashboard_data
            data = get_dashboard_data(top_n=15)
        except Exception as e:
            return

        summary = data.get("summary", {})
        tools   = data.get("top_tools", [])
        blocked = data.get("blocked_tools", [])
        se      = data.get("self_evolve", {})

        # ── Summary row ──────────────────────────────────────────────────────
        for w in self._dash_summary_frame.winfo_children():
            w.destroy()

        rate     = summary.get("success_rate_pct", 0.0)
        total    = summary.get("total_calls", 0)
        n_block  = summary.get("total_blocked", 0)
        n_fail   = summary.get("total_failed", 0)
        rate_col = C_GREEN if rate >= 80 else (C_ACC2 if rate >= 50 else C_RED)
        updated  = str(summary.get("updated_at", ""))[:16]

        for lbl, val, col in [
            ("TOTAL CALLS", str(total), C_PRI),
            ("SUCCESS RATE", f"{rate}%", rate_col),
            ("BLOCKED", str(n_block), C_RED if n_block else C_TEXT_DIM),
            ("FAILED", str(n_fail), C_RED if n_fail else C_TEXT_DIM),
            ("UPDATED", updated, C_TEXT_DIM),
        ]:
            cell = tk.Frame(self._dash_summary_frame, bg=C_PANEL2,
                            highlightbackground=C_LINE, highlightthickness=1)
            cell.pack(side="left", ipadx=14, ipady=8, padx=2, pady=4)
            tk.Label(cell, text=lbl, fg=C_TEXT_DIM, bg=C_PANEL2,
                     font=("Consolas", 7, "bold")).pack()
            tk.Label(cell, text=val, fg=col, bg=C_PANEL2,
                     font=("Bahnschrift SemiBold", 16)).pack()

        # ── Tools table ──────────────────────────────────────────────────────
        self._dash_tools_text.configure(state="normal")
        self._dash_tools_text.delete("1.0", tk.END)
        header = f"{'TOOL':<22} {'CALLS':>6} {'OK':>6} {'FAIL':>6} {'BLOK':>6} {'RATE':>7}\n"
        self._dash_tools_text.insert(tk.END, header, "head")
        self._dash_tools_text.insert(tk.END, "─" * 60 + "\n", "head")
        for t in tools:
            rate_t = t.get("rate", 0)
            tag = "good" if rate_t >= 80 else ("mid" if rate_t >= 50 else "bad")
            line = (
                f"{t['name']:<22} {t['calls']:>6} {t['success']:>6} "
                f"{t['failed']:>6} {t['blocked']:>6} {rate_t:>6.1f}%\n"
            )
            self._dash_tools_text.insert(tk.END, line, tag)
        self._dash_tools_text.configure(state="disabled")

        # ── Self-evolve ──────────────────────────────────────────────────────
        self._dash_se_text.configure(state="normal")
        self._dash_se_text.delete("1.0", tk.END)
        accept_rate = se.get("review_accept_rate_pct", 0)
        ar_tag = "good" if accept_rate >= 60 else ("mid" if accept_rate >= 30 else "bad")
        lines = [
            (f"Total runs:        {se.get('total_runs', 0)}", "mid"),
            (f"Review required:   {se.get('review_required', 0)}", "mid"),
            (f"Accepted:          {se.get('accepted', 0)}", "good"),
            (f"Applied:           {se.get('applied', 0)}", "good"),
            (f"Rejected:          {se.get('rejected', 0)}", "bad"),
            (f"Accept rate:       {accept_rate}%", ar_tag),
            (f"Last run:          {str(se.get('last_run', 'Never'))[:16]}", "mid"),
        ]
        for text, tag in lines:
            self._dash_se_text.insert(tk.END, text + "\n", tag)
        self._dash_se_text.configure(state="disabled")

        # ── Blocked tools ────────────────────────────────────────────────────
        self._dash_blocked_text.configure(state="normal")
        self._dash_blocked_text.delete("1.0", tk.END)
        if blocked:
            for t in blocked:
                self._dash_blocked_text.insert(
                    tk.END,
                    f"  {t['name']:<20} blocked: {t['blocked']}  ({t['rate']:.1f}% OK)\n"
                )
        else:
            self._dash_blocked_text.insert(tk.END, "  No blocked tools recorded yet.\n")
        self._dash_blocked_text.configure(state="disabled")
