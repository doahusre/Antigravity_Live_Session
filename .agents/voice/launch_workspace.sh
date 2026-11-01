#!/usr/bin/env bash
# Antigravity Live Workspace Launcher & Restorer
# Restores or attaches to the exact 3-pane Antigravity Live workspace in tmux:
#   - Top-Left (Pane 1):  Antigravity Lead Agent CLI (agy)
#   - Top-Right (Pane 2): Antigravity Live Brain & HUD (antigravity_hud.py)
#   - Bottom (Pane 3):    Gemini Live Full-Duplex Voice Assistant (gemini-live)
#   - Background Daemon:  Voice Watchdog (watchdog.py)

set -e

SESSION="iggnita"
WINDOW="agy-hub"
WORKDIR="/home/doa/Projects"
VENV_PY="/home/doa/Projects/.agents/voice/.venv/bin/python3"
HUD_SCRIPT="/home/doa/Projects/.agents/voice/antigravity_hud.py"
WATCHDOG_SCRIPT="/home/doa/Projects/.agents/voice/watchdog.py"

# 1. Ensure background watchdog daemon is running
if ! pgrep -f "$WATCHDOG_SCRIPT" >/dev/null 2>&1; then
    nohup "$VENV_PY" "$WATCHDOG_SCRIPT" >/dev/null 2>&1 &
fi

# 2. Check if tmux session 'iggnita' exists
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux new-session -d -s "$SESSION" -n "home-shell" -c "/home/doa"
fi

# 3. Check if window 'agy-hub' exists; if not, build the exact 3-pane layout
if ! tmux list-windows -t "$SESSION" -F '#{window_name}' | grep -qx "$WINDOW"; then
    # Create window with Top-Left pane (Pane 1)
    tmux new-window -t "$SESSION" -n "$WINDOW" -c "$WORKDIR"
    # Split vertically to create Bottom Full-Width pane (Pane 2 initially, becomes 3 after top split)
    tmux split-window -v -p 43 -t "$SESSION:$WINDOW.1" -c "$WORKDIR"
    # Split Top pane horizontally to create Top-Right HUD pane (Pane 2)
    tmux split-window -h -p 50 -t "$SESSION:$WINDOW.1" -c "$WORKDIR"

    # Launch commands in each pane:
    # Pane 1 (Top-Left): Antigravity CLI
    tmux send-keys -t "$SESSION:$WINDOW.1" "agy" C-m
    # Pane 2 (Top-Right): Antigravity Live Brain & HUD
    tmux send-keys -t "$SESSION:$WINDOW.2" "$VENV_PY $HUD_SCRIPT" C-m
    # Pane 3 (Bottom): Gemini Live Voice Assistant
    tmux send-keys -t "$SESSION:$WINDOW.3" "gemini-live" C-m
    tmux select-pane -t "$SESSION:$WINDOW.3"
else
    # Window exists — ensure all 3 panes are present and running their apps
    PANE_COUNT=$(tmux list-panes -t "$SESSION:$WINDOW" | wc -l)
    if [ "$PANE_COUNT" -lt 3 ]; then
        # Rebuild missing panes cleanly if needed
        if [ "$PANE_COUNT" -eq 1 ]; then
            tmux split-window -v -p 43 -t "$SESSION:$WINDOW.1" -c "$WORKDIR"
            tmux split-window -h -p 50 -t "$SESSION:$WINDOW.1" -c "$WORKDIR"
            tmux send-keys -t "$SESSION:$WINDOW.2" "$VENV_PY $HUD_SCRIPT" C-m
            tmux send-keys -t "$SESSION:$WINDOW.3" "gemini-live" C-m
        elif [ "$PANE_COUNT" -eq 2 ]; then
            tmux split-window -h -p 50 -t "$SESSION:$WINDOW.1" -c "$WORKDIR"
            tmux send-keys -t "$SESSION:$WINDOW.2" "$VENV_PY $HUD_SCRIPT" C-m
        fi
    fi
fi

# 4. Select the agy-hub window and attach (or switch if already inside tmux)
tmux select-window -t "$SESSION:$WINDOW"
if [ -n "$TMUX" ]; then
    tmux switch-client -t "$SESSION:$WINDOW"
else
    exec tmux attach-session -t "$SESSION:$WINDOW"
fi
