' GCC Job Radar - Background Bot Launcher
' Launches pythonw.exe completely silently with NO console window or popup.
Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

strScriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strScriptDir

strPythonw = strScriptDir & "\.venv\Scripts\pythonw.exe"
If Not objFSO.FileExists(strPythonw) Then
    strPythonw = "pythonw.exe"
End If

strCommand = """" & strPythonw & """ """ & strScriptDir & "\tools\bot_listener.py"""
objShell.Run strCommand, 0, False
