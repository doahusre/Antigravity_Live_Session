#!/usr/bin/env python3
"""Real-Time On-Screen Visual Dashboard for Antigravity Lead Agent & Voice Watchdog.

Displays live in the terminal pane:
1. Antigravity Lead Agent's real-time thought stream & active tool executions.
2. Dispatched Voice Tasks & Resolution Status (from painpoints.log).
3. Live Voice Telemetry & Watchdog status (from session.log).
"""

import json
import os
import shutil
import sys
import textwrap
import time

TRANSCRIPT_JSONL = "/home/doa/.gemini/antigravity-cli/brain/5fbf4c4f-181a-40f1-9410-8e7403c758f1/.system_generated/logs/transcript.jsonl"
PAINPOINTS_LOG = "/home/doa/Projects/.agents/voice/painpoints.log"
SESSION_LOG = "/home/doa/Projects/.agents/voice/session.log"

# ANSI Colors
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"
RED = "\033[31m"
WHITE = "\033[97m"


def get_lead_agent_thoughts(max_width: int):
    """Read latest thinking and tool calls from Antigravity's transcript.jsonl."""
    thought_text = "Standing by — monitoring live voice session..."
    last_tools = []
    step_time = ""
    is_active = False

    if not os.path.exists(TRANSCRIPT_JSONL):
        return is_active, step_time, thought_text, last_tools

    try:
        # Read last ~48KB of transcript.jsonl
        file_size = os.path.getsize(TRANSCRIPT_JSONL)
        with open(TRANSCRIPT_JSONL, "rb") as f:
            if file_size > 49152:
                f.seek(file_size - 49152)
            raw_lines = f.read().decode("utf-8", errors="ignore").splitlines()

        for line in reversed(raw_lines):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            if obj.get("type") == "PLANNER_RESPONSE":
                created = obj.get("created_at", "")
                if "T" in created:
                    step_time = created.split("T")[1][:8]
                thinking = (obj.get("thinking") or "").strip()
                content = (obj.get("content") or "").strip()
                tcalls = obj.get("tool_calls") or []
                for tc in tcalls:
                    tname = tc.get("name", "tool")
                    targs = tc.get("args") or {}
                    summary = targs.get("toolSummary") or targs.get("Path") or tname
                    last_tools.append(f"{tname}({os.path.basename(str(summary))})")

                raw_thought = thinking if thinking else content
                if raw_thought:
                    # Clean markdown and take last few meaningful sentences
                    clean_lines = [
                        l.strip().lstrip("#*- ")
                        for l in raw_thought.splitlines()
                        if l.strip() and not l.strip().startswith("```")
                    ]
                    thought_text = " ".join(clean_lines[-3:]) if clean_lines else raw_thought
                is_active = len(tcalls) > 0
                break
    except Exception as e:
        thought_text = f"Reading telemetry: {e}"

    wrapped = textwrap.wrap(thought_text, width=max(30, max_width - 4))
    return is_active, step_time, wrapped[:4], last_tools[:3]


def get_tasks_summary(max_width: int):
    """Parse dispatched and resolved tasks from painpoints.log."""
    dispatched = []
    resolved = []
    if not os.path.exists(PAINPOINTS_LOG):
        return dispatched, resolved

    try:
        with open(PAINPOINTS_LOG, "r", errors="ignore") as f:
            lines = f.readlines()

        for line in lines:
            line_s = line.strip()
            if "[TASK_DISPATCHED]" in line_s:
                parts = line_s.split("[TASK_DISPATCHED]", 1)
                ts = parts[0].strip()
                body = parts[1].strip().split(":")[0]
                dispatched.append((ts, body))
            elif "[TASK_RESOLVED]" in line_s or "[CAPABILITY_DEPLOYED]" in line_s:
                tag = "[TASK_RESOLVED]" if "[TASK_RESOLVED]" in line_s else "[CAPABILITY_DEPLOYED]"
                parts = line_s.split(tag, 1)
                ts = parts[0].strip()
                body = parts[1].strip().rstrip(":")
                resolved.append((ts, body))
    except Exception:
        pass

    return dispatched[-3:], resolved[-4:]


