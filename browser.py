"""Drop-in browser module for interactive browser automation.

Provides top-level click, scroll, navigate, type, and read functions
powered by VoiceBrowserController.
"""

import sys
sys.path.append("/home/doa/Projects/.agents/voice")
from browser_controller import VoiceBrowserController

_ctl = VoiceBrowserController.get_instance()

def navigate(url: str):
    return _ctl.navigate(url)

def open(url: str):
    return _ctl.navigate(url)

def click(target: str):
    return _ctl.click(target)

def scroll(direction: str = "down"):
    return _ctl.scroll_desktop(direction)

def type(text: str, press_enter: bool = True):
    return _ctl.type_and_search(text, press_enter)

def read_page():
    return _ctl.read_screen_eyes()

def read_screen():
    return _ctl.read_screen_eyes()

def close_tab():
    return _ctl.close_current_tab()

def close_window():
    return _ctl.close_current_window()

def switch_focus(direction: str = "next"):
    return _ctl.switch_focus(direction)

def focus_window(target: str):
    return _ctl.focus_window(target)

def list_windows():
    return _ctl.list_windows()

def launch_game(name: str):
    return _ctl.launch_game(name)
