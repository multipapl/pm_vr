@echo off
rem Run the PM VR Blender tests.
rem   run_tests.bat        background tests (tests\*_smoke.py)
rem   run_tests.bat gui    also the interactive tests (they open Blender windows briefly)
rem   run_tests.bat gui-only   interactive tests only
rem Set BLENDER to use another Blender executable.
setlocal
if "%BLENDER%"=="" set "BLENDER=C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"
set "LOGDIR=%TEMP%\pmvr_test_logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set FAILED=0

if /I "%~1"=="gui-only" goto :gui_tests
for %%T in ("%~dp0tests\*_smoke.py") do call :run "%%~fT" background
if /I not "%~1"=="gui" goto :results
:gui_tests
    call :run "%~dp0tests\blender_gui_authoring.py" simulate
    call :run "%~dp0tests\blender_gui_authoring_tools.py" simulate
    call :run "%~dp0tests\blender_gui_original_list.py" simulate
    call :run "%~dp0tests\blender_gui_property_selection.py" simulate
    call :run "%~dp0tests\blender_gui_bake_cancel.py" simulate esc
    call :run "%~dp0tests\blender_gui_bake_cancel.py" simulate button
    call :run "%~dp0tests\blender_gui_selection_sync.py" window
    call :run "%~dp0tests\blender_gui_duplicate_undo.py" window
    call :run "%~dp0tests\blender_gui_bake_scenarios.py" simulate finish
    call :run "%~dp0tests\blender_gui_bake_scenarios.py" simulate esc
    call :run "%~dp0tests\blender_gui_unit_list.py" window
    call :run "%~dp0tests\blender_gui_bake_fail_start.py" simulate
    call :run "%~dp0tests\blender_gui_bake_resume.py" simulate
    call :run "%~dp0tests\blender_gui_bake_autosave.py" simulate
    call :run "%~dp0tests\blender_gui_image_editor.py" window
    call :run "%~dp0tests\blender_gui_help.py" simulate
    call :run "%~dp0tests\blender_gui_lighting_looks.py" simulate
    call :run "%~dp0tests\blender_gui_probes.py" simulate finish
    call :run "%~dp0tests\blender_gui_probes.py" simulate button
    call :run "%~dp0tests\blender_gui_probes.py" window esc
:results
if %FAILED%==0 (echo All tests passed.) else (echo Some tests FAILED.)
exit /b %FAILED%

:run
set "LOG=%LOGDIR%\%~n1_%3.log"
if "%~2"=="background" (
    "%BLENDER%" --background --factory-startup --python-exit-code 1 --python "%~1" > "%LOG%" 2>&1
) else if "%~2"=="simulate" (
    "%BLENDER%" --factory-startup --enable-event-simulate --python "%~1" -- %3 > "%LOG%" 2>&1
) else (
    "%BLENDER%" --factory-startup --python "%~1" -- %3 > "%LOG%" 2>&1
)
set RESULT=%ERRORLEVEL%
if not %RESULT%==0 (
    echo FAIL  %~nx1 %3 exited %RESULT%
    findstr /C:"Error" /C:"Traceback" /C:"FAIL" "%LOG%"
    set FAILED=1
    exit /b 0
)
findstr /R /C:"_OK$" "%LOG%" >nul
if errorlevel 1 (
    echo FAIL  %~nx1 %3
    findstr /C:"Error" /C:"Traceback" /C:"FAIL" "%LOG%"
    set FAILED=1
) else (
    echo PASS  %~nx1 %3
)
exit /b 0
