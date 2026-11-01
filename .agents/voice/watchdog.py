#!/usr/bin/env python3
"""Watchdog daemon for Gemini Live Voice Assistant.

Monitors /home/doa/Projects/.agents/voice/session.log in real time.
Surfaces pain points (unresponsive server, clipping, noise floor, dropouts)
to /home/doa/Projects/.agents/voice/painpoints.log.
"""

import os
import re
import sys
import time

LOG_FILE = "/home/doa/Projects/.agents/voice/session.log"
PAINPOINT_FILE = "/home/doa/Projects/.agents/voice/painpoints.log"

def log_painpoint(ptype: str, details: str):
    t = time.strftime("%H:%M:%S")
    entry = f"[{t}] [{ptype}] {details}\n"
    sys.stdout.write(entry)
    sys.stdout.flush()
    with open(PAINPOINT_FILE, "a") as f:
        f.write(entry)

def main():
    print(f"Starting Voice Watchdog on {LOG_FILE}...")
    log_painpoint("WATCHDOG_START", "Watchdog monitoring service started.")

    # Wait for log file if not present
    while not os.path.exists(LOG_FILE):
        time.sleep(0.5)

    with open(LOG_FILE, "r") as f:
        # Seek to end
        f.seek(0, os.SEEK_END)

        last_speech_time = None
        speech_in_progress = False
        speech_rms_samples = []

        while True:
            line = f.readline()
            now = time.monotonic()

            if line:
                line = line.strip()
                if not line:
                    continue

                if "[USER] Speech detected" in line:
                    speech_in_progress = True
                    last_speech_time = now
                    # Extract RMS
                    m = re.search(r"RMS:\s*(\d+)", line)
                    if m:
                        rms = int(m.group(1))
                        speech_rms_samples.append(rms)
                        if rms > 30000:
                            log_painpoint("MIC_CLIPPING", f"Microphone clipped at {rms} RMS")

                elif "[USER] Speech ended" in line:
                    speech_in_progress = False
                    last_speech_time = now

                elif "[SERVER] First response audio chunk" in line:
                    # Server responded!
                    if last_speech_time is not None:
                        latency = now - last_speech_time
                        if latency > 4.0:
                            log_painpoint("HIGH_LATENCY", f"Gemini response took {latency:.2f}s")
                    last_speech_time = None
                    speech_rms_samples.clear()

                elif "[SERVER] Turn complete" in line:
                    # Server finished turn (including 0-chunk completions)
                    last_speech_time = None
                    speech_rms_samples.clear()

                elif "[TRANSCRIPT_USER]" in line:
                    m = re.search(r"\[TRANSCRIPT_USER\]\s*(.*)", line)
                    if m:
                        user_text = m.group(1).strip()
                        log_painpoint("CONVERSATION_CONTENT", f"User: \"{user_text}\"")

                elif "[TOOL] Executing tool" in line:
                    # Tool is running, extend timeout
                    last_speech_time = None
                    speech_rms_samples.clear()

            else:
                # No new line. Check if user spoke but server has not responded!
                if last_speech_time is not None and not speech_in_progress:
                    silence_duration = now - last_speech_time
                    if silence_duration > 2.8:
                        # Only alert if speech was more than transient pop (> 2 chunks / > 130ms)
                        if len(speech_rms_samples) >= 3:
                            avg_rms = sum(speech_rms_samples) / max(1, len(speech_rms_samples))
                            log_painpoint(
                                "UNRESPONSIVE_VAD",
                                f"User finished speaking ({len(speech_rms_samples)} chunks, avg RMS {avg_rms:.0f}), "
                                f"but Gemini has not responded after {silence_duration:.1f}s. "
                                f"Likely cause: room noise preventing VAD silence detection."
                            )
                        # Reset to avoid spamming the alert
                        last_speech_time = None
                        speech_rms_samples.clear()

                time.sleep(0.1)

if __name__ == "__main__":
    main()
