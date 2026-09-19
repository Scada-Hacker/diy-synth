# 🎛️ DIY Synth

A TB-303 style acid box driven by Modulino sensors. Live sensor values stream into a Pure Data
patch over UDP: a 16-step sequencer synth with saw/square oscillators, a resonant filter with
per-step accent and slide, a kick/snare/hat drum machine, and a tempo-synced delay plus reverb
that each source can be sent to independently. A web UI monitors the connected devices and
configures the target address.

---

## Hardware required

- **Arduino UNO Q** — runs the sketch and the Python bridge side-by-side
- **Modulino Buttons** *(optional)* — 3 tactile buttons (mute snare / hat / synth)
- **Modulino Joystick** *(optional)* — analog stick with click; **click randomises the bass pattern**
- **Modulino Knob** *(optional)* — rotary encoder with push button
- **Modulino Distance** *(optional)* — ToF distance sensor (mm); transposes the whole pattern
- **Modulino Movement** *(optional)* — IMU; tilt X = filter cutoff, tilt Y = resonance, shake = fill

Any combination can be connected at once; the sketch auto-discovers all nodes on the I²C bus at startup.

---

## How it works

Three pieces talk to each other in a chain:

1. **Arduino sketch** (`sketch/sketch.ino`): scans the I²C bus on boot, classifies every Modulino node by address, and polls each one every 16 ms. On any state change it pushes a typed event to Python via `Bridge.notify` (`btn_event`, `joy_event`, `knob_event`, `dist_event`, `imu_event`).
2. **Python app** (`python/main.py`): receives MCU events via `Bridge.provide`, formats each one as a space-separated UDP text message, and sends it to the configured host and port. It also serves a Socket.IO web UI for live monitoring and configuration, and persists settings to `python/config.json`.
3. **Browser UI** (`ui/`): shows live values for every discovered device and lets you configure the UDP target (host, on/off).
4. **Pure Data patch** (`pd/`): `main.pd` hosts five graph-on-parent abstractions — `transport`, `drum`, `synth`, `fx`, `udp_io` — plus a master output section. Launch it with `./start-pd.sh`.

---

## Features

### UDP message format
Each event produces one **newline-terminated** text message sent to `[netreceive 7400 1]` in Pure
Data. The trailing newline is mandatory, not cosmetic — Pd's UDP receiver silently drops any
datagram that does not end in `\n`. The first word is the device type, the second is the decimal
I²C address, followed by the sensor values:

| Type | Format | Example | Patch does |
|------|--------|---------|------------|
| Buttons | `btn <addr> <b0> <b1> <b2>` | `btn 62 1 0 0` | mute snare / hat / synth |
| Joystick | `joy <addr> <nx> <ny> <pressed>` | `joy 44 0.500 -0.300 0` | click = randomise bass pattern |
| Knob | `knob <addr> <delta> <pressed>` | `knob 59 1 0` | tempo ±2 BPM / play-stop |
| Distance | `dist <addr> <mm>` | `dist 41 352.0` | transpose the pattern |
| Movement | `move <addr> <ax> <ay> <az>` | `move 106 1 -98 3` | cutoff / resonance / fill |

`move` values are accelerometer g **×100 as integers**, so ±1 g arrives as ±100.

### Pure Data integration
`pd/udp_io.pd` holds `[netreceive 7400 1]` (the `1` enables UDP mode) feeding
`[route btn knob dist move joy]`, which fans the sensors out onto named control buses that the
rest of the patch receives.

Audio travels on three `throw~`/`catch~` buses: the drums throw to **`drumbus`**, the synth to
**`synthbus`**, `fx.pd` catches both and throws dry plus wet to **`mix`**, and the master section
in `main.pd` catches `mix`.

### The 303 sequencer
`pd/synth.pd` is a 16-step monosynth in the TB-303 mould. Four editable rows, one cell per step,
stored in the tables `seq_note` / `seq_gate` / `seq_acc` / `seq_sld`:

| Row | Widget | Meaning |
|-----|--------|---------|
| **pitch** | number box | MIDI note for that step |
| **gate** | toggle | does the step sound |
| **accent** | toggle | louder hit + deeper filter sweep |
| **slide** | toggle | portamento into this step, and no filter-envelope retrigger (303 legato) |

The voice is `[phasor~]` shaped into a saw or a square, through `[vcf~]` whose centre frequency is
`cutoff + (filter envelope × env mod)`. Pitch glides in semitone space (`[line~]` → `[mtof~]`), so
slides are musically even rather than linear in Hz.

Panel controls: **wave** (saw / squ), **cutoff**, **reso**, **env mod**, **decay**, **accent**
amount, **level**, transpose **range**, and **dist limit**.

### Pattern randomiser
The **random** bang on the sequencer panel (or a click of the Modulino joystick) rewrites all
16 steps in one go. Notes are drawn from a minor-pentatonic table (`0 0 3 5 7 10 12 15`
semitones) above the **root** number box, so the result is always in key. Three probabilities
shape it: **density** (chance a step gates), **acc** and **sld**. It writes to the cell widgets
rather than straight to the tables, so the grid on screen always shows what is playing.

