#!/usr/bin/env python3
"""Desktop, Screen & Browser Controller for Gemini Live.

Features:
- Screen 'Eyes': Real-time full-screen visual OCR via grim + tesseract (reads active tabs, UI, terminal).
- Window & Tab Control: Closes tabs via wtype (Ctrl+W) and windows via Niri compositor.
- Thread-safe Playwright Engine: Operates in an isolated dedicated OS worker thread (zero asyncio conflict).
- Interactive navigation, click by text, scroll, and input typing.
"""

import concurrent.futures
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional
from playwright.sync_api import sync_playwright

class VoiceBrowserController:
    _instance: Optional["VoiceBrowserController"] = None
    _executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    @classmethod
    def get_instance(cls) -> "VoiceBrowserController":
        if cls._instance is None:
            cls._instance = VoiceBrowserController()
        return cls._instance

    # -------------------------------------------------------------
    # Desktop & Screen Actions (Real-time Wayland / Niri)
    # -------------------------------------------------------------

    def read_screen_eyes(self) -> Dict[str, Any]:
        """Takes a real-time screenshot of the Wayland screen (OCR) AND captures the live Antigravity terminal interface."""
        try:
            env = os.environ.copy()
            env["WAYLAND_DISPLAY"] = "wayland-1"
            env["DISPLAY"] = ":0"
            
            shot_path = "/tmp/screen_eyes.png"
            # 1. Take Wayland screenshot
            res = subprocess.run(["grim", shot_path], env=env, capture_output=True, timeout=3)
            if res.returncode != 0:
                return {"status": "error", "message": f"Screenshot failed: {res.stderr.decode()}"}

            # 2. Extract visible text with Tesseract
            tess_res = subprocess.run(
                ["tesseract", shot_path, "stdout", "-l", "eng"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            raw_text = tess_res.stdout.strip()
            lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
            screen_summary = "\n".join(lines[:75])

            # 3. Also capture live Antigravity TUI & tmux panes directly for 100% lossless terminal visibility
            tmux_ui = ""
            try:
                ag_pane = subprocess.run(
                    ["tmux", "capture-pane", "-t", "%6", "-p", "-S", "-35"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if ag_pane.returncode == 0 and ag_pane.stdout.strip():
                    ag_lines = [l.rstrip() for l in ag_pane.stdout.splitlines() if l.strip()]
                    tmux_ui = "\n".join(ag_lines[-30:])
            except Exception:
                pass

            return {
                "status": "success",
                "message": "Successfully inspected screen OCR and Antigravity terminal interface.",
                "visible_text": screen_summary or "Screen appears blank or non-textual.",
                "antigravity_terminal_interface": tmux_ui or "No tmux interface captured.",
            }
        except Exception as e:
            return {"status": "error", "message": f"Screen inspection failed: {e}"}

    def open_browser_tab(self, url: Optional[str] = None) -> Dict[str, Any]:
        """Opens a new tab in the existing Chromium window (Ctrl+T) or launches Chromium if not open."""
        try:
            env = os.environ.copy()
            env["WAYLAND_DISPLAY"] = "wayland-1"
            env["DISPLAY"] = ":0"

            # Check open windows for Chromium
            res = subprocess.run(["niri", "msg", "-j", "windows"], env=env, capture_output=True, text=True, timeout=3)
            chromium_win = None
            if res.returncode == 0:
                try:
                    windows = json.loads(res.stdout)
                    for w in windows:
                        app = (w.get("app_id") or "").lower()
                        title = (w.get("title") or "").lower()
                        if "chromium" in app or "chromium" in title or "chrome" in app:
                            chromium_win = w
                            break
                except Exception:
                    pass

            target_url = None
            if url and url.strip():
                clean_url = url.strip()
                if not clean_url.startswith(("http://", "https://", "about:", "chrome://")):
                    if "." in clean_url and " " not in clean_url:
                        target_url = f"https://{clean_url}"
                    else:
                        target_url = f"https://duckduckgo.com/?q={clean_url.replace(' ', '+')}"
                else:
                    target_url = clean_url

            if chromium_win:
                # Focus the existing Chromium window
                subprocess.run(["niri", "msg", "action", "focus-window", "--id", str(chromium_win["id"])], env=env, timeout=2)
                time.sleep(0.2)
                # Send Ctrl+T
                subprocess.run(["wtype", "-M", "ctrl", "-k", "t", "-m", "ctrl"], env=env, timeout=2)
                time.sleep(0.3)
                if target_url:
                    subprocess.run(["wtype", target_url], env=env, timeout=3)
                    time.sleep(0.1)
                    subprocess.run(["wtype", "-k", "Return"], env=env, timeout=2)
                return {
                    "status": "success",
                    "message": f"Opened a new tab in active Chromium window{' with ' + target_url if target_url else ''}.",
                }
            else:
                cmd = ["chromium"]
                if target_url:
                    cmd.append(target_url)
                subprocess.Popen(cmd, env=env)
                return {
                    "status": "success",
                    "message": f"Launched Chromium{' navigating to ' + target_url if target_url else ''}.",
                }
        except Exception as e:
            return {"status": "error", "message": f"Failed to open browser tab: {e}"}

    def close_current_tab(self) -> Dict[str, Any]:
        """Closes the current browser tab by sending Ctrl+W via Wayland wtype."""
        try:
            env = os.environ.copy()
            env["WAYLAND_DISPLAY"] = "wayland-1"
            # If Playwright page is active in worker, also close it
            try:
                if self.page and not self.page.is_closed():
                    self._executor.submit(self.page.close).result(timeout=1)
            except Exception:
                pass
            subprocess.run(["wtype", "-M", "ctrl", "-k", "w", "-m", "ctrl"], env=env, timeout=2)
            time.sleep(0.3)
            return {"status": "success", "message": "Closed the current browser tab (sent Ctrl+W)."}
        except Exception as e:
            return {"status": "error", "message": f"Failed to close tab: {e}"}

    def maximize_column(self) -> Dict[str, Any]:
        """Toggles column maximization across screen width (Mod+M in Niri)."""
        try:
            env = os.environ.copy()
            env["WAYLAND_DISPLAY"] = "wayland-1"
            subprocess.run(["niri", "msg", "action", "maximize-column"], env=env, timeout=2)
            return {"status": "success", "message": "Toggled column maximization (maximize-column)."}
        except Exception as e:
            return {"status": "error", "message": f"Failed to maximize column: {e}"}

    def fullscreen_window(self) -> Dict[str, Any]:
        """Toggles fullscreen mode for the active window."""
        try:
            env = os.environ.copy()
            env["WAYLAND_DISPLAY"] = "wayland-1"
            subprocess.run(["niri", "msg", "action", "fullscreen-window"], env=env, timeout=2)
            return {"status": "success", "message": "Toggled fullscreen window."}
        except Exception as e:
            return {"status": "error", "message": f"Failed to fullscreen window: {e}"}

    def close_current_window(self) -> Dict[str, Any]:
        """Closes the focused window via Niri window manager."""
        try:
            subprocess.run(["niri", "msg", "action", "close-window"], timeout=2)
            return {"status": "success", "message": "Closed the active window."}
        except Exception as e:
            return {"status": "error", "message": f"Failed to close window: {e}"}

    def scroll_desktop(self, direction: str = "down") -> Dict[str, Any]:
        """Scrolls the active window using Wayland key events (Page_Down / Page_Up)."""
        try:
            env = os.environ.copy()
            env["WAYLAND_DISPLAY"] = "wayland-1"
            key = "Page_Down" if direction.lower() == "down" else "Page_Up"
            subprocess.run(["wtype", "-k", key], env=env, timeout=2)
            return {"status": "success", "message": f"Scrolled {direction}."}
        except Exception as e:
            return {"status": "error", "message": f"Scroll failed: {e}"}

    def list_windows(self) -> Dict[str, Any]:
        """Lists all open windows in the Niri Wayland compositor."""
        try:
            env = os.environ.copy()
            env["WAYLAND_DISPLAY"] = "wayland-1"
            res = subprocess.run(["niri", "msg", "-j", "windows"], env=env, capture_output=True, text=True, timeout=3)
            if res.returncode != 0:
                return {"status": "error", "message": f"Failed to list windows: {res.stderr}"}
            windows = json.loads(res.stdout)
            summary = []
            for w in windows:
                summary.append({
                    "id": w.get("id"),
                    "title": w.get("title", ""),
                    "app_id": w.get("app_id", ""),
                    "is_focused": w.get("is_focused", False),
                })
            return {"status": "success", "windows": summary}
        except Exception as e:
            return {"status": "error", "message": f"Failed to list windows: {e}"}

    def switch_focus(self, direction: str = "next") -> Dict[str, Any]:
        """Cycles focus to the next or previous window column in Niri."""
        try:
            env = os.environ.copy()
            env["WAYLAND_DISPLAY"] = "wayland-1"
            action = "focus-column-right-or-first" if direction.lower() in ("next", "right", "forward") else "focus-column-left-or-last"
            subprocess.run(["niri", "msg", "action", action], env=env, timeout=2)
            time.sleep(0.2)
            return {"status": "success", "message": f"Focused {direction} window."}
        except Exception as e:
            return {"status": "error", "message": f"Failed to switch focus: {e}"}

    def focus_window(self, target: str) -> Dict[str, Any]:
        """Focuses a specific window by title substring, app_id (e.g. 'chromium', 'kitty', 'openlauncher', 'eden'), or window ID."""
        try:
            env = os.environ.copy()
            env["WAYLAND_DISPLAY"] = "wayland-1"
            res = subprocess.run(["niri", "msg", "-j", "windows"], env=env, capture_output=True, text=True, timeout=3)
            if res.returncode != 0:
                return {"status": "error", "message": f"Failed to query windows: {res.stderr}"}
            windows = json.loads(res.stdout)
            target_str = str(target).lower().strip()
            
            # Match by ID first
            matched = None
            if target_str.isdigit():
                target_id = int(target_str)
                for w in windows:
                    if w.get("id") == target_id:
                        matched = w
                        break
            
            # Match by app_id or title
            if not matched:
                for w in windows:
                    app = (w.get("app_id") or "").lower()
                    title = (w.get("title") or "").lower()
                    if target_str in app or target_str in title:
                        matched = w
                        break
            
            if not matched:
                return {"status": "error", "message": f"No window found matching '{target}'."}

            win_id = matched["id"]
            subprocess.run(["niri", "msg", "action", "focus-window", "--id", str(win_id)], env=env, timeout=2)
            time.sleep(0.2)
            return {
                "status": "success",
                "message": f"Focused window {win_id}: {matched.get('title', '')} ({matched.get('app_id', '')})",
            }
        except Exception as e:
            return {"status": "error", "message": f"Failed to focus window: {e}"}

    def launch_game(self, name: str) -> Dict[str, Any]:
        """Launches a game such as Zelda (Breath of the Wild) in Eden or OpenLauncher."""
        try:
            env = os.environ.copy()
            env["WAYLAND_DISPLAY"] = "wayland-1"
            env["DISPLAY"] = ":0"
            lower_name = name.lower()

            if "zelda" in lower_name or "botw" in lower_name or "breath of the wild" in lower_name:
                rom_path = "/home/doa/Games/The Legend of Zelda Breath of the Wild [NSZ]/exefs/main.npdm"
                cmd = f"eden '{rom_path}' > /tmp/eden_game.log 2>&1 &"
                subprocess.Popen(cmd, shell=True, env=env)
                time.sleep(1.5)
                # Confirm format dialog if presented
                subprocess.run(["wtype", "-k", "Return"], env=env, timeout=2)
                return {
                    "status": "success",
                    "message": "Launched The Legend of Zelda: Breath of the Wild in Eden emulator.",
                }
            elif "openlauncher" in lower_name or "rct" in lower_name or "roller" in lower_name:
                cmd = "/home/doa/Games/openlauncher > /tmp/openlauncher.log 2>&1 &"
                subprocess.Popen(cmd, shell=True, env=env)
                return {"status": "success", "message": "Launched OpenLauncher."}
            else:
                return {
                    "status": "error",
                    "message": f"Unknown game '{name}'. Available: 'The Legend of Zelda: Breath of the Wild', 'OpenLauncher', 'RCT2'.",
                }
        except Exception as e:
            return {"status": "error", "message": f"Failed to launch game: {e}"}

    # -------------------------------------------------------------
    # Playwright Automation (Executed in isolated thread worker)
    # -------------------------------------------------------------

    def _worker_ensure(self):
        env = os.environ.copy()
        env["DISPLAY"] = ":0"
        env["WAYLAND_DISPLAY"] = "wayland-1"

        if not self.playwright:
            self.playwright = sync_playwright().start()

        if self.browser:
            try:
                if not self.browser.is_connected():
                    self.browser = None
                    self.context = None
                    self.page = None
            except Exception:
                self.browser = None
                self.context = None
                self.page = None

        if not self.browser:
            self.browser = self.playwright.chromium.launch(
                executable_path="/usr/bin/chromium",
                headless=False,
                env=env,
                args=["--start-maximized"],
            )
            self.context = self.browser.new_context(no_viewport=True)

        try:
            if not self.page or self.page.is_closed():
                self.page = self.context.new_page()
        except Exception:
            try:
                self.context = self.browser.new_context(no_viewport=True)
                self.page = self.context.new_page()
            except Exception:
                self.browser = self.playwright.chromium.launch(
                    executable_path="/usr/bin/chromium",
                    headless=False,
                    env=env,
                    args=["--start-maximized"],
                )
                self.context = self.browser.new_context(no_viewport=True)
                self.page = self.context.new_page()

    def _worker_navigate(self, url: str) -> Dict[str, Any]:
        self._worker_ensure()
        if not url.startswith(("http://", "https://", "about:", "chrome://")):
            if "." in url and not " " in url:
                url = f"https://{url}"
            else:
                url = f"https://duckduckgo.com/?q={url.replace(' ', '+')}"
        self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
        time.sleep(0.5)
        return {
            "status": "success",
            "title": self.page.title(),
            "url": self.page.url,
            "message": f"Navigated to {self.page.title()} ({self.page.url})",
        }

    def _worker_click(self, target: str) -> Dict[str, Any]:
        self._worker_ensure()
        # 1. Try finding by visible text (case-insensitive substring)
        loc = self.page.get_by_text(target, exact=False).first
        if loc.count() > 0 and loc.is_visible():
            loc.click(timeout=4000)
        elif self.page.get_by_role("button", name=target).first.count() > 0:
            self.page.get_by_role("button", name=target).first.click(timeout=4000)
        elif self.page.get_by_role("link", name=target).first.count() > 0:
            self.page.get_by_role("link", name=target).first.click(timeout=4000)
        else:
            self.page.locator(f"text='{target}'").first.click(timeout=4000)

        time.sleep(1.0)
        return {
            "status": "success",
            "message": f"Clicked '{target}'. Current page: {self.page.title()} ({self.page.url})",
            "title": self.page.title(),
            "url": self.page.url,
        }

    def _worker_type(self, text: str, press_enter: bool = True) -> Dict[str, Any]:
        self._worker_ensure()
        inputs = self.page.locator("input[type='text'], input[type='search'], textarea, input:not([type='hidden'])")
        if inputs.count() > 0:
            inp = inputs.first
            inp.fill(text)
            if press_enter:
                inp.press("Enter")
        else:
            self.page.keyboard.type(text)
            if press_enter:
                self.page.keyboard.press("Enter")

        time.sleep(1.0)
        return {
            "status": "success",
            "message": f"Typed '{text}'. Current page: {self.page.title()} ({self.page.url})",
        }

    # Public thread-isolated entrypoints
    def navigate(self, url: str) -> Dict[str, Any]:
        try:
            return self._executor.submit(self._worker_navigate, url).result(timeout=20)
        except Exception as e:
            return {"status": "error", "message": f"Navigation failed: {e}"}

    def click(self, target: str) -> Dict[str, Any]:
        try:
            return self._executor.submit(self._worker_click, target).result(timeout=10)
        except Exception as e:
            return {"status": "error", "message": f"Click failed: {e}"}

    def type_and_search(self, text: str, press_enter: bool = True) -> Dict[str, Any]:
        try:
            return self._executor.submit(self._worker_type, text, press_enter).result(timeout=10)
        except Exception as e:
            return {"status": "error", "message": f"Typing failed: {e}"}
