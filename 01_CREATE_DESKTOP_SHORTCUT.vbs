Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
Set shortcut = shell.CreateShortcut(shell.SpecialFolders("Desktop") & "\ActivTrack Report Generator.lnk")
shortcut.TargetPath = folder & "\02_START_APP.bat"
shortcut.WorkingDirectory = folder
shortcut.WindowStyle = 7
shortcut.Description = "ActivTrack Report Generator"
shortcut.Save
MsgBox "Desktop shortcut created. Keep the extracted application folder in this location.", 64, "ActivTrack"
