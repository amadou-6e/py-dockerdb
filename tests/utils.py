import os
import shutil
import platform
import docker
import time
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


def clear_port(port: int, container_prefix: str):
    client = docker.from_env()

    while True:
        containers = client.containers.list(all=True)  # include stopped/exited containers
        port_still_in_use = False

        for container in containers:
            container.reload()
            ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
            if f"{port}/tcp" in ports and ports[f"{port}/tcp"] is not None:
                port_still_in_use = True
                if container.name.startswith(container_prefix):
                    container.stop()

        if not port_still_in_use:
            break

        time.sleep(0.5)

    time.sleep(2)
