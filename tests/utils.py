import os
import shutil
import platform
from pathlib import Path


def nuke_dir(path: Path):
    if path.exists():
        # chmod all files to ensure they are deletable
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                try:
                    os.chmod(os.path.join(root, name), 0o777)
                except Exception:
                    pass
            for name in dirs:
                try:
                    os.chmod(os.path.join(root, name), 0o777)
                except Exception:
                    pass
        shutil.rmtree(path, ignore_errors=True)
        cmd = f'rmdir /s /q "{path}"' if platform.system() == "Windows" else f'rm -rf "{path}"'
        os.system(cmd)
