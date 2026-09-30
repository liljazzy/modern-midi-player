@echo off
cd /d "%~dp0"
python -c "import PySide6" 2>NUL || python -m pip install -r requirements.txt
start "" pythonw run.py %*
