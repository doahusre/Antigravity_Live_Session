#!/usr/bin/env python3
"""Voice Experience & Intent Monitor.

Continuously monitors:
1. /home/doa/Projects/.agents/voice/painpoints.log (VAD, latency, clipping)
2. /home/doa/Projects/.agents/voice/session.log (real-time stream & errors)
3. /home/doa/Projects/.agents/voice/live_transcript.md (user conversational intent & coding requests)
4. Process health of gemini_live.py

Exits immediately whenever:
- User speaks a new conversational turn in live_transcript.md ([USER_INTENT])
- An experience painpoint or alert is detected ([ALERT])
- Gemini process dies ([PROCESS_CRASH])
- 60s heartbeat window elapses cleanly ([HEARTBEAT_HEALTHY])
"""

import json
import os
import re
import subprocess
import sys
import time

PAINPOINTS_FILE = "/home/doa/Projects/.agents/voice/painpoints.log"
SESSION_FILE = "/home/doa/Projects/.agents/voice/session.log"
TRANSCRIPT_FILE = "/home/doa/Projects/.agents/voice/live_transcript.md"
STATE_FILE = "/tmp/voice_monitor_offsets.json"
MAX_WAIT_SECONDS = 60.0

def load_offsets():
    defaults = {"painpoints_offset": 0, "session_offset": 0, "transcript_offset": 0}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                return (
                    data.get("painpoints_offset", 0),
                    data.get("session_offset", 0),
                    data.get("transcript_offset", 0),
                )
        except Exception:
            pass
    pp_off = os.path.getsize(PAINPOINTS_FILE) if os.path.exists(PAINPOINTS_FILE) else 0
    ss_off = os.path.getsize(SESSION_FILE) if os.path.exists(SESSION_FILE) else 0
    tr_off = os.path.getsize(TRANSCRIPT_FILE) if os.path.exists(TRANSCRIPT_FILE) else 0
    return pp_off, ss_off, tr_off

def save_offsets(pp_off, ss_off, tr_off=None):
    if tr_off is None:
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                tr_off = data.get("transcript_offset", 0)
        except Exception:
            tr_off = 0
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({
                "painpoints_offset": pp_off,
                "session_offset": ss_off,
                "transcript_offset": tr_off,
            }, f)
    except Exception:
        pass

def is_gemini_alive():
    try:
        out = subprocess.check_output(["pgrep", "-f", "gemini_live.py"]).decode().strip()
        return bool(out)
    except subprocess.CalledProcessError:
        return False

