"""One-time safe settings filename migration for previous numbered installs."""
from pathlib import Path
import os
import tempfile

def migrate_settings(settings_dir):
    folder = Path(settings_dir)
    old = folder / "1OmniMovement.json"
    new = folder / "OmniMovement.json"
    if new.exists() or not old.is_file():
        return
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".OmniMovement.", suffix=".tmp", delete=False) as stream:
            temp = Path(stream.name)
            stream.write(old.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        if not new.exists():
            os.replace(temp, new)
            temp = None
    except OSError:
        pass
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
