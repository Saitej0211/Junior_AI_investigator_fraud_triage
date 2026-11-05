"""Run the notebook's code without Jupyter and keep its web app serving at http://localhost:8000.

Usage: python run_app.py   (Ctrl+C to stop)
"""
import json
import os
import threading
from pathlib import Path

os.chdir(Path(__file__).parent)  # the notebook reads data/, web/ and .env from the project folder
for cell in json.loads(Path("fraud_triage.ipynb").read_text())["cells"]:
    if cell["cell_type"] == "code":
        exec("".join(cell["source"]), globals())
try:
    threading.Event().wait()
except KeyboardInterrupt:
    print("Stopped")
