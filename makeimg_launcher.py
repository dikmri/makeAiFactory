import sys
from pathlib import Path

# Add src directory to Python path
src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from makeimg.app import run_app

if __name__ == "__main__":
    sys.exit(run_app())
