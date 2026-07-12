' Hidden launch grok_register_ttk.py auto (no black window)
Option Explicit
Dim sh, fso, root, py, script, procs, p, cl, cmd, logf
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
script = root & "\grok_register_ttk.py"
py = "C:\Users\zhugu\scoop\apps\python313\current\python.exe"
If Not fso.FileExists(py) Then py = "C:\Users\zhugu\AppData\Local\Programs\Python\Python313\python.exe"
If Not fso.FileExists(py) Then py = "python.exe"
If Not fso.FileExists(script) Then WScript.Quit 1
On Error Resume Next
Set procs = GetObject("winmgmts:").ExecQuery( _
  "SELECT ProcessId,CommandLine FROM Win32_Process WHERE Name='python.exe' OR Name='pythonw.exe'")
For Each p In procs
  cl = LCase(p.CommandLine & "")
  If InStr(cl, "grok_register_ttk.py") > 0 And InStr(cl, "auto") > 0 Then WScript.Quit 0
Next
On Error GoTo 0
If Not fso.FolderExists(root & "\logs") Then fso.CreateFolder root & "\logs"
sh.CurrentDirectory = root
logf = root & "\logs\register_auto.out.log"
cmd = "cmd.exe /c """"" & py & """ -u """ & script & """ auto >> """ & logf & """ 2>&1"""
sh.Run cmd, 0, False
WScript.Quit 0
