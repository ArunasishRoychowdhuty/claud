from __future__ import annotations

import asyncio
import ctypes
import threading
import json
import os
import re
import subprocess
import sys
import traceback
from collections import deque
from pathlib import Path

import numpy as np
import pyaudio
import time
from ui import JarvisUI
from memory.context_builder import build_runtime_memory_context
from memory.config_manager import is_auto_memory_enabled, load_live_observer_state
from memory.extractor import apply_conversation_learning
from memory.working_memory import (
    get_last_live_context_summary,
    record_tool_result,
    remember_multimodal_anchor,
    set_current_task,
    set_current_user_text,
)
from core.live_reload import consume_live_reload_request, request_live_reload
from core.live_screen_observer import get_live_screen_observer
from core.capabilities import (
    detect_system_capabilities,
    format_capabilities_for_prompt,
    summarize_capabilities,
    tool_capability_issue,
)
from core.live_context_policy import text_requires_explicit_execution, tool_needs_live_context
from core.python_runtime import ensure_windows_runtime_env, resolve_python_executable
from core.resource_monitor import start_monitoring, get_resource_status, is_system_safe
from core.system_inventory import refresh_system_inventory

ensure_windows_runtime_env()

from agent.task_queue import get_queue
from agent.self_evolve import (
    SelfEvolvingAgent,
    evolve_if_needed,
    get_evolution_status,
    get_evolved_tool_declarations,
    register_evolved_skill,
    run_evolved_skill,
)

from actions.flight_finder import flight_finder
from actions.open_app         import open_app
from actions.weather_report   import weather_action
from actions.send_message     import send_message
from actions.reminder         import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor import screen_process
from actions.youtube_video    import youtube_video
from actions.cmd_control      import cmd_control
from actions.desktop          import desktop_control
from actions.desktop_operator import desktop_operator
from actions.browser_control  import browser_control
from actions.live_context     import live_context
from actions.memory_control   import memory_control
from actions.observer_control import observer_control
from actions.file_controller  import file_controller
from actions.code_helper      import code_helper
from actions.dev_agent        import dev_agent
from actions.web_search       import web_search as web_search_action
from actions.computer_control import computer_control
from actions.uia_executor     import uia_executor
from actions.self_evolve_action import (
    self_evolve_action,
    get_evolve_status_action,
    configure_evolve_action,
    apply_skill_action,
    rollback_skill_action,
)
from core.metrics_tracker import record_tool_call as _metrics_record_tool_call
from core.live_provider import (
    create_live_provider as _create_live_provider,
    get_provider_info as _get_live_provider_info,
    resolve_live_provider as _resolve_live_provider,
)

