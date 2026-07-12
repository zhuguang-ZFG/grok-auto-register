' 无窗口启动：run_hidden.vbs <bat|exe|py> [args...]
' 计划任务请指向本 VBS，避免弹黑框。
Option Explicit
Dim sh, fso, root, target, args, i, cmd, ext, pyw
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
If WScript.Arguments.Count < 1 Then
  WScript.Quit 1
End If
target = WScript.Arguments(0)
If Mid(target, 2, 1) <> ":" And Left(target, 2) <> "\\" Then
  target = root & "\" & target
End If
args = ""
For i = 1 To WScript.Arguments.Count - 1
  args = args & " " & Quote(WScript.Arguments(i))
Next
ext = LCase(fso.GetExtensionName(target))
If ext = "py" Then
  ' 绝对路径 pythonw：计划任务环境往往没有 scoop PATH
  pyw = "C:\Users\zhugu\scoop\apps\python313\current\pythonw.exe"
  If Not fso.FileExists(pyw) Then pyw = "C:\Users\zhugu\AppData\Local\Programs\Python\Python313\pythonw.exe"
  If Not fso.FileExists(pyw) Then pyw = "pythonw.exe"
  cmd = Quote(pyw) & " " & Quote(target) & args
ElseIf ext = "bat" Or ext = "cmd" Then
  cmd = "cmd.exe /c " & Quote(target) & args
Else
  cmd = Quote(target) & args
End If
' 0 = 隐藏窗口, False = 不等待
sh.Run cmd, 0, False
WScript.Quit 0

Function Quote(s)
  Quote = """" & Replace(s, """", """""") & """"
End Function
