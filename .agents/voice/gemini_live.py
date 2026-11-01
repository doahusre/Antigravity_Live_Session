#!/usr/bin/env python3
"""Gemini Live Terminal Assistant (Vertex AI Multimodal Live API).

Features:
- Fixed WebSocket keepalive: zero ping timeouts.
- Direct continuous streaming to Gemini's native neural VAD (sub-second latency).
- Real-time dynamic terminal VU meter: see your voice levels live.
- Software digital gain boost + soft limiter for effortless mic pickup.
- Acoustic speaker shielding: prevents speaker audio from looping back to Gemini.
- Barge-in: interrupt Gemini instantly by speaking.
"""

import argparse
import asyncio
import collections
import functools
import math
import os
import signal
import struct
import subprocess
import sys
import threading
import time
import numpy as np

# Patch websockets in google-genai before import to disable client ping timeouts
from google.genai import live
if hasattr(live, "ws_connect"):
    live.ws_connect = functools.partial(live.ws_connect, ping_interval=None, ping_timeout=None)

from google import genai
from google.genai import types

INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
CHUNK_BYTES = 2048  # 1024 samples @ 16-bit mono = 64ms

DEFAULT_PROJECT = "iggnita-ai"
DEFAULT_LOCATION = "us-central1"
DEFAULT_MODEL = "gemini-live-2.5-flash-native-audio"
DEFAULT_VOICE = "Aoede"
# Import browser controller for live interactive web automation
sys.path.append("/home/doa/Projects/.agents/voice")
from browser_controller import VoiceBrowserController

LOG_FILE = "/home/doa/Projects/.agents/voice/session.log"
TRANSCRIPT_FILE = "/home/doa/Projects/.agents/voice/live_transcript.md"
PAINPOINTS_LOG = "/home/doa/Projects/.agents/voice/painpoints.log"

def log_event(category: str, msg: str):
    try:
        t = time.strftime("%H:%M:%S")
        with open(LOG_FILE, "a") as f:
            f.write(f"[{t}] [{category}] {msg}\n")
    except Exception:
        pass

def append_transcript(speaker: str, text: str):
    try:
        t = time.strftime("%H:%M:%S")
        with open(TRANSCRIPT_FILE, "a") as f:
            f.write(f"**[{t}] {speaker}:** {text.strip()}\n\n")
    except Exception:
        pass

SYSTEM_INSTRUCTION = (
    "You are Antigravity Live, an elite, senior-level autonomous AI developer and system operator "
    "partnering directly with the user on Arch Linux. You possess deep system intelligence, extreme competence, "
    "and high agency. You never hesitate, apologize unnecessarily, or give excuses.\n\n"
    "ENVIRONMENT & ARCHITECTURE AWARENESS:\n"
    "- Wayland Compositor: Niri (scrollable-tiling). Window operations MUST use your dedicated tools "
    "('window_switch_focus', 'window_focus_app', 'window_list', 'maximize_column', 'fullscreen_window') or native 'niri msg action' commands.\n"
    "- Vision / Screen Eyes: 'read_screen_eyes' gives you OCR vision of the screen AND captures your own "
    "Antigravity terminal interface (tmux pane %6, including Model Quotas, Weekly/5-Hour Limits, Pricing, and Tasks). "
    "Call it whenever the user asks about visible windows, tabs, your own interface, quotas, or limits.\n"
    "- Browser Control: Use 'open_browser_tab' to open tabs (attaches to existing Chromium via Ctrl+T), 'close_browser_tab', "
    "'browser_navigate', 'browser_click', 'browser_type', and 'browser_scroll'. NEVER spawn duplicate windows when asked to open a tab.\n"
    "- Games & Launchers: The user's games reside in '/home/doa/Games' (capitalized). "
    "Use 'launch_game' for Legend of Zelda (Eden switch emulator) or OpenLauncher.\n"
    "- Local Projects: Located in '/home/doa/Projects/': 'aepm-os' (Energy AEPM OS / Agentic Energy Project Management OS), "
    "'agent-desktop', 'iggnita_portal', 'zafin'. When asked about 'Energy AEPM OS', inspect '/home/doa/Projects/aepm-os'.\n"
    "- File Discovery: Use 'find_files' to locate files/directories without guessing case-sensitivity.\n"
    "- Task & Interface Status: Call 'check_task_status' whenever the user asks about active tasks, session logs, quotas/limits, or Antigravity's status.\n"
    "- Tmux: Use 'list_tmux_panes' to inspect sessions and panes.\n"
    "- Shell: Use 'run_command' for system inspection (inspecting logs, git, system info).\n"
    "- Self-Development & Escalation: If a capability is requested that you lack, call 'dispatch_task' "
    "to prompt Antigravity to build it immediately.\n\n"
    "COMMUNICATION STYLE:\n"
    "- Crisp, razor-sharp, confident, and direct (1 to 2 spoken sentences maximum).\n"
    "- Decisive: execute tools first, state the outcome clearly. No fluff, no robotic filler."
)

