#!/bin/bash
# Start Pure Data with main.pd and auto-connect audio.
# Bluetooth A2DP speakers are preferred; falls back to built-in output.
#
# Connect your Bluetooth speaker via the system GUI before running this script.
# The already-connected device is picked up automatically — no keyboard needed.
#
# Optional flags:
#   ./start-pd.sh --no-bt    — skip BT, use built-in audio
#   ./start-pd.sh --bt MAC   — force a specific BT device by MAC address

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATCH_DIR="$SCRIPT_DIR/pd"

# ── helpers ────────────────────────────────────────────────────────────────
# Note: this image ships pipewire-jack (the pw-jack wrapper + PipeWire's own
# JACK server implementation) but not the jackd2 package, so the jack_lsp /
# jack_connect CLI binaries don't exist. pw-link is PipeWire's native
# port/link tool and reports the identical node/port graph (same names),
# so it's a drop-in replacement here — no extra package needed.
jack_lsp()    { pw-link -o 2>/dev/null; pw-link -i 2>/dev/null; }
jack_connect(){ pw-link "$1" "$2" 2>/dev/null; }

connect_pd() {
    local l="$1" r="$2"
    jack_connect "pure_data:output_1" "$l" &&
    jack_connect "pure_data:output_2" "$r"
}

# ── argument parsing ───────────────────────────────────────────────────────
FORCE_MAC=""
SKIP_BT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --no-bt)          SKIP_BT=1 ;;
        --bt)             shift; FORCE_MAC="$1" ;;
        *) ;;
    esac
    shift
done

# ── Bluetooth device selection ─────────────────────────────────────────────
# Users connect their BT device via the system GUI before running this script.
# We auto-pick the first device that is already connected; no keyboard needed.
SELECTED_MAC=""
SELECTED_NAME=""

if [ $SKIP_BT -eq 0 ]; then
    if [ -n "$FORCE_MAC" ]; then
        SELECTED_MAC="$FORCE_MAC"
    else
        # Find the first already-connected Bluetooth device
        while IFS= read -r line; do
            mac=$(echo "$line" | awk '{print $2}')
            if bluetoothctl info "$mac" 2>/dev/null | grep -q "Connected: yes"; then
                SELECTED_MAC="$mac"
                SELECTED_NAME=$(echo "$line" | cut -d' ' -f3-)
                break
            fi
        done < <(bluetoothctl devices 2>/dev/null | grep "^Device ")
    fi
fi

# ── connect BT device if selected ─────────────────────────────────────────
if [ -n "$SELECTED_MAC" ]; then
    echo "Connecting to ${SELECTED_NAME:-$SELECTED_MAC}..."
    bluetoothctl connect "$SELECTED_MAC" 2>/dev/null
    sleep 3   # give PipeWire time to expose the A2DP sink as a JACK node
fi

# ── launch PD ─────────────────────────────────────────────────────────────
# Kill any pd instance already running this patch first — otherwise PipeWire's
# JACK layer renames the new client (e.g. "pure_data-1") on a name clash,
# and the hardcoded "pure_data:output_*" lookups below silently match nothing.
if pkill -f "jackname pure_data"; then
    sleep 1
fi

echo "Starting Pure Data..."
# -path makes PD search the pd/ folder for abstractions (drum, synth, transport, udp_io).
# Pd defaults to the ALSA audio API on Linux (see `pd --help`) — pw-jack alone only
# makes the JACK client library available, it does NOT change Pd's own audio-backend
# choice. Without -jack, Pd never opens a JACK client at all, so it never shows up as
# "pure_data:output_*" in the PipeWire graph and nothing below can find or wire it.
# -jackname pins the client name so connect_pd()'s hardcoded lookups always match.
# -nojackconnect disables Pd's own auto-connect so our BT-aware logic below is authoritative.
pw-jack pd -jack -jackname pure_data -nojackconnect -path "$PATCH_DIR" "$PATCH_DIR/main.pd" &
PD_PID=$!

# ── wait for PD JACK ports (up to 10 s) ───────────────────────────────────
for i in $(seq 1 20); do
    sleep 0.5
    jack_lsp | grep -q "pure_data:output_1" && break
done

# ── connect audio output ───────────────────────────────────────────────────
PORTS=$(jack_lsp)
CONNECTED=0

# 1. Try Bluetooth first (PipeWire exposes BT A2DP nodes as bluez_* JACK ports)
if [ -n "$SELECTED_MAC" ] || jack_lsp | grep -qi "bluez"; then
    BT_L=$(echo "$PORTS" | grep -i "bluez" | grep -Ei "playback_FL|playback_1$" | head -1 | tr -d ' ')
    BT_R=$(echo "$PORTS" | grep -i "bluez" | grep -Ei "playback_FR|playback_2$" | head -1 | tr -d ' ')
    if [ -n "$BT_L" ] && [ -n "$BT_R" ] && connect_pd "$BT_L" "$BT_R"; then
        echo "✓ Audio → Bluetooth: $BT_L / $BT_R"
        CONNECTED=1
    fi
fi

# 2. Fall back to any available playback output (built-in headphones, HDMI, etc.)
if [ "$CONNECTED" -eq 0 ]; then
    FB_L=$(echo "$PORTS" | grep -Ei "playback_FL|playback FL" | head -1 | tr -d ' ')
    FB_R=$(echo "$PORTS" | grep -Ei "playback_FR|playback FR" | head -1 | tr -d ' ')
    if [ -n "$FB_L" ] && [ -n "$FB_R" ] && connect_pd "$FB_L" "$FB_R"; then
        echo "✓ Audio → built-in: $FB_L / $FB_R"
        CONNECTED=1
    fi
fi

# 3. If still not connected, print available ports so the user can connect manually
if [ "$CONNECTED" -eq 0 ]; then
    echo "⚠ Could not auto-connect audio. Connect manually:"
    echo "  pw-link pure_data:output_1 <sink>:playback_FL"
    echo "  pw-link pure_data:output_2 <sink>:playback_FR"
    echo ""
    echo "Available playback ports:"
    echo "$PORTS" | grep -i "playback" | sed 's/^/  /'
fi

wait $PD_PID
