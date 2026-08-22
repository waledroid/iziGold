@echo off
rem Thin forwarder — the real launcher is scripts\xau-launch.bat (that's the
rem copy the README tells people to put on their Desktop; it does its own
rem repo-location detection so it works from anywhere). This root-level copy
rem exists only so double-clicking straight from a repo checkout also works,
rem without a second copy of the actual logic to drift out of sync with it.
call "%~dp0scripts\xau-launch.bat" %*
exit /b %errorlevel%
