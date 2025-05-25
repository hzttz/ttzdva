@echo off
call chcp 65001
set PYTHON_SCRIPT=main_tts.py
set CONDA_PATH = D:\lapp\conda
where conda >nul 2>&1
if %ERRORLEVEL% neq 0 (
    if exist "%CONDA_PATH%\Scripts\conda.exe" (
        set "PATH=%CONDA_PATH%;%CONDA_PATH%\Scripts;%CONDA_PATH%\Library\bin;%PATH%"
    ) else (
        echo Conda Unfound，please set conda_path or install Anaconda/Miniconda
        pause
        exit /b 1
    )
)
call conda activate base
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Python Unfound,please comfirm the device installed python and added to PATH
    pause
    exit /b 1
)
python main_tts.py
pause
