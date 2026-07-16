@echo off
setlocal
cd /d D:\Users\grok-auto-register
echo ===== daily_pool_health %DATE% %TIME% =====>> logs\daily_pool_health.log
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\cliproxy_fleet_watchdog.ps1 -Once >> logs\daily_pool_health.log 2>&1
python scripts\probe_three_pools.py --write logs\three_pools_probe.json >> logs\daily_pool_health.log 2>&1
rem --auto: hard 401/403/404 disable same run; other BAD needs streak>=2; reloads fleet when writing
python scripts\disable_bad_upstreams.py --auto >> logs\daily_pool_health.log 2>&1
rem Grok pool dead-account sweep (AT probe, soft-disable) — long; run detached
start "" /min cmd /c "python pool_health.py --probe >> logs\daily_pool_health.log 2>&1"
python ops_heartbeat.py --write logs\heartbeat.json >> logs\daily_pool_health.log 2>&1
echo ===== daily_pool_health done %DATE% %TIME% =====>> logs\daily_pool_health.log
endlocal
