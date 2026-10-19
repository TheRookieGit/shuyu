import tempfile
from pathlib import Path

# The app resolves its data directory; give tests the same long, symlink-free temp path
# (macOS /var -> /private/var, Windows 8.3 names such as RUNNER~1).
tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve())
