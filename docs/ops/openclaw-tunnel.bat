@echo off
rem ============================================================
rem  OpenClaw 首尔网关隧道 (开机自启 + 断线自动重连)
rem  由 Hermes 生成 2026-08-11
rem  用法: 放任意目录, 计划任务开机运行本脚本
rem  依赖: Windows 自带 OpenSSH Client (ssh.exe)
rem ============================================================
title OpenClaw Tunnel (Seoul 43.155.129.186)

:loop
echo [%date% %time%] 连接 43.155.129.186:22 ...
ssh -N -L 18789:127.0.0.1:18789 -L 18791:127.0.0.1:18791 ^
    -o BatchMode=yes -o ExitOnForwardFailure=yes ^
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 ^
    -o TCPKeepAlive=yes -o ConnectTimeout=10 ^
    ubuntu@43.155.129.186
echo [%date% %time%] 隧道断开, 5 秒后重连...
timeout /t 5 /nobreak >nul
goto loop
