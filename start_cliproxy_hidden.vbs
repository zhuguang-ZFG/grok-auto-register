' 隐藏启动本机 CLIProxyAPI
' GOMEMLIMIT + GOGC: 防止内存泄漏（github.com/router-for-me/CLIProxyAPI issue #2215）
Option Explicit
Dim sh, fso, exe, cfg, logs, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
exe = "D:\cli-proxy-api\cli-proxy-api.exe"
cfg = "D:\cli-proxy-api\config.yaml"
If Not fso.FileExists(exe) Then WScript.Quit 1
' 若已在跑则退出
Dim svc
On Error Resume Next
If GetObject("winmgmts:").ExecQuery("select * from Win32_Process where Name='cli-proxy-api.exe'").Count > 0 Then
  WScript.Quit 0
End If
On Error GoTo 0
' Go runtime memory limits: GC at ~256MB soft limit, aggressive GC
sh.Environment("Process").Item("GOMEMLIMIT") = "512MiB"
sh.Environment("Process").Item("GOGC") = "50"
cmd = """" & exe & """ -config """ & cfg & """"
sh.CurrentDirectory = "D:\cli-proxy-api"
sh.Run cmd, 0, False
