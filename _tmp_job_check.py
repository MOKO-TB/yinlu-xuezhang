"""真机验一遍 Job Object:关句柄之后,孙子进程是不是真的没了。"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from minicode import win_sandbox as w

CHILD = ("import subprocess, sys, time, os;"
         "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']);"
         "print(os.getpid(), g.pid, flush=True);"
         "time.sleep(120)")

p = subprocess.Popen([sys.executable, "-c", CHILD], stdout=subprocess.PIPE,
                     text=True, creationflags=(subprocess.CREATE_NO_WINDOW
                                               if sys.platform == "win32" else 0))
job = w.Job.create()
print("job created:", job is not None, "| err:", w.Job.last_error)
print("attach:", job.attach(p) if job else None, "| err:", w.Job.last_error)
first, second = p.stdout.readline().split()
print("child pid", first, "grandchild pid", second)
job.close()
time.sleep(1.5)


def alive(pid):
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True)
    return f'"{pid}"' in r.stdout


print("after close -> child alive:", alive(first), "grandchild alive:", alive(second))
try:
    p.wait(timeout=10)
except Exception:
    p.kill()
print("child returncode:", p.returncode)
print("PE 断言:", "两个都该是 False")
