@echo off
setlocal
cd /d D:\Users\grok-auto-register
echo ===== hourly_remote_probe %DATE% %TIME% =====>> logs\hourly_remote_probe.log
rem Light probe: remotes only (no full CPA pool_health). Soft quota/slow temp-out + recover.
python scripts\disable_bad_upstreams.py --auto --soft-recover-hours 6 >> logs\hourly_remote_probe.log 2>&1
python ops_heartbeat.py --write logs\heartbeat.json >> logs\hourly_remote_probe.log 2>&1
echo ===== hourly_remote_probe done %DATE% %TIME% =====>> logs\hourly_remote_probe.log
endlocal