### The drum machine
`pd/drum.pd` is three 16-step rows — **kick**, **snare**, **hat** — stored in `pat_kick` /
`pat_snare` / `pat_hat`. The toggle at the left of each row mutes it.

| Voice | Synthesis |
|-------|-----------|
| **kick** | `osc~` with a `vline~` pitch drop 140 → 50 Hz in 45 ms, two-stage amp decay, plus a 12 ms high-passed noise click |
| **snare** | band-passed `noise~` + a 180 Hz `osc~` body, 150 ms decay |
| **hat** | `noise~` through `hip~ 7000`, 45 ms decay |

A shake on the Modulino Movement retriggers the snare as a 55 ms fill for 320 ms.

### Delay and reverb
`pd/fx.pd` sits between the two source buses and the master. Each effect has its own pair of
send toggles — **S** for the synth, **D** for the drums — so you can put the 303 in a long delay
while the drums stay dry, or reverb only the snare and hat. The dry path is always on.

| Control | Range |
|---------|-------|
| delay **div** | 16th / 8th / dotted 8th / quarter, locked to the transport tempo |
| delay **fb** | 0 – 0.85, repeats damped by `lop~ 4500` |
| delay **level**, reverb **level** | 0 – 1 wet amount |
| reverb **size** | 70 – 96 (`rev3~` liveness) |
| reverb **damp** | 0 – 100 % high-frequency damping |

Changing the tempo retunes the delay automatically.

### Sensor mapping

| Sensor | Controls |
|--------|----------|
| Distance | Transposes the **whole pattern** ±`range` semitones. Out of range → back to the root. |
| Movement tilt X | Filter cutoff, 200–6000 Hz |
| Movement tilt Y | Filter resonance, 1–25 |
| Movement shake | Snare fill (threshold in `g`, edge-triggered) |
| Knob turn / press | Tempo ±2 BPM / play-stop |
| Buttons 1-2-3 | Mute snare / hat / synth |
| Joystick click | Randomise the bass pattern |

The Modulino Buttons board only has three buttons, so the **kick mute and the FX send toggles
are mouse-only**.

Everything is also clickable with the mouse, so the patch is fully playable with no hardware attached.

### Live monitoring dashboard
The web UI shows a card for every discovered Modulino node with its current values updating in real time, so you can verify data is flowing before opening your PD patch.

### Configurable target
Host and the on/off toggle are editable in the UI and persisted to `python/config.json`; changes take effect immediately. The port is fixed — edit `python/config.json` (and `[netreceive]` in `pd/udp_io.pd`) and restart.

---

## Project structure

```
diy-synth/
├── app.yaml
├── start-pd.sh           # Launches Pd via JACK/PipeWire, auto-connects Bluetooth audio
├── pd/
│   ├── main.pd           # Host canvas + master output (catch~ mix -> dac~)
│   ├── transport.pd      # Tempo, play/stop, 16-step clock (tick / step buses)
│   ├── synth.pd          # 16-step 303: pitch/gate/accent/slide, saw+square, vcf~, randomiser
│   ├── drum.pd           # Kick + snare + hat step grids and voices
│   ├── fx.pd             # Tempo-synced delay + reverb, per-source send toggles
│   └── udp_io.pd         # netreceive 7400 -> route -> control buses
├── ui/
│   └── index.html        # Dashboard — device cards + UDP config (styles and JS inlined)
├── sketch/
│   ├── sketch.ino        # I²C discovery, 16 ms polling, Bridge.notify per event
│   └── sketch.yaml       # Platform and library versions
└── python/
    ├── main.py           # Bridge events → UDP messages + web UI state
    └── requirements.txt  # Dependencies (pre-installed by App Lab)
```

---

## Running the patch

```bash
./start-pd.sh            # auto-connects an already-paired Bluetooth speaker
./start-pd.sh --no-bt    # built-in audio
./start-pd.sh --bt MAC   # force a specific Bluetooth device
```

Then in Pd: tick **AUDIO ON**, tick **play / stop** in the TRANSPORT panel.

### Running on the UNO Q

Pd runs on the board's Linux side, next to the Python bridge:

    sudo apt install puredata

Install the `puredata` **metapackage**, not `puredata-core` on its own. Two things live in
`puredata-extra`, which only the metapackage pulls in: the control-rate `[expr]` boxes, and
`[rev3~]` — which the reverb is built on. `rev3~` is itself a plain Pd abstraction made of
core objects, so nothing exotic ends up in the DSP chain: `phasor~`, `noise~`, `osc~`, `vcf~`,
`bp~`, `hip~`, `lop~`, `vline~`, `line~`, `clip~`, `mtof~`, `delwrite~`/`delread4~`, and
`throw~`/`catch~`.

The synth is a single monophonic voice, the drums are three short bursts and the reverb is the
only real expense, so DSP load on the UNO Q's A53 cores stays low.

**Pointing the bridge at Pd:** if the Python app runs inside App Lab's container while Pd runs on
the host, set **Host** in the web UI to the Docker bridge address (`172.17.0.1`). Use `127.0.0.1`
only when both run in the same namespace.
