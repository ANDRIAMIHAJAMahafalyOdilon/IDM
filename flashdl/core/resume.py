import json
import os


def state_path(dest):
    return dest + ".flashdl"


def part_path(dest, i):
    return f"{dest}.part{i}"


def save_state(dest, state):
    tmp = state_path(dest) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, state_path(dest))


def load_state(dest):
    try:
        with open(state_path(dest), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def clear_state(dest):
    try:
        os.remove(state_path(dest))
    except OSError:
        pass


def remove_parts(dest):
    folder = os.path.dirname(dest) or "."
    prefix = os.path.basename(dest) + ".part"
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        # The destination directory may have been moved/deleted since the
        # download was recorded. Missing partial data is already cleaned up.
        return
    for name in names:
        if name.startswith(prefix):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