LIVE_TOOLS = [
    {
        "function_declarations": [
            {
                "name": "read_screen_eyes",
                "description": "Takes a real-time screenshot of the developer's visible screen and extracts all visible text via OCR (gives you 'eyes' on their screen).",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "close_browser_tab",
                "description": "Closes the currently active browser tab (sends Ctrl+W).",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "open_browser_tab",
                "description": "Opens a new tab in the active Chromium browser (via Ctrl+T), and optionally navigates to a URL. Does not create a redundant new window.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Optional URL or search query to open in the new tab.",
                        }
                    },
                },
            },
            {
                "name": "maximize_column",
                "description": "Toggles column width maximization (fills screen width without hiding system bars; matches Mod+M in Niri).",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "fullscreen_window",
                "description": "Toggles true fullscreen for the focused window.",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "close_window",
                "description": "Closes the currently focused desktop window.",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "browser_navigate",
                "description": "Navigate to a URL or search Google in the user's visible desktop browser (e.g. 'google.com', 'github.com', 'weather in San Francisco').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL or search query to navigate to.",
                        }
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "browser_click",
                "description": "Click any button, link, or text element on the active web page (e.g. 'Gmail', 'Images', 'Sign in', 'Search').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "The exact visible text or name of the button, link, or element to click.",
                        }
                    },
                    "required": ["target"],
                },
            },
            {
                "name": "browser_type",
                "description": "Type text into the focused input or search bar on the active web page, and optionally submit.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "The text to type.",
                        },
                        "press_enter": {
                            "type": "boolean",
                            "description": "Whether to press Enter after typing (default: true).",
                        },
                    },
                    "required": ["text"],
                },
            },
            {
                "name": "browser_scroll",
                "description": "Scroll the active web page 'down' or 'up'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": ["down", "up"],
                            "description": "Direction to scroll.",
                        }
                    },
                    "required": ["direction"],
                },
            },
            {
                "name": "list_tmux_panes",
                "description": "Lists all active tmux sessions, windows, and panes on this system with their active commands and layout.",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "run_command",
                "description": "Execute a safe shell command on the user's Linux system (e.g. tmux, git status, ls, ps, cat, inspecting session.log).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The exact shell command to run.",
                        }
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "window_switch_focus",
                "description": "Cycle focus between open desktop windows in the Niri Wayland window manager.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": ["next", "prev"],
                            "description": "Direction to cycle focus ('next' or 'prev').",
                        }
                    },
                },
            },
            {
                "name": "window_focus_app",
                "description": "Focus a specific desktop window by its app name ('chromium', 'kitty', 'openlauncher', 'eden'), title substring, or ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "App ID, window title, or window ID to focus (e.g. 'chromium', 'OpenLauncher', 'kitty').",
                        }
                    },
                    "required": ["target"],
                },
            },
            {
                "name": "window_list",
                "description": "Lists all open desktop windows with their ID, title, app ID, and focus state.",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "launch_game",
                "description": "Launches a game installed on the system (e.g. 'zelda' / 'The Legend of Zelda: Breath of the Wild', 'openlauncher', 'RCT2').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "game_name": {
                            "type": "string",
                            "description": "Name of the game to launch (e.g. 'zelda', 'openlauncher').",
                        }
                    },
                    "required": ["game_name"],
                },
            },
            {
                "name": "find_files",
                "description": "Finds files or directories by name (case-insensitive) across the system.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "File or folder name pattern to locate (e.g. 'zelda', 'game', 'openlauncher').",
                        },
                        "start_dir": {
                            "type": "string",
                            "description": "Directory to start search from (default: '/home/doa').",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "check_task_status",
                "description": "Checks the live status, resolution, and history of all background development tasks and painpoints dispatched to Antigravity.",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "dispatch_task",
                "description": "Dispatch a coding task, bug fix, or pain point directly to Antigravity (the lead AI agent) to address.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_title": {"type": "string", "description": "Short title of the task."},
                        "details": {"type": "string", "description": "Detailed explanation of what needs to be fixed or built."},
                    },
                    "required": ["task_title", "details"],
                },
            },
        ]
    }
]


