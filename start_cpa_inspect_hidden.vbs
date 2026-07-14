' 隐藏启动 cpa-auth-inspect 巡检服务
Option Explicit
Dim sh, fso, exe, script
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
script = "D:\Users\grok-auto-register\scripts\start_cpa_inspect.ps1"
If Not fso.FileExists(script) Then WScript.Quit 1
' 若已在跑则退出（PS1 脚本内部会检查）
Dim cmd
cmd = "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & script & """"
sh.Run cmd, 0, False
