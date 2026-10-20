@echo off
rem 双击启动（需要已安装 Python 与 requirements.txt 依赖）
cd /d "%~dp0"
python -m PyInstaller duanju.spec