def execute_tool_call(call) -> dict:
    try:
        name = call.name
        args = call.args or {}
        log_event("TOOL", f"Executing tool {name}({args})")
        append_transcript("TOOL_EXECUTION", f"`{name}({args})`")
        
        b_ctl = VoiceBrowserController.get_instance()

        if name == "read_screen_eyes":
            res = b_ctl.read_screen_eyes()
            log_event("TOOL", f"Screen OCR summary: {res.get('visible_text', '')[:120]}")
            return res

        elif name == "close_browser_tab":
            res = b_ctl.close_current_tab()
            log_event("TOOL", f"Result: {res}")
            return res

        elif name == "open_browser_tab":
            url = args.get("url")
            res = b_ctl.open_browser_tab(url)
            log_event("TOOL", f"Result: {res}")
            return res

        elif name == "maximize_column":
            res = b_ctl.maximize_column()
            log_event("TOOL", f"Result: {res}")
            return res

        elif name == "fullscreen_window":
            res = b_ctl.fullscreen_window()
            log_event("TOOL", f"Result: {res}")
            return res

        elif name == "close_window":
            res = b_ctl.close_current_window()
            log_event("TOOL", f"Result: {res}")
            return res

        elif name == "browser_navigate":
            url = args.get("url", "https://google.com")
            res = b_ctl.open_browser_tab(url)
            log_event("TOOL", f"Result: {res}")
            return res

        elif name == "browser_click":
            target = args.get("target", "")
            res = b_ctl.click(target)
            log_event("TOOL", f"Result: {res}")
            return res

        elif name == "browser_type":
            text = args.get("text", "")
            press_enter = args.get("press_enter", True)
            res = b_ctl.type_and_search(text=text, press_enter=press_enter)
            log_event("TOOL", f"Result: {res}")
            return res

        elif name == "browser_scroll":
            direction = args.get("direction", "down")
            res = b_ctl.scroll_desktop(direction)
            log_event("TOOL", f"Result: {res}")
            return res

        elif name == "list_tmux_panes":
            cmd = "tmux list-panes -a -F 'Window #{window_index} (#{window_name}) | Pane #{pane_index}: #{pane_current_command} | Active: #{pane_active}'"
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
            out = (res.stdout + "\n" + res.stderr).strip() or "No open panes found."
            log_event("TOOL", f"Result: {out[:120]}")
            return {"panes": out}

        elif name == "run_command":
            cmd = args.get("command", "ls")
            if "tmux send-keys" in cmd and any(p in cmd for p in ["iggnita:2.1", "%6", "agy-hub:1", "iggnita:2.2", "%19"]):
                log_event("TOOL", f"Blocked unsafe tmux send-keys to Lead Agent / HUD pane: {cmd}")
                return {"error": "Panes iggnita:2.1 (%6, Lead Agent) and iggnita:2.2 (%19, Live HUD) are protected system panes. Do not send keys to them."}
            if "tmux capture-pane" in cmd and " -p" not in cmd:
                cmd = cmd.replace("tmux capture-pane", "tmux capture-pane -p")
            gui_launchers = ["kitty", "alacritty", "foot", "wezterm", "chromium", "google-chrome", "openrct2"]
            cmd_parts = cmd.strip().split()
            if cmd_parts and cmd_parts[0] in gui_launchers and not cmd.strip().endswith("&"):
                try:
                    subprocess.Popen(f"nohup {cmd} >/dev/null 2>&1 &", shell=True)
                    log_event("TOOL", f"Launched GUI app detached: {cmd}")
                    return {"status": f"Launched {cmd} in background"}
                except Exception as e:
                    return {"error": f"Failed to launch {cmd}: {e}"}
            try:
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
                out = (res.stdout + "\n" + res.stderr).strip() or "(no output)"
                log_event("TOOL", f"Result: {out[-120:]}")
                return {"output": out[-2500:]}
            except subprocess.TimeoutExpired:
                log_event("TOOL", f"Command timed out: {cmd}")
                return {"error": f"Command '{cmd}' timed out after 8 seconds"}

        elif name == "window_switch_focus":
            direction = args.get("direction", "next")
            res = b_ctl.switch_focus(direction)
            log_event("TOOL", f"Result: {res}")
            return res

        elif name == "window_focus_app":
            target = args.get("target", "")
            res = b_ctl.focus_window(target)
            log_event("TOOL", f"Result: {res}")
            return res

        elif name == "window_list":
            res = b_ctl.list_windows()
            log_event("TOOL", f"Result: {res}")
            return res

        elif name == "launch_game":
            game_name = args.get("game_name", "")
            res = b_ctl.launch_game(game_name)
            log_event("TOOL", f"Result: {res}")
            return res

        elif name == "find_files":
            query = args.get("query", "")
            start_dir = args.get("start_dir", "/home/doa")
            cmd = f"find '{start_dir}' -maxdepth 3 -iname '*{query}*' 2>/dev/null | head -n 15"
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
            out = (res.stdout + "\n" + res.stderr).strip() or "No matching files or directories found."
            log_event("TOOL", f"Result: {out[:120]}")
            return {"matches": out}

        elif name == "check_task_status":
            try:
                with open(PAINPOINTS_LOG, "r") as f:
                    lines = f.readlines()
                recent = "".join(lines[-25:]).strip()
                with open(LOG_FILE, "r") as sf:
                    slines = sf.readlines()
                recent_session = "".join(slines[-20:]).strip()
                ag_ui = ""
                try:
                    ag_res = subprocess.run(
                        ["tmux", "capture-pane", "-t", "%6", "-p", "-S", "-35"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if ag_res.returncode == 0:
                        ag_ui = "\n".join([l.rstrip() for l in ag_res.stdout.splitlines() if l.strip()][-30:])
                except Exception:
                    pass
                return {
                    "status_report": recent or "All dispatched tasks have been resolved. System is fully operational.",
                    "recent_session_log": recent_session,
                    "antigravity_terminal_interface": ag_ui,
                }
            except Exception as e:
                return {"error": f"Failed to read task log: {e}"}

        elif name == "dispatch_task":
            title = args.get("task_title", "User voice request")
            details = args.get("details", "")
            entry = f"[{time.strftime('%H:%M:%S')}] [TASK_DISPATCHED] {title}: {details}\n"
            with open("/home/doa/Projects/.agents/voice/painpoints.log", "a") as f:
                f.write(entry)
            log_event("DISPATCH", f"Dispatched task: {title}")
            return {"status": "Task dispatched successfully to Antigravity."}

        return {"error": f"Unknown tool: {name}"}
    except Exception as e:
        log_event("TOOL_ERROR", f"Exception in tool {call.name}: {e}")
        return {"error": f"Tool execution failed: {e}"}


def compute_rms(samples: np.ndarray) -> float:
    if len(samples) == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


def make_vu_meter(rms: float, max_rms: float = 6000.0, width: int = 10) -> str:
    """Render a compact visual meter that fits comfortably even in 40-col panes."""
    if rms <= 10.0:
        ratio = 0.0
    else:
        ratio = min(1.0, math.log10(max(1.0, rms)) / math.log10(max_rms))
    filled = int(round(ratio * width))
    bar = "█" * filled + "░" * (width - filled)
    
    if rms > 1.0:
        db = 20 * math.log10(min(1.0, rms / 32767.0))
        db_str = f"{db:4.0f}dB"
    else:
        db_str = " -inf"
        
    return f"[{bar}] {db_str}"


class PacatPlayer:
    """Manages an active pacat playback subprocess with instant kill on barge-in."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self._spawn()

    def _spawn(self):
        try:
            self.proc = subprocess.Popen(
                [
                    "pacat",
                    "--playback",
                    f"--rate={OUTPUT_SAMPLE_RATE}",
                    "--channels=1",
                    "--format=s16le",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            print(f"\n[Player Spawn Error] {e}", file=sys.stderr)
            self.proc = None

    def write(self, data: bytes):
        if not self.proc or self.proc.poll() is not None:
            self._spawn()
        if self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except Exception:
                self._spawn()

    def interrupt(self):
        """Kill current playback immediately to halt audio on barge-in."""
        if self.proc:
            try:
                self.proc.kill()
                self.proc.wait(timeout=0.05)
            except Exception:
                pass
            self.proc = None
        self._spawn()

    def close(self):
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                self.proc.terminate()
                self.proc.wait(timeout=0.1)
            except Exception:
                pass
            self.proc = None


class GeminiLiveBridge:
    def __init__(
        self,
        project: str = DEFAULT_PROJECT,
        location: str = DEFAULT_LOCATION,
        model: str = DEFAULT_MODEL,
        voice_name: str = DEFAULT_VOICE,
        gain: float = 3.0,
    ):
        self.project = project
        self.location = location
        self.model = model
        self.voice_name = voice_name
        self.gain = max(1.0, min(gain, 6.0))

        self.player = PacatPlayer()
        self.stop_event = threading.Event()
        self.ambient_rms = 50.0
        self.barge_in_threshold = 300.0
        self.playback_end_time = 0.0

    def render_meter(self, rms: float, state: str):
        # Keep entire line under 42 characters so 45-col panes never wrap
        vu = make_vu_meter(rms, width=10)
        line = f"\r\033[K{state} {vu}"[:42]
        sys.stdout.write(line)
        sys.stdout.flush()

    async def mic_stream_loop(self, session: genai.live.AsyncSession, ffmpeg_proc: subprocess.Popen):
        last_meter_update = 0.0
        consecutive_speech = 0
        silence_chunks = 0
        user_talking_logged = False
        pre_roll = collections.deque(maxlen=4)

        try:
            while not self.stop_event.is_set():
                chunk = await asyncio.to_thread(ffmpeg_proc.stdout.read, CHUNK_BYTES)
                if not chunk:
                    break

                raw = np.frombuffer(chunk, dtype=np.int16)
                # Apply digital gain with soft clipping limiter
                amplified = raw.astype(np.float32) * self.gain
                boosted = np.clip(amplified, -32768, 32767).astype(np.int16)
                boosted_bytes = boosted.tobytes()
                rms = compute_rms(boosted)

                now = time.monotonic()
                is_gemini_speaking = (now < self.playback_end_time)

                if is_gemini_speaking:
                    user_talking_logged = False
                    consecutive_speech = 0
                    silence_chunks = 0
                    pre_roll.clear()
                    # Assistant audio is actively coming out of speakers
                    # Mute mic stream to prevent acoustic feedback loop to server
                    if now - last_meter_update > 0.15:
                        sys.stdout.write("\r\033[K🔊 Gemini Speaking...   ")
                        sys.stdout.flush()
                        last_meter_update = now
                    continue
                else:
                    # Assistant has finished speaking: listening to user
                    # Clamp speech gate between 1350 and 1900 RMS so startup noise never locks out user voice
                    speech_gate = min(max(self.ambient_rms * 1.5, 1350.0), 1900.0)
                    HANGOVER_CHUNKS = 10  # ~640ms to bridge natural pauses between words

                    if rms > speech_gate:
                        consecutive_speech += 1
                        silence_chunks = 0

                        # Trigger on 2 consecutive chunks (~128ms) or 1 strong vocal burst (> 1.5x gate)
                        if not user_talking_logged and (consecutive_speech >= 2 or rms > speech_gate * 1.5):
                            log_event("USER", f"Speech detected (RMS: {rms:.0f}, gate: {speech_gate:.0f})")
                            user_talking_logged = True
                            # Flush pre-roll chunks so initial consonant/syllable is never clipped
                            for pr_chunk in pre_roll:
                                await session.send_realtime_input(
                                    media=types.Blob(
                                        data=pr_chunk,
                                        mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                                    )
                                )
                            pre_roll.clear()

                        if user_talking_logged:
                            if now - last_meter_update > 0.08:
                                self.render_meter(rms, "🗣️  Talking")
                                last_meter_update = now

                            # Stream real amplified audio chunk
                            await session.send_realtime_input(
                                media=types.Blob(
                                    data=boosted_bytes,
                                    mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                                )
                            )
                        else:
                            pre_roll.append(boosted_bytes)
                    else:
                        silence_chunks += 1
                        pre_roll.append(boosted_bytes)

                        # Adapt ambient floor downward quickly toward quiet room level, capped at 1000 RMS
                        if rms < self.ambient_rms:
                            self.ambient_rms = max(350.0, 0.90 * self.ambient_rms + 0.10 * rms)
                        else:
                            self.ambient_rms = min(1000.0, 0.995 * self.ambient_rms + 0.005 * rms)

                        if user_talking_logged:
                            if silence_chunks < HANGOVER_CHUNKS:
                                # Natural pause between words: KEEP streaming real audio so words stay connected!
                                await session.send_realtime_input(
                                    media=types.Blob(
                                        data=boosted_bytes,
                                        mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                                    )
                                )
                            else:
                                # User has completed the sentence (paused > 640ms)
                                log_event("USER", "Speech ended (sentence pause > 640ms)")
                                user_talking_logged = False
                                consecutive_speech = 0
                                # Send true silence to trigger Gemini's turn VAD
                                await session.send_realtime_input(
                                    media=types.Blob(
                                        data=b"\x00" * len(boosted_bytes),
                                        mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                                    )
                                )
                        else:
                            # Idle room ambient: send silence so Gemini is not flooded with noise
                            consecutive_speech = 0
                            await session.send_realtime_input(
                                media=types.Blob(
                                    data=b"\x00" * len(boosted_bytes),
                                    mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                                )
                            )

                        if now - last_meter_update > 0.08:
                            self.render_meter(rms, "🎙️  Listen ")
                            last_meter_update = now

        except asyncio.CancelledError:
            pass
        except Exception as e:
            if not self.stop_event.is_set():
                log_event("ERROR", f"Mic stream error: {e}")
                print(f"\n[Mic Stream Error] {e}", file=sys.stderr)
                self.stop_event.set()

    async def receive_loop(self, session: genai.live.AsyncSession):
        # session.receive() yields for a SINGLE turn and then breaks.
        # We loop it continuously to support multi-turn conversations!
        turn_chunks = 0
        user_text_parts = []
        gemini_text_parts = []

        while not self.stop_event.is_set():
            try:
                async for response in session.receive():
                    if response.tool_call:
                        calls = response.tool_call.function_calls
                        if calls:
                            sys.stdout.write(f"\r\033[K⚙️  Checking {calls[0].name}...   ")
                            sys.stdout.flush()
                        fn_responses = []
                        for call in calls:
                            try:
                                res = execute_tool_call(call)
                            except Exception as e:
                                log_event("ERROR", f"Error executing tool {call.name}: {e}")
                                res = {"error": f"Tool execution failed: {type(e).__name__}: {str(e)}"}
                            fn_responses.append(
                                types.FunctionResponse(
                                    id=call.id,
                                    name=call.name,
                                    response=res,
                                )
                            )
                        await session.send_tool_response(function_responses=fn_responses)
                        continue

                    sc = response.server_content
                    if not sc:
                        continue

                    # Capture user's transcribed speech
                    if sc.input_transcription and sc.input_transcription.text:
                        user_text_parts.append(sc.input_transcription.text)

                    # Capture Gemini's transcribed spoken output
                    if sc.output_transcription and sc.output_transcription.text:
                        gemini_text_parts.append(sc.output_transcription.text)

                    if sc.interrupted:
                        self.playback_end_time = 0.0
                        self.player.interrupt()
                        log_event("SERVER", "Response interrupted by server")
                        sys.stdout.write("\r\033[K⚡ [Interrupted]")
                        sys.stdout.flush()
                        continue

                    if sc.model_turn:
                        for part in sc.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                chunk_data = part.inline_data.data
                                if turn_chunks == 0:
                                    log_event("SERVER", "First response audio chunk received")
                                turn_chunks += 1
                                duration = len(chunk_data) / (OUTPUT_SAMPLE_RATE * 2)
                                self.playback_end_time = max(self.playback_end_time, time.monotonic()) + duration
                                self.player.write(chunk_data)

                    if sc.turn_complete:
                        # Flush full transcript turn
                        if user_text_parts:
                            full_user_text = "".join(user_text_parts).strip()
                            log_event("TRANSCRIPT_USER", full_user_text)
                            append_transcript("USER", full_user_text)
                            user_text_parts.clear()

                        if gemini_text_parts:
                            full_gemini_text = "".join(gemini_text_parts).strip()
                            log_event("TRANSCRIPT_GEMINI", full_gemini_text)
                            append_transcript("GEMINI", full_gemini_text)
                            gemini_text_parts.clear()

                        log_event("SERVER", f"Turn complete ({turn_chunks} chunks, playback ~{self.playback_end_time - time.monotonic():.1f}s)")
                        turn_chunks = 0
                        sys.stdout.write("\r\033[K🎙️  Listen...")
                        sys.stdout.flush()

            except asyncio.CancelledError:
                break
            except Exception as e:
                err_msg = str(e).lower()
                if any(x in err_msg for x in ["1000", "cancelled", "closed", "connection"]):
                    log_event("SESSION", f"WebSocket closed ({e}). Stopping session.")
                    self.stop_event.set()
                    break
                if not self.stop_event.is_set():
                    log_event("ERROR", f"Receive turn error (recovering): {e}")
                    await asyncio.sleep(0.5)

    async def run(self):
        print(f"Connecting to Gemini Live on Vertex AI ({self.project})...")
        print(f"Model: {self.model} | Voice: {self.voice_name}")

        client = genai.Client(
            vertexai=True,
            project=self.project,
            location=self.location,
        )

        recent_memory = ""
        try:
            with open(TRANSCRIPT_FILE, "r") as tf:
                tlines = [l.strip() for l in tf.readlines() if l.strip()]
            if tlines:
                recent_memory = "\n\nRECENT CONVERSATION MEMORY (Maintain seamless continuity with this ongoing conversation):\n" + "\n".join(tlines[-14:])
        except Exception:
            pass

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            tools=LIVE_TOOLS,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice_name
                    )
                )
            ),
            system_instruction=types.Content(
                parts=[types.Part.from_text(text=SYSTEM_INSTRUCTION + recent_memory)]
            ),
        )

        ffmpeg_proc = subprocess.Popen(
            [
                "ffmpeg",
                "-loglevel",
                "quiet",
                "-f",
                "pulse",
                "-i",
                "default",
                "-ac",
                "1",
                "-ar",
                str(INPUT_SAMPLE_RATE),
                "-f",
                "s16le",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        try:
            # Discard first 2 startup chunks (avoid device open click/pop)
            for _ in range(2):
                ffmpeg_proc.stdout.read(CHUNK_BYTES)

            samples = []
            for _ in range(6):
                c = ffmpeg_proc.stdout.read(CHUNK_BYTES)
                if c:
                    r = compute_rms(np.frombuffer(c, dtype=np.int16) * self.gain)
                    samples.append(r)
            if samples:
                lowest = sorted(samples)[:3]
                self.ambient_rms = min(max(sum(lowest) / len(lowest), 350.0), 950.0)

            log_event("SESSION", f"Calibrated: Ambient RMS {self.ambient_rms:.0f}, Gain {self.gain}x")
            print(f"Calibrated: Ambient RMS: {self.ambient_rms:.0f} | Gain: {self.gain:.1f}x")

            async with client.aio.live.connect(model=self.model, config=config) as session:
                print("\n" + "=" * 60)
                print("✨ GEMINI LIVE READY (Full-Duplex Terminal Mode) ✨")
                print("- Keepalive ping timeout: DISABLED (indefinite connection)")
                print("- Full-duplex: Stream naturally, pause to get answers")
                print("- Echo protection: Active while assistant speaks")
                print("Press Ctrl+C to exit.")
                print("=" * 60 + "\n")

                mic_task = asyncio.create_task(self.mic_stream_loop(session, ffmpeg_proc))
                recv_task = asyncio.create_task(self.receive_loop(session))

                await asyncio.gather(mic_task, recv_task)

        finally:
            self.stop_event.set()
            try:
                ffmpeg_proc.kill()
            except Exception:
                pass
            self.player.close()


def main():
    parser = argparse.ArgumentParser(description="Gemini Live Terminal Client")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="GCP project ID")
    parser.add_argument("--location", default=DEFAULT_LOCATION, help="Vertex AI region")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Live model ID")
    parser.add_argument(
        "--voice",
        default=DEFAULT_VOICE,
        choices=["Aoede", "Puck", "Charon", "Kore", "Fenrir"],
        help="Voice persona name",
    )
    parser.add_argument(
        "--gain",
        type=float,
        default=3.0,
        help="Microphone digital gain multiplier (default: 3.0 = +9.5dB boost)",
    )
    args = parser.parse_args()

    bridge = GeminiLiveBridge(
        project=args.project,
        location=args.location,
        model=args.model,
        voice_name=args.voice,
        gain=args.gain,
    )

    def handle_sigint(signum, frame):
        print("\n\nDisconnecting Gemini Live...")
        bridge.stop_event.set()
        bridge.player.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)

    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
