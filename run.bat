@echo off
REM Command Prompt cannot run a .ps1 file, and double-clicking one opens it in an
REM editor rather than running it -- so a Windows user following the README from
REM cmd sees nothing happen at all. This hands the script to PowerShell, and
REM bypasses the execution policy that blocks scripts which arrived in a
REM downloaded ZIP.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
