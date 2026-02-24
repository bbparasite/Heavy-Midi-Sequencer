#!/usr/bin/env python3
"""
midi_to_table_msg.py
Extracts pitch, velocity, duration (ms), and absolute onset time (ms)
from a .mid file and writes 4 text files containing ready-to-paste PlugData
init messages for table objects.

Each file contains a single message that:
  - resizes the table to fit the data
  - sets display bounds
  - adds xlabel and ylabel tick marks
  - loads all values in one shot

The onset table (seq_onset_ms) stores the absolute time in ms from the
start of the file to each note-on event, fully tempo-map resolved.
Use it to drive a [timer]-based or elapsed-ms sequencer in PlugData.

Usage:
    pip install mido
    python3 midi_to_table_msg.py mysong.mid
"""

import sys
import mido


def ticks_to_ms(ticks, tempo, ppq):
    return (ticks * tempo) / (ppq * 1000.0)


def build_tempo_map(mid):
    """List of (abs_tick, tempo_us) covering every tempo change."""
    tmap = [(0, 500000)]
    abs_tick = 0
    for msg in mido.merge_tracks(mid.tracks):
        abs_tick += msg.time
        if msg.type == "set_tempo":
            tmap.append((abs_tick, msg.tempo))
    return tmap


def abs_ticks_to_ms(abs_tick, ppq, tempo_map):
    """Resolve absolute tick to ms using the full tempo map."""
    ms = 0.0
    prev_tick, prev_tempo = tempo_map[0]
    for seg_tick, seg_tempo in tempo_map[1:]:
        if abs_tick <= seg_tick:
            break
        ms += (seg_tick - prev_tick) * (prev_tempo / ppq / 1000.0)
        prev_tick, prev_tempo = seg_tick, seg_tempo
    ms += (abs_tick - prev_tick) * (prev_tempo / ppq / 1000.0)
    return ms


def extract(midi_path):
    mid = mido.MidiFile(midi_path)
    ppq = mid.ticks_per_beat
    tempo_map = build_tempo_map(mid)
    current_tempo = 500000

    active = {}   # (pitch, ch) -> (abs_tick, vel, tempo_at_noteon)
    notes = []    # (pitch, vel, dur_ms, onset_ms)
    abs_tick = 0

    for msg in mido.merge_tracks(mid.tracks):
        abs_tick += msg.time

        if msg.type == "set_tempo":
            current_tempo = msg.tempo

        elif msg.type == "note_on" and msg.velocity > 0:
            active[(msg.note, msg.channel)] = (abs_tick, msg.velocity, current_tempo)

        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            key = (msg.note, msg.channel)
            if key in active:
                on_tick, vel, note_tempo = active.pop(key)
                off_tick  = abs_tick
                onset_ms  = round(abs_ticks_to_ms(on_tick,  ppq, tempo_map), 3)
                offset_ms = round(abs_ticks_to_ms(off_tick, ppq, tempo_map), 3)
                dur_ms    = round(offset_ms - onset_ms, 3)
                notes.append((msg.note, vel, dur_ms, onset_ms))

    # Sort by onset so the table is in chronological order
    notes.sort(key=lambda n: n[3])
    return notes, ppq


def fmt(v):
    """Format float cleanly — strip trailing zeros."""
    s = f"{float(v):.3f}".rstrip("0").rstrip(".")
    return s if s else "0"


def xlabel_marks(n, max_marks=20):
    """
    Return a list of index positions to use as xlabel ticks.
    Keeps it readable by capping at max_marks evenly spaced marks.
    """
    if n <= max_marks:
        return list(range(n))
    step = max(1, n // max_marks)
    marks = list(range(0, n, step))
    if marks[-1] != n - 1:
        marks.append(n - 1)
    return marks


def build_message(table_name, values, y_min, y_max, y_label):
    """
    Build a single PlugData/Pd init message string for a table.

    Format (all on one line in the message box):
      ; tablename resize N
      ; tablename bounds xmin ymax xmax ymin   <- note: ymax before ymin (Pd convention)
      ; tablename xlabel offset idx0 idx1 ...
      ; tablename ylabel offset val0 val1 ...
      ; tablename 0 v0 v1 v2 ...
    """
    n = len(values)

    # bounds: xmin ymax xmax ymin  (Pd puts ymax first, ymin last)
    bounds = f"0 {fmt(y_max * 1.1)} {n} {fmt(y_min - y_max * 0.1)}"

    # xlabel: offset then indices
    x_marks = xlabel_marks(n)
    xlabel = f"-0.5 {' '.join(str(i) for i in x_marks)}"

    # ylabel: offset then values evenly spaced between y_min and y_max
    num_y = 6
    y_step = (y_max - y_min) / max(num_y - 1, 1)
    y_marks = [fmt(y_min + i * y_step) for i in range(num_y)]
    ylabel = f"-0.05 {' '.join(y_marks)}"

    # data: starting index 0 followed by all values
    data = "0 " + " ".join(fmt(v) for v in values)

    lines = [
        f"; {table_name} resize {n}",
        f"; {table_name} bounds {bounds}",
        f"; {table_name} xlabel {xlabel}",
        f"; {table_name} ylabel {ylabel}",
        f"; {table_name} {data}",
    ]

    return " \\\n".join(lines)


def write_msg_file(path, table_name, values, y_min, y_max, y_label):
    msg = build_message(table_name, values, y_min, y_max, y_label)
    with open(path, "w") as f:
        f.write(msg + "\n")
    print(f"  wrote: {path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 midi_to_table_msg.py mysong.mid")
        sys.exit(1)

    midi_path = sys.argv[1]
    base = midi_path.rsplit(".", 1)[0]
    notes, ppq = extract(midi_path)

    if not notes:
        print("No notes found.")
        sys.exit(1)

    pitches  = [n[0] for n in notes]
    vels     = [n[1] for n in notes]
    durs     = [n[2] for n in notes]
    onsets   = [n[3] for n in notes]

    n = len(notes)
    total_ms = max(onsets) + max(durs) if onsets else 0
    print(f"\nNotes found  : {n}")
    print(f"PPQ          : {ppq}")
    print(f"First onset  : {min(onsets):.3f} ms")
    print(f"Last onset   : {max(onsets):.3f} ms")
    print(f"Total length : {total_ms:.3f} ms")
    print(f"Use [mod {n}] in your metro counter\n")

    write_msg_file(
        base + "_pitch_msg.txt",
        "seq_pitch", pitches,
        y_min=0, y_max=127,
        y_label="MIDI note"
    )

    write_msg_file(
        base + "_vel_msg.txt",
        "seq_vel", vels,
        y_min=0, y_max=127,
        y_label="velocity"
    )

    write_msg_file(
        base + "_dur_msg.txt",
        "seq_dur_ms", durs,
        y_min=0, y_max=max(durs) if durs else 1000,
        y_label="ms"
    )

    write_msg_file(
        base + "_onset_msg.txt",
        "seq_onset_ms", onsets,
        y_min=0, y_max=max(onsets) if onsets else 1000,
        y_label="ms"
    )

    print(f"""
To use in PlugData:
  1. Create tables: [table seq_pitch]    [table seq_vel]
                   [table seq_dur_ms]   [table seq_onset_ms]
  2. Paste each _msg.txt into a message box, connect [loadbang] to it
  3. Use [mod {n}] in your sequencer counter

Onset-based sequencer wiring:
  Use [timer] or [elapsed] to track ms since playback started.
  On each metro tick compare elapsed ms against seq_onset_ms[i]
  to fire notes at the correct absolute time regardless of tempo changes.
""")


if __name__ == "__main__":
    main()
