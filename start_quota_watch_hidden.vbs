' Hidden launch quota_watch.py (python.exe window style 0)
Option Explicit
Dim sh, fso, root, py, script, procs, p, cl, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
script = root & "\quota_watch.py"
py = "C:\Users\zhugu\scoop\apps\python313\current\python.exe"
If Not fso.FileExists(py) Then py = "C:\Users\zhugu\AppData\Local\Programs\Python\Python313\python.exe"
If Not fso.FileExists(py) Then py = "python.exe"
If Not fso.FileExists(script) Then WScript.Quit 1
On Error Resume Next
Set procs = GetObject("winmgmts:").ExecQuery( _
  "SELECT ProcessId,CommandLine FROM Win32_Process WHERE Name='python.exe' OR Name='pythonw.exe'")
For Each p In procs
  cl = LCase(p.CommandLine & "")
  If InStr(cl, "quota_watch") > 0 Then WScript.Quit 0
Next
On Error GoTo 0
sh.CurrentDirectory = root
cmd = """" & py & """ -u """ & script & """"
sh.Run cmd, 0, False
WScript.Quit 0
