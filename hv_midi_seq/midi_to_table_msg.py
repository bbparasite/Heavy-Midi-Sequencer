#!/usr/bin/env python3
"""
midi_to_table_msg.py
Extracts pitch, velocity, and duration (ms) from a .mid file and writes
a single combined init message file for PlugData.

Handles POLYPHONY by assigning notes to separate voices (max 3 by default).
All voices share 9 fixed tables:

  seq_pitch_0  seq_vel_0  seq_dur_ms_0
  seq_pitch_1  seq_vel_1  seq_dur_ms_1
  seq_pitch_2  seq_vel_2  seq_dur_ms_2

The output is one .txt file containing a single message box that:
  - resizes all 9 tables
  - sets bounds, xlabel, ylabel on all 9 tables
  - loads all data into all 9 tables
  - updates seq_size (for use with [mod] in your patch)

To switch songs, just trigger a different song's message box.

Usage:
    pip install mido
    python3 midi_to_table_msg.py mysong.mid
    python3 midi_to_table_msg.py mysong.mid --max-voices 3
"""

import sys
import argparse
import mido


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

def build_tempo_map(mid):
    tmap = [(0, 500000)]
    abs_tick = 0
    for msg in mido.merge_tracks(mid.tracks):
        abs_tick += msg.time
        if msg.type == "set_tempo":
            tmap.append((abs_tick, msg.tempo))
    return tmap


def abs_ticks_to_ms(abs_tick, ppq, tempo_map):
    ms = 0.0
    prev_tick, prev_tempo = tempo_map[0]
    for seg_tick, seg_tempo in tempo_map[1:]:
        if abs_tick <= seg_tick:
            break
        ms += (seg_tick - prev_tick) * (prev_tempo / ppq / 1000.0)
        prev_tick, prev_tempo = seg_tick, seg_tempo
    ms += (abs_tick - prev_tick) * (prev_tempo / ppq / 1000.0)
    return ms


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract(midi_path):
    mid = mido.MidiFile(midi_path)
    ppq = mid.ticks_per_beat
    tempo_map = build_tempo_map(mid)
    current_tempo = 500000

    active = {}
    notes  = []
    abs_tick = 0

    for msg in mido.merge_tracks(mid.tracks):
        abs_tick += msg.time

        if msg.type == "set_tempo":
            current_tempo = msg.tempo

        elif msg.type == "note_on" and msg.velocity > 0:
            active[(msg.note, msg.channel)] = (abs_tick, msg.velocity)

        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            key = (msg.note, msg.channel)
            if key in active:
                on_tick, vel = active.pop(key)
                onset_ms  = round(abs_ticks_to_ms(on_tick,  ppq, tempo_map), 3)
                offset_ms = round(abs_ticks_to_ms(abs_tick, ppq, tempo_map), 3)
                dur_ms    = round(offset_ms - onset_ms, 3)
                notes.append((msg.note, vel, dur_ms, onset_ms))

    notes.sort(key=lambda n: (n[3], n[0]))
    return notes, ppq


# ---------------------------------------------------------------------------
# Voice assignment
# ---------------------------------------------------------------------------

def assign_voices(notes, max_voices):
    voices = []
    end_ms = []

    for pitch, vel, dur_ms, onset_ms in notes:
        placed = False
        for i in range(len(voices)):
            if onset_ms >= end_ms[i]:
                voices[i].append((pitch, vel, dur_ms))
                end_ms[i] = onset_ms + dur_ms
                placed = True
                break

        if not placed:
            if max_voices and len(voices) >= max_voices:
                i = end_ms.index(min(end_ms))
                voices[i].append((pitch, vel, dur_ms))
                end_ms[i] = onset_ms + dur_ms
            else:
                voices.append([(pitch, vel, dur_ms)])
                end_ms.append(onset_ms + dur_ms)

    max_len = max(len(v) for v in voices)

    # Pad shorter voices and fill missing voices with all rests
    for v in voices:
        while len(v) < max_len:
            v.append((0, 0, 0.0))

    # If fewer voices than max_voices, pad with silent voice tables
    while max_voices and len(voices) < max_voices:
        voices.append([(0, 0, 0.0)] * max_len)

    return voices


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt(v):
    s = f"{float(v):.3f}".rstrip("0").rstrip(".")
    return s if s else "0"


