' Hidden launch dahl_pipeline proxy (no black window). Idempotent.
Option Explicit
Dim sh, fso, root, py, procs, p, cl, cmd, logf, port
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
py = "C:\Users\zhugu\scoop\apps\python313\current\python.exe"
If Not fso.FileExists(py) Then py = "C:\Users\zhugu\AppData\Local\Programs\Python\Python313\python.exe"
If Not fso.FileExists(py) Then py = "python.exe"
On Error Resume Next
Set procs = GetObject("winmgmts:").ExecQuery( _
  "SELECT ProcessId,CommandLine FROM Win32_Process WHERE Name='python.exe' OR Name='pythonw.exe'")
For Each p In procs
  cl = LCase(p.CommandLine & "")
  If InStr(cl, "dahl_pipeline") > 0 And InStr(cl, "proxy") > 0 Then WScript.Quit 0
Next
On Error GoTo 0
If Not fso.FolderExists(root & "\logs") Then fso.CreateFolder root & "\logs"
sh.CurrentDirectory = root
logf = root & "\logs\dahl_proxy.out.log"
port = "8330"
cmd = "cmd.exe /c """"" & py & """ -u -m dahl_pipeline proxy --port " & port & _
      " --proxy http://127.0.0.1:7897 --api-key sk-local-dahl" & _
      " --remint-max-per-day 5 --remint-low-threshold 50000 >> """ & logf & """ 2>&1"""
sh.Run cmd, 0, False
WScript.Quit 0
