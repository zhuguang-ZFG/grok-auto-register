@echo off
rem cc-switch Claude 渠道轮换（计划任务 CcClaudeProviderRotate 每 3h 调用）
cd /d D:\Users\grok-auto-register
"C:\Users\zhugu\scoop\apps\python313\current\pythonw.exe" "D:\Users\grok-auto-register\scripts\cc_rotate_claude_provider.py" next >> "D:\Users\grok-auto-register\logs\_cc_claude_rotate_task.log" 2>&1
