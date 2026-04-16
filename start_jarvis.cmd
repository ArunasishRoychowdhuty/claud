@echo off
if not defined SystemRoot set "SystemRoot=C:\Windows"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -ExecutionPolicy Bypass -File "%~dp0start_jarvis.ps1" %*