def main():
    pp_off, ss_off, tr_off = load_offsets()

    for path in (PAINPOINTS_FILE, SESSION_FILE, TRANSCRIPT_FILE):
        if not os.path.exists(path):
            with open(path, "w") as f:
                pass

    if os.path.getsize(PAINPOINTS_FILE) < pp_off:
        pp_off = 0
    if os.path.getsize(SESSION_FILE) < ss_off:
        ss_off = 0
    if os.path.getsize(TRANSCRIPT_FILE) < tr_off:
        tr_off = 0

    pp_f = open(PAINPOINTS_FILE, "r")
    pp_f.seek(pp_off)

    ss_f = open(SESSION_FILE, "r")
    ss_f.seek(ss_off)

    tr_f = open(TRANSCRIPT_FILE, "r")
    tr_f.seek(tr_off)

    start_time = time.monotonic()
    last_speech_time = None
    speech_in_progress = False
    tool_in_progress = False
    speech_rms_samples = []
    last_process_check = 0.0

    try:
        while True:
            now = time.monotonic()

            # 1. Check process health
            if now - last_process_check > 2.0:
                last_process_check = now
                if not is_gemini_alive():
                    save_offsets(pp_f.tell(), ss_f.tell(), tr_f.tell())
                    print("[ALERT] [PROCESS_CRASH] gemini_live.py is not running!")
                    sys.exit(0)

            # 2. Check for new lines in live_transcript.md (USER INTENT)
            while True:
                tr_line = tr_f.readline()
                if not tr_line:
                    break
                tr_line = tr_line.strip()
                if not tr_line:
                    continue

                if "USER:**" in tr_line:
                    save_offsets(pp_f.tell(), ss_f.tell(), tr_f.tell())
                    print(f"[USER_INTENT] {tr_line}")
                    sys.exit(0)
                elif "DISPATCH" in tr_line or "TOOL_EXECUTION" in tr_line:
                    save_offsets(pp_f.tell(), ss_f.tell(), tr_f.tell())
                    print(f"[TOOL_EVENT] {tr_line}")
                    sys.exit(0)

            # 3. Check for new lines in painpoints.log
            while True:
                pp_line = pp_f.readline()
                if not pp_line:
                    break
                pp_line = pp_line.strip()
                if not pp_line or "WATCHDOG_START" in pp_line or "[TASK_RESOLVED]" in pp_line or "[CAPABILITY_DEPLOYED]" in pp_line:
                    continue
                if "[CONVERSATION_CONTENT]" in pp_line:
                    continue  # handled via live_transcript.md [USER_INTENT]
                save_offsets(pp_f.tell(), ss_f.tell(), tr_f.tell())
                print(f"[ALERT] [PAINPOINT_LOG] {pp_line}")
                sys.exit(0)

            # 4. Check for new lines in session.log
            while True:
                ss_line = ss_f.readline()
                if not ss_line:
                    break
                ss_line = ss_line.strip()
                if not ss_line:
                    continue

                if "[USER] Speech detected" in ss_line:
                    speech_in_progress = True
                    last_speech_time = now
                    m = re.search(r"RMS:\s*(\d+)", ss_line)
                    if m:
                        rms = int(m.group(1))
                        speech_rms_samples.append(rms)
                        if rms > 30000:
                            save_offsets(pp_f.tell(), ss_f.tell())
                            print(f"[ALERT] [MIC_CLIPPING] Microphone clipped at {rms} RMS: {ss_line}")
                            sys.exit(0)

                elif "[USER] Speech ended" in ss_line:
                    speech_in_progress = False
                    last_speech_time = now

                elif "[TOOL] Executing tool" in ss_line:
                    tool_in_progress = True

                elif "[SERVER] First response audio chunk" in ss_line:
                    if last_speech_time is not None:
                        latency = now - last_speech_time
                        if latency > (5.0 if tool_in_progress else 2.5):
                            save_offsets(pp_f.tell(), ss_f.tell())
                            ptype = "TOOL_LATENCY" if tool_in_progress else "HIGH_LATENCY"
                            print(f"[ALERT] [{ptype}] Gemini response took {latency:.2f}s: {ss_line}")
                            sys.exit(0)
                    last_speech_time = None
                    tool_in_progress = False
                    speech_rms_samples.clear()

                elif "[SERVER] Turn complete" in ss_line:
                    if not tool_in_progress:
                        last_speech_time = None
                        speech_rms_samples.clear()

                elif "[ERROR]" in ss_line:
                    save_offsets(pp_f.tell(), ss_f.tell())
                    print(f"[ALERT] [PROCESS_ERROR] Error logged in session: {ss_line}")
                    sys.exit(0)

            # 5. Check if user finished speaking but Gemini failed to reply
            if last_speech_time is not None and not speech_in_progress:
                silence_duration = now - last_speech_time
                timeout = 5.5 if tool_in_progress else 2.5
                if silence_duration > timeout:
                    count = len(speech_rms_samples)
                    if count >= 3:
                        avg_rms = sum(speech_rms_samples) / max(1, count)
                        save_offsets(pp_f.tell(), ss_f.tell())
                        ptype = "TOOL_TIMEOUT" if tool_in_progress else "UNRESPONSIVE_VAD"
                        print(
                            f"[ALERT] [{ptype}] User spoke ({count} chunks, avg RMS {avg_rms:.0f}), "
                            f"no Gemini response after {silence_duration:.2f}s."
                        )
                        sys.exit(0)
                    else:
                        last_speech_time = None
                        speech_rms_samples.clear()

            # 6. Heartbeat check
            if now - start_time >= MAX_WAIT_SECONDS:
                save_offsets(pp_f.tell(), ss_f.tell(), tr_f.tell())
                print(f"[HEARTBEAT_HEALTHY] All voice systems operational (monitoring window {MAX_WAIT_SECONDS:.0f}s completed).")
                sys.exit(0)

            time.sleep(0.08)

    finally:
        pp_f.close()
        ss_f.close()
        tr_f.close()

if __name__ == "__main__":
    main()