def _configure_console_output() -> None:
    """Avoid UnicodeEncodeError on Windows consoles when logs include emoji."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if not stream or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_configure_console_output()

_google_genai = None
_google_genai_types = None
_google_genai_error: Exception | None = None


def _load_google_genai():
    global _google_genai, _google_genai_types, _google_genai_error
    if _google_genai is not None and _google_genai_types is not None:
        return _google_genai, _google_genai_types
    try:
        from google import genai as imported_genai
        from google.genai import types as imported_types
    except Exception as exc:
        _google_genai_error = exc
        raise RuntimeError(
            "google-genai is required for Gemini live voice mode. Install the dependency or switch the live provider."
        ) from exc
    _google_genai = imported_genai
    _google_genai_types = imported_types
    _google_genai_error = None
    return _google_genai, _google_genai_types


class _LazyGenaiProxy:
    def __getattr__(self, name: str):
        module, _ = _load_google_genai()
        return getattr(module, name)


class _LazyGenaiTypesProxy:
    def __getattr__(self, name: str):
        _, module = _load_google_genai()
        return getattr(module, name)


genai = _LazyGenaiProxy()
types = _LazyGenaiTypesProxy()

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
FORMAT              = pyaudio.paInt16
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024
INPUT_AUDIO_MIME    = f"audio/pcm;rate={SEND_SAMPLE_RATE}"
VAD_PRE_ROLL_CHUNKS = 8
VAD_START_CHUNKS    = 2
VAD_END_CHUNKS      = 14
VAD_MIN_RMS         = 280.0
VAD_NOISE_MULTIPLIER = 3.0

pya = pyaudio.PyAudio()

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _retry_with_backoff(func, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
    """
    Retry a function with exponential backoff.

    Args:
        func: Function to retry (should be callable)
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds (default: 1s)
        max_delay: Maximum delay between retries (default: 30s)

    Returns:
        Function result or raises last exception

    Backoff formula: delay = min(base_delay * (2 ^ attempt), max_delay)
    Attempt delays: 1s, 2s, 4s, 8s, 16s, 30s, 30s...
    """
    import random

    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exception = e
            error_str = str(e)

            # Rate limit error (429) - always retry with backoff
            if "429" in error_str or "quota" in error_str.lower() or "rate limit" in error_str.lower():
                if attempt < max_retries:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    jitter = random.uniform(0, delay * 0.1)
                    total_delay = delay + jitter
                    print(f"[Retry] ⏳ Rate limited. Waiting {total_delay:.1f}s before retry {attempt + 1}/{max_retries}")
                    time.sleep(total_delay)
                    continue
                else:
                    print(f"[Retry] ❌ Rate limit exceeded after {max_retries} retries")
                    raise

            # Other errors - don't retry
            print(f"[Retry] ⚠️ Error (not retrying): {error_str[:100]}")
            raise

    if last_exception:
        raise last_exception


class SessionReloadRequested(Exception):
    pass

def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_memory_turn_counter  = 0
_memory_turn_lock     = threading.Lock()
_MEMORY_EVERY_N_TURNS = 3
_last_memory_input    = ""

NORMAL_MODE_ALLOWED_TOOLS = {
    "open_app",
    "web_search",
    "weather_report",
    "send_message",
    "screen_process",
    "live_context",
    "observer_control",
    "memory_control",
    "cmd_control",
    "evolve_status",
}
NORMAL_MODE_ALLOWED_YOUTUBE_ACTIONS = {"play", "summarize", "get_info", "trending"}
NORMAL_MODE_ALLOWED_BROWSER_ACTIONS = {"go_to", "search", "get_text", "list_tabs", "close"}
NORMAL_MODE_ALLOWED_FILE_ACTIONS = {"list", "read", "find", "largest", "disk_usage", "info"}
NORMAL_MODE_ALLOWED_DESKTOP_ACTIONS = {"list", "stats", "current_wallpaper"}


def _normal_mode_allows(name: str, args: dict) -> bool:
    if name in NORMAL_MODE_ALLOWED_TOOLS:
        return True

    if name == "youtube_video":
        action = str(args.get("action", "play")).lower().strip() or "play"
        return action in NORMAL_MODE_ALLOWED_YOUTUBE_ACTIONS

    if name == "browser_control":
        action = str(args.get("action", "")).lower().strip()
        return action in NORMAL_MODE_ALLOWED_BROWSER_ACTIONS

    if name == "file_controller":
        action = str(args.get("action", "")).lower().strip()
        return action in NORMAL_MODE_ALLOWED_FILE_ACTIONS

    if name == "desktop_control":
        action = str(args.get("action", "")).lower().strip()
        return action in NORMAL_MODE_ALLOWED_DESKTOP_ACTIONS

    return False


def _is_running_as_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _request_admin_relaunch() -> bool:
    if sys.platform != "win32":
        return False

    try:
        if getattr(sys, "frozen", False):
            current = Path(str(sys.executable or "")).expanduser()
            executable = str(current.resolve()) if current.exists() else resolve_python_executable()
            params = subprocess.list2cmdline(sys.argv[1:])
            cwd = str(Path(executable).parent)
        else:
            current = Path(str(sys.executable or "")).expanduser()
            executable = str(current.resolve()) if current.exists() else resolve_python_executable()
            params = subprocess.list2cmdline([str(Path(__file__).resolve()), *sys.argv[1:]])
            cwd = str(BASE_DIR)

        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            executable,
            params,
            cwd,
            1,
        )
        return result > 32
    except Exception:
        return False


def _update_memory_async(user_text: str, jarvis_text: str) -> None:
    """
    Multilingual memory updater.
    Behavior preferences are learned from every turn.
    Rich fact extraction runs periodically to avoid noisy or expensive writes.
    """
    global _memory_turn_counter, _last_memory_input

    if not is_auto_memory_enabled():
        return

    text = user_text.strip()
    if len(text) < 3:
        return

    behavior_result = apply_conversation_learning(
        text,
        jarvis_text,
        source="conversation_behavior",
    )
    reload_reason = ""
    if behavior_result.get("behavior_updates"):
        print(f"[Memory] 🧭 Behavior updated: {behavior_result['behavior_updates']}")
        reload_reason = "Behavior memory updated from the current conversation."

    with _memory_turn_lock:
        _memory_turn_counter += 1
        current_count = _memory_turn_counter

    if current_count % _MEMORY_EVERY_N_TURNS != 0:
        return

    if len(text) < 10:
        return
    if text == _last_memory_input:
        return
    _last_memory_input = text

    try:
        learning_result = apply_conversation_learning(
            text,
            jarvis_text,
            source="conversation_profile",
        )
        if learning_result.get("memory_saved"):
            print("[Memory] ✅ Profile memory updated")
            if reload_reason:
                reload_reason = "Behavior and profile memory updated from the current conversation."
            else:
                reload_reason = "Profile memory updated from the current conversation."
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ {e}")
    finally:
        if reload_reason:
            request_live_reload(reload_reason)


TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the Windows computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
{
    "name": "web_search",
    "description": "Searches the web for any information.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query":  {"type": "STRING", "description": "Search query"},
            "mode":   {"type": "STRING", "description": "search (default) or compare"},
            "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
            "aspect": {"type": "STRING", "description": "price | specs | reviews"}
        },
        "required": ["query"]
    }
},
    {
        "name": "weather_report",
        "description": "Gets real-time weather information for a city.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Windows Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
    "name": "youtube_video",
    "description": (
        "Controls YouTube. Use for: playing videos, summarizing a video's content, "
        "getting video info, or showing trending videos. For music playback on Windows, it may prefer a native desktop media app first and use YouTube as fallback."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "play | summarize | get_info | trending (default: play; 'search' is treated as play)"
            },
            "query":  {"type": "STRING", "description": "Search query for play action"},
            "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
            "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
            "url":    {"type": "STRING", "description": "Video URL for get_info action"},
        },
        "required": []
    }
    },
    {
        "name": "screen_process",
        "description": (
            "Queries the always-on live screen observer for the current screen state, "
            "or analyzes the webcam image when angle='camera'. "
            "Use this when the user asks what is on screen, what you see, "
            "or wants a visual analysis before acting. "
            "This is an analysis-only tool. It does NOT click, type, play, or control the app by itself."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {
                    "type": "STRING",
                    "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"
                },
                "text": {
                    "type": "STRING",
                    "description": "The question or instruction about the captured image"
                },
                "force_refresh": {
                    "type": "BOOLEAN",
                    "description": "Force the live observer to refresh before answering. Default: false"
                }
            },
            "required": ["text"]
        }
    },
    {
        "name": "live_context",
        "description": (
            "Captures a live read-only snapshot of the current laptop state from the always-on observer cache: active window, visible windows, "
            "top running processes, browser tabs from existing Windows browser windows when detectable, "
            "JARVIS-managed browser tabs, observer scene summary, visible targets, blockers, and optional suggested actions. "
            "Use this before acting when the task depends on what is currently open on the computer."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "question": {"type": "STRING", "description": "What to focus on while analyzing the current laptop state"},
                "include_screen": {"type": "BOOLEAN", "description": "Include observer scene summary, targets, blockers, and suggestions. Default: true"},
                "include_windows": {"type": "BOOLEAN", "description": "Include visible windows. Default: true"},
                "include_processes": {"type": "BOOLEAN", "description": "Include running process summary. Default: true"},
                "include_browser": {"type": "BOOLEAN", "description": "Include JARVIS-managed browser tabs. Default: true"},
                "force_refresh": {"type": "BOOLEAN", "description": "Force the live observer to refresh before answering. Default: false"},
                "max_windows": {"type": "INTEGER", "description": "Maximum visible windows to list. Default: 10"},
                "max_processes": {"type": "INTEGER", "description": "Maximum processes to summarize. Default: 12"},
                "max_tabs": {"type": "INTEGER", "description": "Maximum browser tabs to list. Default: 10"}
            },
            "required": []
        }
    },
    {
        "name": "observer_control",
        "description": (
            "Controls the always-on live screen observer. "
            "Use this to inspect observer status, pause/resume live vision, force a refresh, "
            "or update observer mode settings."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "status | pause | resume | refresh | set_mode"},
                "question": {"type": "STRING", "description": "Optional focus question for refresh"},
                "enabled": {"type": "BOOLEAN", "description": "Enable or disable the observer when action=set_mode"},
                "mode": {"type": "STRING", "description": "Observer mode, e.g. always_on"},
                "latency": {"type": "STRING", "description": "Latency profile, e.g. balanced"},
                "narration": {"type": "STRING", "description": "Narration mode: off (silent observer, recommended) or live"},
                "autonomy": {"type": "STRING", "description": "Autonomy mode, e.g. full_autonomous"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "memory_control",
        "description": (
            "Manages JARVIS memory. Use this when the user wants something remembered, forgotten, shown, "
            "or when they ask to review preferences, recent tasks, or memory stats."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "remember | forget | show | search | recent_tasks | preferences | stats | inventory"
                },
                "text": {"type": "STRING", "description": "Raw text to remember or forget"},
                "query": {"type": "STRING", "description": "Search or forget query"},
                "category": {"type": "STRING", "description": "identity | preferences | relationships | notes"},
                "key": {"type": "STRING", "description": "Optional exact memory key"},
                "refresh": {"type": "BOOLEAN", "description": "Refresh the cached system inventory before returning it"},
            },
            "required": ["action"]
        }
    },
    {
    "name": "computer_settings",
    "description": (
        "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
        "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
        "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page, and safe native performance optimization. "
        "ALSO use for repeated actions: 'refresh 10 times', 'reload page 5 times' → action: reload_n, value: 10. "
        "Use for ANY single computer control command — even if repeated N times. "
        "Prefer this and other OS-native tools before browser fallbacks on Windows. "
        "NEVER route simple computer commands to agent_task."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action":      {"type": "STRING", "description": "The action to perform (if known). For repeated reload: 'reload_n'"},
            "description": {"type": "STRING", "description": "Natural language description of what to do"},
            "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, number of times, etc."}
        },
        "required": []
    }
},
    {
        "name": "browser_control",
        "description": (
            "Controls the web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, finding cheapest products, "
            "booking flights, any web-based task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | list_tabs | press | close"},
                "url":         {"type": "STRING", "description": "URL for go_to action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up or down for scroll"},
                "key":         {"type": "STRING", "description": "Key name for press action"},
                "max_tabs":    {"type": "INTEGER", "description": "Maximum tabs to list for list_tabs action, including external Windows browser tabs when detectable"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": (
            "Manages files and folders. Use for: listing files, creating/deleting/moving/copying "
            "files, reading file contents, finding files by name or extension, checking disk usage, "
            "organizing the desktop, getting file info."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "cmd_control",
        "description": (
            "Runs CMD/terminal commands by understanding natural language. "
            "Use when user wants to: find large files, check disk space, list processes, "
            "get system info, navigate folders, check network, find files by name, "
            "or do ANYTHING in the command line they don't know how to do themselves."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task":    {"type": "STRING", "description": "Natural language description of what to do. Example: 'find the 10 largest files on C drive'"},
                "visible": {"type": "BOOLEAN", "description": "Open visible CMD window so user can see. Default: true"},
                "command": {"type": "STRING", "description": "Optional: exact command if already known"},
            },
            "required": ["task"]
        }
    },
    {
        "name": "desktop_control",
        "description": (
            "Desktop utility and compatibility tool. Use for wallpaper changes, desktop cleanup, "
            "desktop listing/stats, or legacy compatibility routing into the live desktop operator. "
            "For real screen-aware multi-step computer operation, prefer desktop_operator."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Legacy natural language desktop goal; routes into desktop_operator"},
                "autonomy_mode": {"type": "STRING", "description": "suggest_only | ask_before_action | ask_before_risky_action | full_autonomous"},
                "max_steps": {"type": "INTEGER", "description": "Maximum operator steps when action=task"},
                "retries": {"type": "INTEGER", "description": "Recovery budget when action=task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_operator",
        "description": (
            "Primary live desktop operator. Use for cross-app computer control that must observe the live screen, "
            "understand the current UI, choose the next best action, execute with mouse/keyboard/UI automation, "
            "verify the result, recover from changes, and repeat until the goal is completed or safely blocked."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal": {"type": "STRING", "description": "Natural language desktop goal to complete end-to-end"},
                "autonomy_mode": {"type": "STRING", "description": "suggest_only | ask_before_action | ask_before_risky_action | full_autonomous"},
                "max_steps": {"type": "INTEGER", "description": "Maximum number of operator steps before stopping"},
                "retries": {"type": "INTEGER", "description": "Recovery budget for blocked or failed steps"},
                "settle_delay": {"type": "NUMBER", "description": "Delay after each action before verification"},
                "stop_token": {"type": "STRING", "description": "Optional stop token for interruptible operator runs"},
                "interrupt": {"type": "BOOLEAN", "description": "If true, requests a stop for the provided stop_token instead of starting a task"},
            },
            "required": []
        }
    },
    {
    "name": "code_helper",
    "description": (
        "Writes, edits, explains, runs, self-builds code files, uses an Open Interpreter-style code-first loop for novel scripts, inspects repo context, validates repo commands, or prepares repo-safe review bundles. "
        "Use for ANY coding request: writing a script, fixing a file, "
        "editing existing code, running a file, building and testing automatically, or reviewing upgrades for the current project."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action":      {"type": "STRING", "description": "write | edit | explain | run | build | interpret | screen_debug | optimize | repo_plan | repo_context | repo_validate | auto (default: auto)"},
            "description": {"type": "STRING", "description": "What the code should do, or what change to make"},
            "language":    {"type": "STRING", "description": "Programming language (default: python)"},
            "output_path": {"type": "STRING", "description": "Where to save the file (full path or filename)"},
            "file_path":   {"type": "STRING", "description": "Path to existing file for edit / explain / run / build"},
            "code":        {"type": "STRING", "description": "Raw code string for explain"},
            "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
            "commands":    {"type": "STRING", "description": "One or more repo validation commands joined by && for repo_validate"},
            "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
        },
        "required": ["action"]
    }
    },
    {
    "name": "dev_agent",
    "description": (
        "Builds complete multi-file projects from scratch. "
        "Plans structure, writes all files, installs dependencies, "
        "opens VSCode, runs the project, fixes errors automatically, or prepares a review bundle for an existing repo when mode=review. "
        "Use for any project larger than a single script."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "description":  {"type": "STRING", "description": "What the project should do"},
            "language":     {"type": "STRING", "description": "Programming language (default: python)"},
            "project_name": {"type": "STRING", "description": "Optional project folder name"},
            "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            "mode":         {"type": "STRING", "description": "build | review (default: build)"},
        },
        "required": ["description"]
    }
    },
    {
    "name": "agent_task",
    "description": (
        "Executes complex multi-step tasks that require MULTIPLE DIFFERENT tools. "
        "Always respond to the user in the language they spoke. "
        "Examples: 'research X and save to file', 'find files and organize them', "
        "'fill a form on a website', 'write and test code'. "
        "DO NOT use for simple computer commands like volume, refresh, close, scroll, "
        "minimize, screenshot, restart, shutdown — use computer_settings for those. "
        "For technical/system problems, prefer local diagnosis and safe fixes before reporting failure. "
        "DO NOT use if the task can be done with a single tool call."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "goal": {
                "type": "STRING",
                "description": "Complete description of what needs to be accomplished"
            },
            "priority": {
                "type": "STRING",
                "description": "low | normal | high (default: normal)"
            }
        },
        "required": ["goal"]
    }
},
    {
    "name": "computer_control",
    "description": (
        "Direct computer control: type text, click buttons, use keyboard shortcuts, "
        "scroll, move mouse, take screenshots, fill forms, find elements on screen, and use native Windows UIA grounding when available. "
        "Use when the user wants to interact with any app on the computer directly. "
        "Can generate random data for forms or use user's real info from memory."
    ),
        "parameters": {
        "type": "OBJECT",
        "properties": {
            "action":      {"type": "STRING", "description": "type | smart_type | screen_type | smart_click | click | double_click | right_click | drag | screen_drag | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | uia_find | uia_list | uia_click | uia_type | uia_get_value | uia_focus | uia_invoke | uia_select | uia_check | uia_wait | uia_window_list | uia_window_focus | random_data | user_data"},
            "text":        {"type": "STRING", "description": "Text to type or paste"},
            "x":           {"type": "INTEGER", "description": "X coordinate for click/move"},
            "y":           {"type": "INTEGER", "description": "Y coordinate for click/move"},
            "dx":          {"type": "INTEGER", "description": "Relative X offset for screen_drag"},
            "dy":          {"type": "INTEGER", "description": "Relative Y offset for screen_drag"},
            "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
            "key":         {"type": "STRING", "description": "Single key to press e.g. 'enter'"},
            "direction":   {"type": "STRING", "description": "Scroll direction: up | down | left | right"},
            "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
            "seconds":     {"type": "NUMBER", "description": "Seconds to wait"},
            "title":       {"type": "STRING", "description": "Window title for focus_window"},
            "description": {"type": "STRING", "description": "Element description for smart_click/screen_find/screen_click/screen_drag"},
            "force_refresh": {"type": "BOOLEAN", "description": "Force the live observer to refresh before resolving an on-screen target"},
            "window":      {"type": "STRING", "description": "Window title for UIA actions"},
            "value":       {"type": "STRING", "description": "Value for uia_select"},
            "checked":     {"type": "BOOLEAN", "description": "Checkbox state for uia_check"},
            "max_results": {"type": "INTEGER", "description": "Max controls/windows to list for uia_list"},
            "timeout":     {"type": "NUMBER", "description": "Timeout for uia_wait"},
            "type":        {"type": "STRING", "description": "Data type for random_data: name|email|username|password|phone|birthday|address"},
            "field":       {"type": "STRING", "description": "Field for user_data: name|email|city"},
            "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            "path":        {"type": "STRING", "description": "Save path for screenshot"},
            "duration":    {"type": "NUMBER", "description": "Drag duration for drag/screen_drag"},
        },
        "required": ["action"]
    }
},

{
    "name": "flight_finder",
    "description": (
        "Searches for flights on Google Flights and speaks the best options. "
        "Use when user asks about flights, plane tickets, uçuş, bilet, etc."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "origin":       {"type": "STRING",  "description": "Departure city or airport code"},
            "destination":  {"type": "STRING",  "description": "Arrival city or airport code"},
            "date":         {"type": "STRING",  "description": "Departure date (any format)"},
            "return_date":  {"type": "STRING",  "description": "Return date for round trips"},
            "passengers":   {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
            "cabin":        {"type": "STRING",  "description": "economy | premium | business | first"},
            "save":         {"type": "BOOLEAN", "description": "Save results to Notepad"},
        },
        "required": ["origin", "destination", "date"]
    }
},
{
    "name": "self_evolve",
    "description": (
        "Self-evolution: Researches unknown topics on the web and automatically upgrades JARVIS capabilities. "
        "Use when user asks JARVIS to learn something new, upgrade itself, research a topic deeply, "
        "or when the task is beyond current capabilities. "
        "This is how JARVIS becomes self-evolving — by researching and implementing new skills."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "topic": {
                "type": "STRING",
                "description": "What to research and implement (required)"
            },
            "depth": {
                "type": "INTEGER",
                "description": "Research depth 1-5 (default: 3)"
            },
            "auto_apply": {
                "type": "BOOLEAN",
                "description": "Legacy hint. Safe evolved skills now auto-register silently only after tests plus guardrails pass."
            }
        },
        "required": ["topic"]
    }
},
{
    "name": "evolve_status",
    "description": "Get current self-evolution status, statistics, and list of evolved skills.",
    "parameters": {
        "type": "OBJECT",
        "properties": {},
        "required": []
    }
},
{
    "name": "configure_evolve",
    "description": "Configure self-evolution settings (auto-research, auto-upgrade, depth, confidence threshold).",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "auto_research": {"type": "BOOLEAN", "description": "Enable/disable auto research"},
            "auto_upgrade": {"type": "BOOLEAN", "description": "Enable/disable auto upgrade"},
            "auto_apply": {"type": "STRING", "description": "never | on_test_pass | always"},
            "max_depth": {"type": "INTEGER", "description": "Maximum research depth (1-5)"},
            "confidence_threshold": {"type": "NUMBER", "description": "Minimum confidence threshold (0.0-1.0)"},
            "open_editor_on_upgrade": {"type": "BOOLEAN", "description": "Open the generated candidate in an editor"}
        },
        "required": []
    }
}
]

class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._pre_roll      = deque(maxlen=VAD_PRE_ROLL_CHUNKS)
        self._speech_active = False
        self._speech_hits   = 0
        self._silence_hits  = 0
        self._noise_floor   = 0.0
        self._capabilities  = detect_system_capabilities(is_admin=_is_running_as_admin())
        self._current_user_text = ""
        self._forced_live_context_done = False
        self._reload_in_progress = False
        self._last_reload_reason = ""
        self._expected_disconnect = False
        self._live_provider = None
        self._provider_user_text = ""
        self._provider_reply_buffer = ""
        self._observer = get_live_screen_observer()
        self._observer.configure_runtime(
            log_callback=self._observer_log,
            narrate_callback=self._observer_narrate,
            status_callback=self._observer_status,
            is_user_speaking=lambda: self._speech_active,
            is_tts_active=lambda: self.ui.speaking,
            is_trusted_mode=lambda: self._get_permission_state().get("trusted_mode", False),
        )
        observer_settings = load_live_observer_state()
        self._observer.apply_settings(**observer_settings)
        if observer_settings.get("enabled", True):
            self._observer.start()

    def speak(self, text: str):
        """Thread-safe speak — any thread can call this."""
        if self._live_provider is not None:
            try:
                self._live_provider.speak(text)
            except Exception:
                pass
            return
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
         )

    def _observer_log(self, text: str):
        line = str(text or "").strip()
        if not line:
            return
        self.ui.write_log(line)

    def _observer_narrate(self, text: str):
        message = str(text or "").strip()
        if not message:
            return
        self.speak(message)

    def _observer_status(self, payload: dict):
        if not isinstance(payload, dict):
            return
        self.ui.set_observer_state(payload)

    async def _provider_tool_call(self, name: str, args: dict) -> str:
        fc = type(
            "ProviderToolCall",
            (),
            {"name": str(name or "").strip(), "args": dict(args or {}), "id": f"provider-{int(time.time() * 1000)}"},
        )()
        response = await self._execute_tool(fc)
        try:
            return str(response.response.get("result", "") or "")
        except Exception:
            return ""

    def _provider_on_user_text(self, text: str):
        cleaned = str(text or "").strip()
        if not cleaned or cleaned == self._provider_user_text:
            return
        self._provider_user_text = cleaned
        self._current_user_text = cleaned
        set_current_user_text(cleaned)
        set_current_task(cleaned)
        self.ui.write_log(f"You: {cleaned}")
        self._record_multimodal_anchor("user_speech", user_text=cleaned)

    def _provider_on_assistant_text(self, text: str):
        cleaned = str(text or "")
        if not cleaned:
            return
        self._provider_reply_buffer += cleaned
        reply_preview = self._provider_reply_buffer.strip()
        if reply_preview:
            self.ui.reply_text = reply_preview[-240:]

    def _provider_speaking_start(self):
        self.ui.start_speaking()

    def _provider_speaking_stop(self):
        self.ui.stop_speaking()
        final_text = self._provider_reply_buffer.strip()
        if final_text:
            self.ui.write_log(f"Jarvis: {final_text}")
            self._record_multimodal_anchor("assistant_reply", assistant_text=final_text)
            if self._provider_user_text and len(self._provider_user_text) > 5:
                threading.Thread(
                    target=_update_memory_async,
                    args=(self._provider_user_text, final_text),
                    daemon=True,
                ).start()
        self._provider_reply_buffer = ""

    def _get_permission_state(self) -> dict:
        try:
            return self.ui.get_permission_state()
        except Exception:
            return {
                "mode": "normal",
                "trusted_mode": False,
                "request_admin_on_start": False,
                "consent_recorded": False,
            }

    def _blocked_tool_message(self, name: str, args: dict) -> str | None:
        state = self._get_permission_state()
        if state.get("trusted_mode"):
            return None

        if _normal_mode_allows(name, args):
            return None

        return (
            f"Trusted Mode is required for '{name}'. "
            "Normal Mode only allows everyday safe actions. Restart JARVIS and approve Trusted Mode from the startup permission prompt for full desktop control."
        )

    def _task_status_callback(self, task_id: str, status: str, payload: str):
        message = str(payload or "").strip()[:160] or "No details."
        self.ui.note_task_event(task_id, status, message)

    def _current_live_multimodal_context(self) -> str:
        try:
            anchor = self._observer.current_anchor()
        except Exception:
            anchor = {}
        lines = []
        frame_seq = int(anchor.get("frame_seq", 0) or 0)
        if frame_seq:
            lines.append(
                f"Observer frame #{frame_seq} | age={float(anchor.get('age_seconds', 0.0) or 0.0):.2f}s"
            )
        active_window = str(anchor.get("active_window") or "").strip()
        if active_window:
            lines.append(f"Active window: {active_window}")
        scene_summary = str(anchor.get("scene_summary") or "").strip()
        if scene_summary:
            lines.append(f"Scene: {scene_summary[:220]}")
        interaction_mode = str(anchor.get("interaction_mode") or "").strip()
        if interaction_mode:
            lines.append(f"Interaction mode: {interaction_mode}")
        user_activity = str(anchor.get("user_activity") or "").strip()
        if user_activity:
            lines.append(f"User activity: {user_activity[:220]}")
        assist = str(anchor.get("assist_opportunity") or "").strip()
        if assist:
            lines.append(f"Assist opportunity: {assist[:220]}")
        live_summary = get_last_live_context_summary(max_chars=260)
        if live_summary:
            lines.append(f"Latest live context: {live_summary[:260]}")
        return "\n".join(lines[:6]).strip()

    def _record_multimodal_anchor(
        self,
        source: str,
        *,
        user_text: str = "",
        assistant_text: str = "",
        tool_name: str = "",
    ) -> None:
        try:
            anchor = dict(self._observer.current_anchor() or {})
        except Exception:
            anchor = {}
        anchor.update(
            {
                "source": str(source or "").strip()[:60],
                "user_text": str(user_text or "").strip()[:240],
                "assistant_text": str(assistant_text or "").strip()[:240],
                "tool_name": str(tool_name or "").strip()[:120],
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        remember_multimodal_anchor(anchor)

    async def _watch_reload_requests(self):
        while True:
            await asyncio.sleep(0.4)
            reason = consume_live_reload_request()
            if not reason:
                continue
            self._reload_in_progress = True
            self._last_reload_reason = reason
            print(f"[JARVIS] 🔄 Hot reload requested: {reason}")
            self.ui.set_reload_state(True, reason)
            self.ui.write_log(f"SYS: Hot reload requested: {reason}")
            if self.session is not None:
                self._expected_disconnect = True
                await self.session.close()
            return
    
    def _build_system_prompt_text(self) -> str:
        from datetime import datetime 

        mem_str = build_runtime_memory_context("", include_knowledge=True, max_chars=3200)
        permission_state = self._get_permission_state()

        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        access_mode = "TRUSTED" if permission_state.get("trusted_mode") else "NORMAL"
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders. "
            f"If user says 'in 2 minutes', add 2 minutes to this time.\n\n"
        )
        live_ctx = (
            "[LIVE LAPTOP STATE]\n"
            "If a task depends on what is currently open on the laptop, which windows/processes/tabs are active, "
            "or what is visible on screen, call live_context before acting. "
            "The always-on live screen observer is already monitoring the current screen in memory. "
            "Use screen_process when you need an observer-backed visual analysis answer or a webcam analysis when angle='camera'. "
            "Most browser or direct computer interaction actions now trigger a forced live_context preflight automatically. "
            "Use observer_control only when you need to inspect or change observer state itself. "
            "If a tool response says a forced live context snapshot was captured, treat that snapshot as mandatory pre-action context and then choose the next tool.\n\n"
        )
        permission_ctx = (
            "[DEVICE ACCESS MODE]\n"
            f"Current mode: {access_mode}\n"
            f"Trusted mode: {'ENABLED' if permission_state.get('trusted_mode') else 'DISABLED'}\n"
            f"Windows administrator session: {'YES' if _is_running_as_admin() else 'NO'}\n"
        )

        if permission_state.get("trusted_mode"):
            permission_ctx += (
                "Local desktop control has been approved by the computer owner. "
                "Treat this computer as your operating body: use the available tools to control apps, browser tabs, files, keyboard, mouse, desktop, screen capture, webcam capture, reminders, local commands, coding flows, and local software whenever that helps complete the user's task. "
                "Default to acting through tools instead of describing steps. "
                "If a task depends on administrator rights and the admin session is NO, say Windows may still block that action.\n\n"
            )
        else:
            permission_ctx += (
                "Normal Mode is active. "
                "You may use only everyday safe actions: web search, weather, opening apps, messaging, safe browser navigation, screen analysis, safe command queries, and read-only file or desktop lookups. "
                "Do not attempt full computer control, code/project mutation, reminder scheduling, self-upgrade, destructive file changes, or other high-trust automation. "
                "If the user asks for those, explain that Trusted Mode must be approved from the startup prompt.\n\n"
            )

        parts = [
            time_ctx,
            live_ctx,
            permission_ctx,
            format_capabilities_for_prompt(self._capabilities),
        ]
        if mem_str:
            parts.append(mem_str + "\n")
        parts.append(sys_prompt)
        return "".join(parts)

    def _build_config(self) -> types.LiveConnectConfig:
        sys_prompt = self._build_system_prompt_text()

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction=sys_prompt,
            tools=[{"function_declarations": TOOL_DECLARATIONS + get_evolved_tool_declarations()}],
            session_resumption=types.SessionResumptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=True,
                ),
                turn_coverage=types.TurnCoverage.TURN_INCLUDES_ALL_INPUT,
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon" 
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        from core.task_orchestrator import route_task
        from core.task_trace import TaskTraceRecorder

        tool_goal = self._current_user_text.strip() or f"{name}: {args}"
        routed_tool = route_task(tool_goal, source="voice", preferred_tool=name, plan={"tool": name, "args": args})
        trace = TaskTraceRecorder.start(
            tool_goal,
            routed_tool["lane"],
            source="voice",
            plan=routed_tool["plan"],
            metadata={"tool": name},
        )
        trace_step_id = trace.add_step(
            step=1,
            tool=name,
            description=f"voice tool call: {name}",
            input_summary=str(args)[:700],
        )
        self._record_multimodal_anchor("tool_call", user_text=self._current_user_text, tool_name=name)

        def _finish_tool(status: str, message: str, *, error: str = "") -> types.FunctionResponse:
            trace.update_step(
                trace_step_id,
                status="completed" if status != "failed" else "failed",
                result_summary=str(message)[:1200],
                error=error,
            )
            trace.finalize(status, summary=message, error=error)
            # ── Metrics hook ──────────────────────────────────────────────────
            try:
                _metrics_record_tool_call(name, status)
            except Exception:
                pass
            return types.FunctionResponse(
                id=fc.id,
                name=name,
                response={"result": message},
            )

        blocked_message = self._blocked_tool_message(name, args)
        if blocked_message:
            print(f"[JARVIS] 🚫 {blocked_message}")
            self.ui.write_log(f"SYS: {blocked_message}")
            return _finish_tool("blocked", blocked_message, error=blocked_message)

        capability_message = tool_capability_issue(name, args, self._capabilities)
        if capability_message:
            print(f"[JARVIS] ⚠️ {capability_message}")
            self.ui.write_log(f"SYS: {capability_message}")
            return _finish_tool("blocked", capability_message, error=capability_message)

        print(f"[JARVIS] 🔧 TOOL: {name}  ARGS: {args}")
        if self._current_user_text:
            set_current_user_text(self._current_user_text)

        loop   = asyncio.get_event_loop()
        result = "Done."
        self._observer.set_tool_busy(True, reason=name)

        try:
            if (
                name == "screen_process"
                and text_requires_explicit_execution(self._current_user_text)
            ):
                result = (
                    "Analysis-only tool rejected for this request. "
                    "Use an acting tool next: computer_control, computer_settings, or browser_control."
                )
                print("[JARVIS] ⚠️ screen_process rejected for action-heavy request")
                return types.FunctionResponse(
                    id=fc.id,
                    name=name,
                    response={"result": result},
                )

            if (
                not self._forced_live_context_done
                and tool_needs_live_context(name, args, self._current_user_text)
            ):
                context_question = (
                    self._current_user_text.strip()
                    or f"Summarize the current laptop state before running {name}."
                )
                context_result = await loop.run_in_executor(
                    None,
                    lambda: live_context(
                        parameters={
                            "question": context_question,
                            "include_screen": True,
                            "include_windows": True,
                            "include_processes": True,
                            "include_browser": True,
                            "max_windows": 10,
                            "max_processes": 12,
                            "max_tabs": 15,
                        },
                        player=self.ui,
                    ),
                )
                if context_result:
                    self._forced_live_context_done = True
                    record_tool_result("live_context", context_result)
                    trace.add_live_screen(context_result, source="live_context", summary=f"Forced precheck before {name}")
                    result = (
                        "Forced live context snapshot before action:\n\n"
                        f"{context_result}\n\n"
                        "Use this current laptop state and choose the next tool/action. "
                        "Do not execute the previous action blindly."
                    )
                    print(f"[JARVIS] 🛰️ Forced live_context before {name}")
                    self.ui.write_log(f"PRECHECK: live context captured before {name}.")
                    return _finish_tool("partial", result)

            if name == "open_app":
                r = await loop.run_in_executor(
                    None, lambda: open_app(parameters=args, response=None, player=self.ui)
                )
                result = r or f"Opened {args.get('app_name')} successfully."

            elif name == "weather_report":
                r = await loop.run_in_executor(
                    None, lambda: weather_action(parameters=args, player=self.ui)
                )
                result = r or f"Weather report for {args.get('city')} delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(
                    None, lambda: browser_control(parameters=args, player=self.ui)
                )
                result = r or "Browser action completed."

            elif name == "memory_control":
                r = await loop.run_in_executor(
                    None, lambda: memory_control(parameters=args, player=self.ui)
                )
                result = r or "Memory updated."
                if str(args.get("action", "")).strip().lower() in {"remember", "forget"}:
                    request_live_reload("Manual memory change applied.")

            elif name == "file_controller":
                r = await loop.run_in_executor(
                    None, lambda: file_controller(parameters=args, player=self.ui)
                )
                result = r or "File operation completed."

            elif name == "send_message":
                r = await loop.run_in_executor(
                    None, lambda: send_message(
                        parameters=args, response=None,
                        player=self.ui, session_memory=None
                    )
                )
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(
                    None, lambda: reminder(parameters=args, response=None, player=self.ui)
                )
                result = r or f"Reminder set for {args.get('date')} at {args.get('time')}."

            elif name == "youtube_video":
                r = await loop.run_in_executor(
                    None, lambda: youtube_video(parameters=args, response=None, player=self.ui)
                )
                result = r or "Done."

            elif name == "screen_process":
                r = await loop.run_in_executor(
                    None,
                    lambda: screen_process(
                        parameters=args,
                        response=None,
                        player=self.ui,
                        session_memory=None,
                    ),
                )
                result = r or "Live screen analysis completed."

            elif name == "live_context":
                r = await loop.run_in_executor(
                    None, lambda: live_context(parameters=args, player=self.ui)
                )
                result = r or "Live laptop context captured."
                if text_requires_explicit_execution(self._current_user_text):
                    result += (
                        "\n\nThis is analysis only. Use an acting tool next: "
                        "computer_control, computer_settings, or browser_control."
                    )

            elif name == "observer_control":
                r = await loop.run_in_executor(
                    None, lambda: observer_control(parameters=args, player=self.ui)
                )
                result = r or "Observer state updated."

            elif name == "computer_settings":
                r = await loop.run_in_executor(
                    None, lambda: computer_settings(
                        parameters=args, response=None, player=self.ui
                    )
                )
                result = r or "Done."

            elif name == "cmd_control":
                r = await loop.run_in_executor(
                    None, lambda: cmd_control(parameters=args, player=self.ui)
                )
                result = r or "Command executed."

            elif name == "desktop_control":
                r = await loop.run_in_executor(
                    None, lambda: desktop_control(parameters=args, player=self.ui)
                )
                result = r or "Desktop action completed."
            elif name == "desktop_operator":
                r = await loop.run_in_executor(
                    None, lambda: desktop_operator(parameters=args, player=self.ui)
                )
                result = r or "Desktop operator finished."
            elif name == "code_helper":
                r = await loop.run_in_executor(
                    None, lambda: code_helper(
                        parameters=args,
                        player=self.ui,
                        speak=self.speak 
                    )
                )
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(
                    None, lambda: dev_agent(
                        parameters=args,
                        player=self.ui,
                        speak=self.speak
                    )
                )
                result = r or "Done."
            elif name == "agent_task":
                goal         = args.get("goal", "")
                priority_str = args.get("priority", "normal").lower()
                set_current_task(goal)

                from agent.task_queue import get_queue, TaskPriority
                priority_map = {
                    "low":    TaskPriority.LOW,
                    "normal": TaskPriority.NORMAL,
                    "high":   TaskPriority.HIGH,
                }
                priority = priority_map.get(priority_str, TaskPriority.NORMAL)

                # Check if task requires self-evolution (beyond current capabilities)
                from agent.self_evolve import SelfEvolvingAgent
                evolve_agent = SelfEvolvingAgent(speak=self.speak)
                gap_result = evolve_agent.check_capability_gap(goal, self._capabilities)
                evolve_threshold = float(evolve_agent.config.get("confidence_threshold", 0.55) or 0.55)

                if gap_result.get("gap_detected") and gap_result.get("confidence", 0) >= evolve_threshold:
                    print(f"[JARVIS] 🧬 Capability gap detected for agent_task")
                    if self.speak:
                        self.speak("This task requires new capabilities. Let me research and upgrade myself.")

                    # Execute self-evolution
                    evolution_result = evolve_agent.execute_self_evolution(goal, self._capabilities)

                    if evolution_result.get("evolved"):
                        output_path = evolution_result.get("output_path") or evolution_result.get("file_path")
                        test_status = evolution_result.get("test_status", "not_run")
                        review_status = evolution_result.get("review_status", "pending_review")
                        auto_applied = bool(evolution_result.get("auto_applied"))
                        if auto_applied:
                            queue = get_queue()
                            task_id = queue.submit(
                                goal=goal,
                                priority=priority,
                                speak=self.speak,
                                on_complete=self._task_status_callback,
                            )
                            self.ui.note_task_event(task_id, "queued", goal[:160])
                            result = (
                                f"Installed a tested capability upgrade silently and resumed the task (ID: {task_id}). "
                                f"Tests: {test_status}. Review status: {review_status}."
                            )
                            if output_path:
                                trace.add_artifact(
                                    kind="self_evolve_candidate",
                                    name=Path(output_path).name,
                                    path=str(output_path),
                                    summary=result,
                                    metadata={
                                        "mode": evolution_result.get("mode", ""),
                                        "trace_id": evolution_result.get("trace_id", ""),
                                        "auto_applied": True,
                                    },
                                )
                            print(f"[JARVIS] ⚡ Self-evolution auto-applied and task resumed: {output_path}")
                            return _finish_tool("success", result)
                        result = (
                            f"Prepared a review-ready upgrade candidate: {Path(output_path).name if output_path else 'candidate'}. "
                            f"Tests: {test_status}. Review status: {review_status}. "
                            "Manual review is required before this task can continue with new capabilities."
                        )
                        if output_path:
                            trace.add_artifact(
                                kind="self_evolve_candidate",
                                name=Path(output_path).name,
                                path=str(output_path),
                                summary=result,
                                metadata={
                                    "mode": evolution_result.get("mode", ""),
                                    "trace_id": evolution_result.get("trace_id", ""),
                                },
                            )
                        print(f"[JARVIS] ✅ Self-evolution prepared review candidate: {output_path}")
                        return _finish_tool("review_required", result)
                    else:
                        reason = evolution_result.get("reason", "Unknown reason")
                        result = (
                            "This task needs capabilities that are not active yet, and self-evolution "
                            f"could not prepare a usable candidate: {reason}"
                        )
                        print(f"[JARVIS] ⚠️ Self-evolution skipped: {reason}")
                        return _finish_tool("failed", result, error=reason)

                queue   = get_queue()
                task_id = queue.submit(
                    goal=goal,
                    priority=priority,
                    speak=self.speak,
                    on_complete=self._task_status_callback,
                )
                self.ui.note_task_event(task_id, "queued", goal[:160])
                result = f"Task started (ID: {task_id}). I'll update you as I make progress, sir."

            elif name == "web_search":
                r = await loop.run_in_executor(
                    None, lambda: web_search_action(parameters=args, player=self.ui)
                    )
                result = r or "Search completed."
            elif name == "computer_control":
                r = await loop.run_in_executor(
                    None, lambda: computer_control(parameters=args, player=self.ui)
                )
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(
                    None, lambda: flight_finder(parameters=args, player=self.ui)
                )
                result = r or "Done."

            elif name == "self_evolve":
                r = await loop.run_in_executor(
                    None, lambda: self_evolve_action(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Evolution complete."

            elif name == "evolve_status":
                r = await loop.run_in_executor(
                    None, lambda: get_evolve_status_action(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Status retrieved."

            elif name == "configure_evolve":
                r = await loop.run_in_executor(
                    None, lambda: configure_evolve_action(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Settings updated."

            elif name == "uia_executor":
                r = await loop.run_in_executor(
                    None, lambda: uia_executor(parameters=args, player=self.ui)
                )
                result = r or "UI action completed."

            elif name == "apply_skill":
                r = await loop.run_in_executor(
                    None, lambda: apply_skill_action(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Skill apply attempted."

            elif name == "rollback_skill":
                r = await loop.run_in_executor(
                    None, lambda: rollback_skill_action(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Rollback attempted."

            else:
                r = await loop.run_in_executor(
                    None,
                    lambda: run_evolved_skill(
                        name,
                        parameters=args,
                        player=self.ui,
                        speak=self.speak,
                    ),
                )
                result = r if r is not None else f"Unknown tool: {name}"
            
        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
        finally:
            self._observer.set_tool_busy(False, reason=name)

        print(f"[JARVIS] 📤 {name} → {result[:80]}")
        record_tool_result(name, result)
        return _finish_tool("failed" if str(result).startswith("Tool '") and "failed:" in str(result) else "success", result, error=result if str(result).startswith("Tool '") and "failed:" in str(result) else "")

    @staticmethod
    def _chunk_rms(data: bytes) -> float:
        if not data:
            return 0.0
        samples = np.frombuffer(data, dtype=np.int16)
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(samples.astype(np.float32)))))

    def _detect_voice(self, data: bytes) -> bool:
        rms = self._chunk_rms(data)
        if not self._speech_active:
            if self._noise_floor <= 0.0:
                self._noise_floor = rms
            else:
                self._noise_floor = (self._noise_floor * 0.92) + (rms * 0.08)

        threshold = max(VAD_MIN_RMS, self._noise_floor * VAD_NOISE_MULTIPLIER)
        return rms >= threshold

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            kind = msg.get("kind", "audio")
            if kind == "activity_start":
                await self.session.send_realtime_input(
                    activity_start=types.ActivityStart()
                )
            elif kind == "activity_end":
                await self.session.send_realtime_input(
                    activity_end=types.ActivityEnd()
                )
            else:
                await self.session.send_realtime_input(
                    media={"data": msg["data"], "mime_type": INPUT_AUDIO_MIME}
                )

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
        )
        try:
            while True:
                data = await asyncio.to_thread(
                    stream.read, CHUNK_SIZE, exception_on_overflow=False
                )
                heard_voice = self._detect_voice(data)

                if not self._speech_active:
                    self._pre_roll.append(data)
                    self._speech_hits = self._speech_hits + 1 if heard_voice else 0

                    if self._speech_hits >= VAD_START_CHUNKS:
                        self._speech_active = True
                        self._silence_hits = 0
                        self._current_user_text = ""
                        set_current_user_text("")
                        self._forced_live_context_done = False
                        await self.out_queue.put({"kind": "activity_start"})
                        while self._pre_roll:
                            await self.out_queue.put(
                                {"kind": "audio", "data": self._pre_roll.popleft()}
                            )
                    continue

                await self.out_queue.put({"kind": "audio", "data": data})

                if heard_voice:
                    self._silence_hits = 0
                else:
                    self._silence_hits += 1
                    if self._silence_hits >= VAD_END_CHUNKS:
                        self._speech_active = False
                        self._speech_hits = 0
                        self._silence_hits = 0
                        self._pre_roll.clear()
                        await self.out_queue.put({"kind": "activity_end"})
        except Exception as e:
            print(f"[JARVIS] ❌ Mic error: {e}")
            self.ui.write_log(f"SYS: Mic error: {e}")
            raise
        finally:
            stream.close()

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf = []
        in_buf  = []

        try:
            while True:
                turn = self.session.receive()
                async for response in turn:

                    if response.data:
                        if not self.ui.speaking:
                            self.ui.start_speaking()
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)
                                self._current_user_text = " ".join(in_buf).strip()
                                set_current_user_text(self._current_user_text)

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                out_buf.append(txt)

                        if sc.turn_complete:
                            full_in  = ""
                            full_out = ""
                            self.ui.stop_speaking()

                            if in_buf:
                                full_in = " ".join(in_buf).strip()
                                if full_in:
                                    self._current_user_text = full_in
                                    set_current_user_text(full_in)
                                    set_current_task(full_in)
                                    self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            if out_buf:
                                full_out = " ".join(out_buf).strip()
                                if full_out:
                                    self.ui.write_log(f"Jarvis: {full_out}")
                            out_buf = []

                            if full_in and len(full_in) > 5:
                                threading.Thread(
                                    target=_update_memory_async,
                                    args=(full_in, full_out),
                                    daemon=True
                                ).start()

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 Tool call: {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )

        except Exception as e:
            if self._expected_disconnect:
                print("[JARVIS] ℹ️ Receive loop closed for expected reconnect")
                return
            print(f"[JARVIS] ❌ Recv error: {e}")
            self.ui.stop_speaking()
            self.ui.write_log(f"SYS: Receive error: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
        )
        try:
            while True:
                chunk = await self.audio_in_queue.get()
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            if self._expected_disconnect:
                print("[JARVIS] ℹ️ Audio playback loop closed for expected reconnect")
                return
            print(f"[JARVIS] ❌ Play error: {e}")
            self.ui.stop_speaking()
            self.ui.write_log(f"SYS: Speaker error: {e}")
            raise
        finally:
            stream.close()

    async def run(self):
        provider_name = _resolve_live_provider()
        if provider_name != "gemini":
            self._loop = asyncio.get_event_loop()
            self._live_provider = _create_live_provider(
                on_tool_call=self._provider_tool_call,
                on_user_text=self._provider_on_user_text,
                on_assistant_text=self._provider_on_assistant_text,
                on_log=self.ui.write_log,
                on_speaking_start=self._provider_speaking_start,
                on_speaking_stop=self._provider_speaking_stop,
                context_provider=self._current_live_multimodal_context,
                system_prompt=self._build_system_prompt_text(),
                tool_declarations=TOOL_DECLARATIONS + get_evolved_tool_declarations(),
                jarvis_live_instance=self,
            )
            await self._live_provider.run()
            return

        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        reconnect_delay = 3.0
        max_reconnect_delay = 30.0

        while True:
            try:
                print("[JARVIS] 🔌 Connecting...")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=10)
                    self._pre_roll.clear()
                    self._speech_active = False
                    self._speech_hits   = 0
                    self._silence_hits  = 0
                    self._noise_floor   = 0.0
                    self._reload_in_progress = False
                    self._expected_disconnect = False
                    reconnect_delay = 3.0  # Reset delay on successful connection

                    print("[JARVIS] ✅ Connected.")
                    self.ui.set_reload_state(None, "Live session synced.")
                    self.ui.write_log("JARVIS online.")
                    self.ui.write_log("SYS: Voice pipeline ready.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._watch_reload_requests())

                if self._reload_in_progress:
                    print("[JARVIS] 🔄 Session reload completed, reconnecting with fresh config")
                    time.sleep(0.4)
                    continue

            except SessionReloadRequested as e:
                reason = str(e) or self._last_reload_reason or "Runtime context updated."
                self.ui.stop_speaking()
                self.ui.set_reload_state(True, reason)
                self.ui.write_log("SYS: Reconnecting live session with refreshed memory and behavior context.")
                reconnect_delay = 0.6
                time.sleep(reconnect_delay)

            except Exception as e:
                if self._expected_disconnect or self._reload_in_progress:
                    print("[JARVIS] ℹ️ Suppressed expected reconnect exception")
                    self._expected_disconnect = False
                    time.sleep(0.4)
                    continue
                error_str = str(e)
                print(f"[JARVIS] ⚠️  Error: {e}")
                self.ui.stop_speaking()
                self.ui.set_reload_state(False, error_str or "Connection degraded.")
                self.ui.write_log(f"SYS: Connection error: {e}")
                traceback.print_exc()

                # Exponential backoff for reconnection
                if "429" in error_str or "quota" in error_str.lower():
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                    print(f"[JARVIS] ⏳ Rate limited. Waiting {reconnect_delay}s")
                elif "connection" in error_str.lower() or "timeout" in error_str.lower():
                    reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
                    print(f"[JARVIS] ⏳ Connection issue. Waiting {reconnect_delay}s")

                time.sleep(reconnect_delay)

def main():
    ui = JarvisUI(str(BASE_DIR / "face.png"))

    def runner():
        ui.wait_for_startup()

        permission_state = ui.get_permission_state()
        trusted_mode = permission_state.get("trusted_mode", False)
        wants_admin = trusted_mode and permission_state.get("request_admin_on_start", False)

        if wants_admin and not _is_running_as_admin():
            ui.write_log("SYS: Trusted mode approved. Requesting Windows administrator access.")
            if _request_admin_relaunch():
                ui.write_log("SYS: Relaunching with administrator access.")
                time.sleep(0.8)
                os._exit(0)
            ui.write_log("SYS: Administrator request was denied. Continuing without admin privileges.")
        elif trusted_mode:
            if _is_running_as_admin():
                ui.write_log("SYS: Trusted mode active with administrator privileges.")
            else:
                ui.write_log("SYS: Trusted mode active.")
        else:
            ui.write_log("SYS: Normal mode active. Full desktop control is locked.")

        ui.set_access_state(trusted_mode, _is_running_as_admin())

        try:
            p_info = _get_live_provider_info()
            ui.write_log(f"SYS: Live Provider initialized: {p_info['name']}")
            print(f"[JARVIS] 🎙️  Provider: {p_info['name']} (Capabilities: " +
                  ", ".join(k.replace("can_", "") for k, v in p_info["caps"].items() if v) + ")")
        except Exception:
            pass

        capabilities = detect_system_capabilities(is_admin=_is_running_as_admin())
        ui.set_capabilities(capabilities)
        ui.write_log(f"SYS: Capabilities: {summarize_capabilities(capabilities)}")
        for note in capabilities.get("notes", [])[:3]:
            ui.write_log(f"SYS: {note}")
        threading.Thread(target=refresh_system_inventory, kwargs={"force": False}, daemon=True).start()

        # Start resource monitoring
        start_monitoring()

        # Log system status
        status = get_resource_status()
        print(f"[System] CPU: {status['cpu_status']} | RAM: {status['ram_status']} | Threads: {status['thread_count']}")

        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()
