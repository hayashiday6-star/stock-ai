@echo off
setlocal
cd /d "%~dp0"

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.

rem Opens the hypothesis ledger in whatever handles .md on this machine.
rem It lists every idea that has been tested and how each one ended.

start "" "%~dp0docs\HYPOTHESES.md"
