import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from organizer.integration_check import run_local_check
with tempfile.TemporaryDirectory() as folder:
    print(run_local_check(Path(folder)))