def get_voice_events(max_width: int):
    """Read last few events from session.log."""
    events = []
    if not os.path.exists(SESSION_LOG):
        return events
    try:
        with open(SESSION_LOG, "r", errors="ignore") as f:
            lines = f.readlines()
        for l in lines[-5:]:
            s = l.strip()
            if s:
                events.append(s[: max_width - 2])
    except Exception:
        pass
    return events


def render_dashboard(tick: int):
    cols, rows = shutil.get_terminal_size((85, 23))
    w = max(40, cols - 1)
    spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    spin = spinners[tick % len(spinners)]
    now_str = time.strftime("%H:%M:%S")

    is_active, step_time, thought_lines, tools = get_lead_agent_thoughts(w)
    dispatched, resolved = get_tasks_summary(w)
    voice_events = get_voice_events(w)

    out = ["\033[H"]  # Move cursor to top-left without clearing screen (zero flicker)

    # Header bar
    status_badge = f"{GREEN}{BOLD}● ACTIVE{RESET}" if is_active else f"{CYAN}{BOLD}{spin} WATCHING{RESET}"
    title = f"{MAGENTA}{BOLD}⚡ ANTIGRAVITY LIVE BRAIN & HUD{RESET} [{status_badge}] {DIM}{now_str}{RESET}"
    out.append(title + "\033[K")
    out.append(f"{DIM}{'─' * w}{RESET}\033[K")

    # Section 1: Lead Agent Thought Stream
    out.append(f"{CYAN}{BOLD}🧠 LEAD AGENT THOUGHT STREAM:{RESET}\033[K")
    for tl in thought_lines:
        out.append(f"  {WHITE}{tl[:w-3]}{RESET}\033[K")
    if tools:
        t_str = ", ".join(tools)
        out.append(f"  {YELLOW}🔧 Active Tools:{RESET} {t_str[:w-19]}\033[K")
    else:
        out.append(f"  {DIM}🔧 Tools: Ready & listening for voice escalations{RESET}\033[K")

    out.append(f"{DIM}{'─' * w}{RESET}\033[K")

    # Section 2: Dispatched & Resolved Engineering Tasks
    out.append(f"{GREEN}{BOLD}📋 LIVE ENGINEERING TASKS & DEPLOYMENTS:{RESET}\033[K")
    for ts, rbody in resolved[-3:]:
        line_str = f"  {GREEN}✔{RESET} {DIM}{ts}{RESET} {rbody}"
        out.append(f"{line_str[:w+18]}\033[K")
    for ts, dbody in dispatched[-2:]:
        line_str = f"  {YELLOW}⚡{RESET} {DIM}{ts}{RESET} {dbody}"
        out.append(f"{line_str[:w+18]}\033[K")

    out.append(f"{DIM}{'─' * w}{RESET}\033[K")

    # Section 3: Voice Telemetry
    out.append(f"{BLUE}{BOLD}🎙️  VOICE & WATCHDOG TELEMETRY:{RESET}\033[K")
    for ev in voice_events[-4:]:
        color = GREEN if "TRANSCRIPT_GEMINI" in ev else (YELLOW if "TRANSCRIPT_USER" in ev else DIM)
        out.append(f"  {color}{ev[:w-3]}{RESET}\033[K")

    # Clear remaining lines up to rows - 1
    current_lines = len(out)
    for _ in range(max(0, rows - current_lines - 1)):
        out.append("\033[K")

    sys.stdout.write("\n".join(out))
    sys.stdout.flush()


def main():
    # Hide cursor and clear once
    sys.stdout.write("\033[?25l\033[2J")
    sys.stdout.flush()
    tick = 0
    try:
        while True:
            render_dashboard(tick)
            tick += 1
            time.sleep(0.4)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
