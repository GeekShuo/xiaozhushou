# -*- coding: utf-8 -*-
import subprocess, sys, os

env = dict(os.environ)
env["LIFECOACH_LLM_API_KEY"] = "sk-8c21e873954d4ff5bcec1cf15253e547"
env["PYTHONIOENCODING"] = "utf-8"

with open("server2.log", "w", encoding="utf-8") as log:
    subprocess.Popen(
        ["python", "-X", "utf8", "src/main.py", "--port", "61373"],
        cwd=r"d:\project\xiaozhushou",
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=0x00000200,  # DETACHED_PROCESS
    )
print("LAUNCHED")
