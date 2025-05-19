import sys
import os
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1].joinpath("src")))
sys.path.append(str(Path(__file__).resolve().parents[1]))

os.environ["WORKDIR"] = str(Path(__file__).resolve().parents[2])
TEST_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(TEST_DIR, "data")
TEMP_DIR = Path(DATA_DIR, "tmp")
CONFIG_DIR = Path(TEST_DIR, "configs")
