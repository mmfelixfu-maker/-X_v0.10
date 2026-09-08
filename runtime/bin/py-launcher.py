import subprocess
import sys

arguments = sys.argv[1:]
if arguments and arguments[0].startswith("-3."):
    arguments.pop(0)
raise SystemExit(subprocess.run([sys.executable, *arguments]).returncode)