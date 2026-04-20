"""
Built-in plugin: system resource usage (CPU, RAM, disk).
Usage: PLUGIN: sysinfo
"""
import shutil
import subprocess

PLUGIN_NAME = "sysinfo"
PLUGIN_DESCRIPTION = "Get current CPU load, RAM usage, and disk space. No args needed."


def execute(args: str) -> str:
    lines = []

    # RAM (macOS vm_stat)
    try:
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3)
        if vm.returncode == 0:
            vm_lines = {
                l.split(":")[0].strip(): l.split(":")[1].strip().rstrip(".")
                for l in vm.stdout.splitlines()
                if ":" in l
            }
            page_size = 16384  # 16 KB on Apple Silicon
            free = int(vm_lines.get("Pages free", "0")) * page_size
            inactive = int(vm_lines.get("Pages inactive", "0")) * page_size
            wired = int(vm_lines.get("Pages wired down", "0")) * page_size
            active = int(vm_lines.get("Pages active", "0")) * page_size
            total = free + inactive + wired + active
            used = wired + active
            lines.append(
                f"RAM: {used / 1024**3:.1f} GB used / {total / 1024**3:.1f} GB total"
            )
    except Exception:
        pass

    # CPU (sysctl)
    try:
        load = subprocess.run(
            ["sysctl", "-n", "vm.loadavg"], capture_output=True, text=True, timeout=3
        )
        if load.returncode == 0:
            lines.append(f"Load avg: {load.stdout.strip()}")
    except Exception:
        pass

    # Disk
    try:
        total, used, free = shutil.disk_usage("/")
        lines.append(
            f"Disk (/): {used / 1024**3:.1f} GB used / {total / 1024**3:.1f} GB total "
            f"({free / 1024**3:.1f} GB free)"
        )
    except Exception:
        pass

    return "\n".join(lines) if lines else "Could not retrieve system info."