def xlabel_marks(n, max_marks=20):
    if n <= max_marks:
        return list(range(n))
    step = max(1, n // max_marks)
    marks = list(range(0, n, step))
    if marks[-1] != n - 1:
        marks.append(n - 1)
    return marks


def table_lines(table_name, values, y_min, y_max):
    """Return a list of semicolon-terminated Pd message strings for one table."""
    n       = len(values)
    bounds  = f"0 {fmt(y_max * 1.1)} {n} {fmt(max(0, y_min - y_max * 0.1))}"
    x_marks = xlabel_marks(n)
    xlabel  = f"-0.5 {' '.join(str(i) for i in x_marks)}"
    num_y   = 6
    y_step  = (y_max - y_min) / max(num_y - 1, 1)
    y_marks = [fmt(y_min + i * y_step) for i in range(num_y)]
    ylabel  = f"-0.05 {' '.join(y_marks)}"
    data    = "0 " + " ".join(fmt(v) for v in values)

    return [
        f"; {table_name} resize {n}",
        f"; {table_name} bounds {bounds}",
        f"; {table_name} xlabel {xlabel}",
        f"; {table_name} ylabel {ylabel}",
        f"; {table_name} {data}",
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Convert MIDI to a single PlugData table init message")
    ap.add_argument("midi_file",              help="Input .mid file")
    ap.add_argument("--max-voices", type=int, default=3,
                    help="Number of voices/table sets (default: 3)")
    args = ap.parse_args()

    midi_path  = args.midi_file
    max_voices = args.max_voices
    base       = midi_path.rsplit(".", 1)[0]

    notes, ppq = extract(midi_path)

    if not notes:
        print("No notes found.")
        sys.exit(1)

    voices  = assign_voices(notes, max_voices)
    nv      = len(voices)
    n       = len(voices[0])
    max_dur = max(note[2] for note in notes)

    # Metro interval from the first tempo event in the file
    mid_file = mido.MidiFile(midi_path)
    first_tempo = 500000
    for msg in mido.merge_tracks(mid_file.tracks):
        if msg.type == "set_tempo":
            first_tempo = msg.tempo
            break
    metro_ms = round(first_tempo / 1000.0, 3)  # microseconds per beat -> ms per beat
    bpm      = round(60_000_000 / first_tempo, 2)

    print(f"\nNotes found   : {len(notes)}")
    print(f"PPQ           : {ppq}")
    print(f"BPM           : {bpm}")
    print(f"Metro interval: {metro_ms} ms")
    print(f"Voices        : {nv}")
    print(f"Table size    : {n} slots per voice")
    print(f"Use [mod {n}] in your sequencer counter\n")

    # Build one combined message: seq_size + seq_metro + all 9 tables
    all_lines = []

    # seq_size  -> [tabread seq_size]  -> [mod] right inlet
    # seq_metro -> [tabread seq_metro] -> [metro] right inlet
    all_lines.append(f"; seq_size 0 {n}")
    all_lines.append(f"; seq_metro 0 {metro_ms}")

    for vi, voice in enumerate(voices):
        pitches = [note[0] for note in voice]
        vels    = [note[1] for note in voice]
        durs    = [note[2] for note in voice]

        all_lines += table_lines(f"seq_pitch_{vi}",  pitches, 0,     127)
        all_lines += table_lines(f"seq_vel_{vi}",    vels,    0,     127)
        all_lines += table_lines(f"seq_dur_ms_{vi}", durs,    0, max_dur)

    # Join as a single Pd message box (backslash-continuation between statements)
    combined = " \\\n".join(all_lines)

    out_path = base + "_init_msg.txt"
    with open(out_path, "w") as f:
        f.write(combined + "\n")

    print(f"  wrote: {out_path}")
    print()
    print("To use in PlugData:")
    print(f"  1. Create 9 tables ({nv} voices x 3 tables):")
    for vi in range(nv):
        print(f"       [table seq_pitch_{vi}]  [table seq_vel_{vi}]  [table seq_dur_ms_{vi}]")
    print(f"  2. Create [table seq_size 1] and [table seq_metro 1]")
    print(f"  3. Paste {out_path} into a message box, connect [loadbang] to it")
    print(f"  4. On song load, read both control tables:")
    print(f"       [tabread seq_size]  -> [mod] right inlet")
    print(f"       [tabread seq_metro] -> [metro] right inlet")
    print(f"  5. Repeat steps 3-4 for each song with its own message box")
    print(f"  6. One button per song triggers its message box to hot-swap everything")
    print()


if __name__ == "__main__":
    main()
