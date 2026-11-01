#!/usr/bin/env python3
"""Antigravity Voice Agent Hook & Live Voice Chat Utility."""

import argparse
from collections import deque
import fcntl
import json
import math
import os
import re
import signal
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

VOICE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = VOICE_DIR / "config.json"
EDGE_TTS_BIN = VOICE_DIR / ".venv" / "bin" / "edge-tts"
PID_FILE = Path("/tmp/agy_voice_tts.pid")
LOCK_FILE = Path("/tmp/agy_voice_hook.lock")
STATE_FILE = Path("/tmp/agy_voice_last_step.json")
AUDIO_FILE = Path("/tmp/agy_voice_reply.mp3")
LIVE_LOG = Path("/tmp/agy_voice_live.log")

STOP_PHRASES = (
    "stop live chat",
    "exit live chat",
    "end live chat",
    "stop voice chat",
    "exit voice chat",
    "end voice chat",
    "stop listening",
    "pause voice",
    "pause live chat",
    "goodbye",
    "bye bye",
)


def log_live(msg: str) -> None:
    try:
        with open(LIVE_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def load_config() -> dict:
    default_cfg = {
        "enabled": True,
        "live_chat": True,
        "session_id": "5fbf4c4f-181a-40f1-9410-8e7403c758f1",
        "voice": "en-US-AvaMultilingualNeural",
        "rate": "+8%",
        "max_chars": 350,
        "listen_timeout": 18,
        "silence_duration": 1.2,
    }
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                default_cfg.update(json.load(f))
        except Exception:
            pass
    return default_cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def stop_playback() -> None:
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.killpg(pid, signal.SIGTERM)
        except Exception:
            pass
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass


def clean_markdown_for_speech(text: str, max_chars: int = 350) -> str:
    if not text:
        return ""
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[`*_#>|~—–-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > max_chars:
        cut = text[:max_chars]
        last_period = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        if last_period > max_chars // 3:
            text = cut[: last_period + 1]
        else:
            text = cut.rsplit(" ", 1)[0]
    return text


def speak_sync(text: str, cfg: dict) -> None:
    spoken = clean_markdown_for_speech(text, int(cfg.get("max_chars", 350)))
    if not spoken:
        return
    stop_playback()
    voice = cfg.get("voice", "en-US-AvaMultilingualNeural")
    rate = cfg.get("rate", "+8%")
    for attempt in range(2):
        try:
            subprocess.run(
                [
                    str(EDGE_TTS_BIN),
                    "--voice",
                    str(voice),
                    f"--rate={rate}",
                    "--text",
                    spoken,
                    "--write-media",
                    str(AUDIO_FILE),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            subprocess.run(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(AUDIO_FILE)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=35,
            )
            return
        except Exception as e:
            log_live(f"speak_sync attempt {attempt + 1} error: {e}")
            time.sleep(0.3)


def spawn_tts(text: str, cfg: dict) -> None:
    spoken = clean_markdown_for_speech(text, int(cfg.get("max_chars", 350)))
    if not spoken:
        return

    stop_playback()

    voice = cfg.get("voice", "en-US-AvaMultilingualNeural")
    rate = cfg.get("rate", "+8%")

    runner_code = (
        "import os, subprocess, sys\n"
        "text, edge_bin, voice, rate, audio_path, pid_path = sys.argv[1:7]\n"
        "try:\n"
        "    with open(pid_path, 'w') as pf:\n"
        "        pf.write(str(os.getpgrp()))\n"
        "    subprocess.run([edge_bin, '--voice', voice, f'--rate={rate}', '--text', text, '--write-media', audio_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)\n"
        "    subprocess.run(['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', audio_path], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "finally:\n"
        "    try:\n"
        "        os.remove(pid_path)\n"
        "    except Exception:\n"
        "        pass\n"
    )

    with open(os.devnull, "wb") as devnull:
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                runner_code,
                spoken,
                str(EDGE_TTS_BIN),
                str(voice),
                str(rate),
                str(AUDIO_FILE),
                str(PID_FILE),
            ],
            start_new_session=True,
            stdin=devnull,
            stdout=devnull,
            stderr=devnull,
        )


def play_chime(freqs: list[float], duration_ms: int = 80, volume: float = 0.12) -> None:
    """Play a gentle two-tone chime to indicate mic open/close."""
    sample_rate = 16000
    tone_path = Path("/tmp/agy_voice_chime.wav")
    try:
        frames = bytearray()
        for freq in freqs:
            n_samples = int(sample_rate * (duration_ms / 1000.0))
            for i in range(n_samples):
                env = math.sin(math.pi * i / n_samples)
                val = int(32767 * volume * env * math.sin(2 * math.pi * freq * i / sample_rate))
                frames.extend(struct.pack("<h", val))
        with wave.open(str(tone_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(frames))
        subprocess.run(
            ["pw-play", str(tone_path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except Exception:
        pass


def compute_rms(chunk: bytes) -> float:
    count = len(chunk) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", chunk[: count * 2])
    return math.sqrt(sum(s * s for s in samples) / count)


def listen_and_transcribe(cfg: dict) -> str | None:
    """Record speech from default microphone using VAD and transcribe via SpeechRecognition."""
    import speech_recognition as sr

    sample_rate = 16000
    chunk_ms = 100
    chunk_samples = (sample_rate * chunk_ms) // 1000
    chunk_bytes = chunk_samples * 2

    listen_timeout = float(cfg.get("listen_timeout", 18.0))
    silence_duration = float(cfg.get("silence_duration", 1.2))
    max_silence_chunks = max(5, int(silence_duration * 1000 / chunk_ms))
    max_speech_chunks = int(25 * 1000 / chunk_ms)

    # Play gentle rising chime: mic is now live
    play_chime([523.25, 659.25], duration_ms=75, volume=0.11)
    # Wait briefly so chime echo doesn't inflate ambient noise calibration
    time.sleep(0.25)

    proc = subprocess.Popen(
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
            str(sample_rate),
            "-f",
            "s16le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    try:
        assert proc.stdout is not None
        # Discard first 200ms buffer
        for _ in range(2):
            _ = proc.stdout.read(chunk_bytes)

        calibration_rms = []
        pre_roll: deque[bytes] = deque(maxlen=6)
        for _ in range(4):
            c = proc.stdout.read(chunk_bytes)
            if len(c) < chunk_bytes:
                break
            pre_roll.append(c)
            calibration_rms.append(compute_rms(c))

        raw_ambient = min(calibration_rms) if calibration_rms else 100.0
        ambient = min(max(raw_ambient, 40.0), 260.0)
        threshold = min(max(ambient * 1.65, ambient + 110.0, 170.0), 420.0)
        log_live(
            f"Mic listening (raw_ambient={raw_ambient:.0f}, ambient={ambient:.0f}, threshold={threshold:.0f})"
        )

        start_wait = time.monotonic()
        recognizer = sr.Recognizer()
        peak_rms = 0.0

        while (time.monotonic() - start_wait) < listen_timeout:
            speech_chunks: list[bytes] = []
            active_streak = 0
            silence_streak = 0
            speaking = False

            while True:
                if not speaking and (time.monotonic() - start_wait) >= listen_timeout:
                    break
                chunk = proc.stdout.read(chunk_bytes)
                if len(chunk) < chunk_bytes:
                    break
                rms = compute_rms(chunk)
                if rms > peak_rms:
                    peak_rms = rms

                if not speaking:
                    pre_roll.append(chunk)
                    if rms >= threshold:
                        active_streak += 1
                        if active_streak >= 2:
                            speaking = True
                            speech_chunks.extend(pre_roll)
                            silence_streak = 0
                            log_live(f"Speech started (rms={rms:.0f})")
                    else:
                        active_streak = 0
                else:
                    speech_chunks.append(chunk)
                    if rms < threshold * 0.85:
                        silence_streak += 1
                    else:
                        silence_streak = 0
                    if silence_streak >= max_silence_chunks or len(speech_chunks) >= max_speech_chunks:
                        log_live(f"Speech ended ({len(speech_chunks)} chunks, peak_rms={peak_rms:.0f})")
                        break

            if not speaking or len(speech_chunks) < 5:
                continue

            play_chime([659.25, 523.25], duration_ms=65, volume=0.09)

            pcm_data = b"".join(speech_chunks)
            audio_data = sr.AudioData(pcm_data, sample_rate, 2)
            try:
                transcript = recognizer.recognize_google(audio_data)
                if transcript and transcript.strip():
                    log_live(f"Transcribed: {transcript}")
                    return transcript.strip()
            except sr.UnknownValueError:
                log_live("Speech not understood, continuing to listen...")
                play_chime([523.25, 659.25], duration_ms=60, volume=0.08)
                continue
            except Exception as e:
                log_live(f"Recognition error: {e}")
                return None

        log_live(f"Listen timeout reached with no speech (peak_rms={peak_rms:.0f}).")
        play_chime([440.0, 349.23], duration_ms=70, volume=0.08)
        return None
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def get_latest_planner_response(transcript_path_str: str):
    if not transcript_path_str:
        return None, None
    tpath = Path(transcript_path_str)
    if not tpath.exists():
        return None, None

    try:
        lines = tpath.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None, None

    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx].strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") == "PLANNER_RESPONSE":
            content = (obj.get("content") or "").strip()
            truncated = obj.get("truncated_fields") or []
            if "content" in truncated:
                full_path = tpath.with_name("transcript_full.jsonl")
                if full_path.exists():
                    try:
                        full_lines = full_path.read_text(
                            encoding="utf-8", errors="replace"
                        ).splitlines()
                        if idx < len(full_lines):
                            full_obj = json.loads(full_lines[idx])
                            content = (full_obj.get("content") or content).strip()
                    except Exception:
                        pass
            if content:
                return obj.get("step_index", idx), content
    return None, None


def handle_pre_hook() -> None:
    raw = ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    cfg = load_config()
    target_session = cfg.get("session_id")
    if not target_session or payload.get("conversationId") == target_session:
        stop_playback()
    print("{}")


def handle_stop_hook() -> None:
    raw = ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    cfg = load_config()
    conv_id = payload.get("conversationId", "")
    target_session = cfg.get("session_id")
    if target_session and conv_id and conv_id != target_session:
        print(json.dumps({"decision": "stop"}))
        return

    term_reason = payload.get("terminationReason", "")
    if not cfg.get("enabled", True) or term_reason != "NO_TOOL_CALL":
        print(json.dumps({"decision": "stop"}))
        return

    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except Exception:
        print(json.dumps({"decision": "stop"}))
        return

    try:
        tpath = payload.get("transcriptPath", "")
        step_idx, content = get_latest_planner_response(tpath)

        if cfg.get("live_chat", True):
            if content:
                last_state = {}
                if STATE_FILE.exists():
                    try:
                        last_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                    except Exception:
                        last_state = {}
                if last_state.get(conv_id) != step_idx:
                    last_state[conv_id] = step_idx
                    try:
                        STATE_FILE.write_text(
                            json.dumps(last_state), encoding="utf-8"
                        )
                    except Exception:
                        pass
                    speak_sync(content, cfg)

            transcript = listen_and_transcribe(cfg)
            if not transcript:
                print(json.dumps({"decision": "stop"}))
                return

            lower_t = transcript.lower().strip()
            if any(phrase in lower_t for phrase in STOP_PHRASES):
                speak_sync("Okay, pausing live voice chat.", cfg)
                print(json.dumps({"decision": "stop"}))
                return

            print(
                json.dumps(
                    {
                        "decision": "continue",
                        "reason": (
                            f'[LIVE VOICE CHAT] The user just spoke to you via microphone: "{transcript}"\n'
                            "Reply naturally and concisely (1-3 short sentences unless they ask for code or details) "
                            "so your spoken reply sounds like a real-time conversation."
                        ),
                    }
                )
            )
            return

        if content:
            last_state = {}
            if STATE_FILE.exists():
                try:
                    last_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                except Exception:
                    last_state = {}
            key = f"{conv_id}:{step_idx}"
            if last_state.get("last_key") != key:
                try:
                    STATE_FILE.write_text(
                        json.dumps({"last_key": key}), encoding="utf-8"
                    )
                except Exception:
                    pass
                spawn_tts(content, cfg)

        print(json.dumps({"decision": "stop"}))
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Antigravity Voice Agent Utility")
    parser.add_argument(
        "--event",
        choices=["pre", "stop"],
        help="Internal hook event mode",
    )
    sub = parser.add_subparsers(dest="cmd")

    speak_p = sub.add_parser("speak", help="Speak text aloud")
    speak_p.add_argument("text", nargs="+", help="Text to speak")

    turn_p = sub.add_parser(
        "turn",
        help="Speak the given response aloud, then listen on the mic and print the user's transcribed reply",
    )
    turn_p.add_argument("text", nargs="+", help="Agent response text to speak before listening")

    sub.add_parser("listen", help="Test live microphone VAD + transcription once")
    sub.add_parser("live-on", help="Enable hands-free live voice chat loop")
    sub.add_parser("live-off", help="Disable hands-free live voice chat loop (TTS replies only)")
    sub.add_parser("stop", help="Stop current audio playback")
    sub.add_parser("mute", help="Disable all voice output and live chat")
    sub.add_parser("unmute", help="Enable voice output")
    sub.add_parser("status", help="Show voice agent status")

    voice_p = sub.add_parser("voice", help="Set neural TTS voice name")
    voice_p.add_argument("name", help="Voice name (e.g. en-US-AvaMultilingualNeural)")

    args = parser.parse_args()

    if args.event == "pre":
        handle_pre_hook()
        return
    if args.event == "stop":
        handle_stop_hook()
        return

    cfg = load_config()
    if args.cmd == "speak":
        spawn_tts(" ".join(args.text), cfg)
    elif args.cmd == "turn":
        speak_sync(" ".join(args.text), cfg)
        res = listen_and_transcribe(cfg)
        if res:
            print(f"USER_VOICE: {res}")
        else:
            print("USER_VOICE_TIMEOUT")
    elif args.cmd == "listen":
        res = listen_and_transcribe(cfg)
        print(f"Heard: {res!r}")
    elif args.cmd == "live-on":
        cfg["enabled"] = True
        cfg["live_chat"] = True
        save_config(cfg)
        print("Live voice chat enabled.")
    elif args.cmd == "live-off":
        cfg["live_chat"] = False
        save_config(cfg)
        print("Live voice chat disabled (TTS replies only).")
    elif args.cmd == "stop":
        stop_playback()
        print("Stopped active voice playback.")
    elif args.cmd == "mute":
        cfg["enabled"] = False
        save_config(cfg)
        stop_playback()
        print("Voice agent muted.")
    elif args.cmd == "unmute":
        cfg["enabled"] = True
        save_config(cfg)
        print("Voice agent unmuted.")
    elif args.cmd == "voice":
        cfg["voice"] = args.name
        save_config(cfg)
        print(f"Voice set to: {args.name}")
    else:
        print(json.dumps(cfg, indent=2))


if __name__ == "__main__":
    main()
