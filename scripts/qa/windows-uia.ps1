param(
 [Parameter(Mandatory=$true)][int]$AppPid,
 [Parameter(Mandatory=$true)][string]$ExpectedExe,
 [ValidateSet('tree','click','invoke','value','keys','capture','close','expand','toggle','select','render_capture','check','activate','collapse','scroll')][string]$Action='tree',
 [string]$Id='', [string]$Name='', [string]$Text='', [string]$Output='',
 [string]$WindowTitle='Sylvia GM', [string]$ControlType='', [string]$ScopeId='', [string]$RuntimeId=''
)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public class SylviaQaInput {
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint flags);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT {public int dx,dy; public uint mouseData,dwFlags,time; public IntPtr dwExtraInfo;}
 [StructLayout(LayoutKind.Explicit)] public struct INPUT { [FieldOffset(0)]public uint type; [FieldOffset(8)]public MOUSEINPUT mi; }
 [DllImport("user32.dll",SetLastError=true)] public static extern uint SendInput(uint n,INPUT[] p,int size);
 public static uint Click(){var p=new INPUT[2];p[0].mi.dwFlags=2;p[1].mi.dwFlags=4; return SendInput(2,p,Marshal.SizeOf(typeof(INPUT)));}
}
"@
$owned=Get-CimInstance Win32_Process -Filter "ProcessId=$AppPid"
if(!$owned -or [IO.Path]::GetFullPath($owned.ExecutablePath) -ne [IO.Path]::GetFullPath($ExpectedExe)){throw 'Target is not the specified owned executable'}
$condition=New-Object System.Windows.Automation.AndCondition((New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$AppPid)),(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,$WindowTitle)))
$window=[System.Windows.Automation.AutomationElement]::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children,$condition)
if(!$window){throw 'Owned target window unavailable'}
function Target {if($RuntimeId){$matches=@($window.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)|Where-Object {($_.GetRuntimeId() -join ',') -eq $RuntimeId});if($matches.Count -ne 1){throw 'Runtime selector unavailable or ambiguous'};return $matches[0]};$searchRoot=$window;if($ScopeId){$searchRoot=$window.FindFirst([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty,$ScopeId)));if(!$searchRoot){throw 'Requested owned scope unavailable'}};if($Id){$p=[System.Windows.Automation.AutomationElement]::AutomationIdProperty;$v=$Id}else{$p=[System.Windows.Automation.AutomationElement]::NameProperty;$v=$Name};$e=$searchRoot.FindFirst([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition($p,$v)));if(!$e){throw "Control unavailable: $Id $Name"};if($ControlType){$matches=$searchRoot.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition($p,$v))) | Where-Object {$_.Current.ControlType.ProgrammaticName -eq ("ControlType."+$ControlType)};if(@($matches).Count -ne 1){throw "Selector must match exactly one $ControlType control"};$e=@($matches)[0]};return $e}
function Assert-Foreground { [uint32]$foregroundPid=0; [SylviaQaInput]::GetWindowThreadProcessId([SylviaQaInput]::GetForegroundWindow(),[ref]$foregroundPid)|Out-Null; if($foregroundPid -ne $AppPid){throw ("Foreground ownership check failed: expected $AppPid, observed $foregroundPid, handle $([SylviaQaInput]::GetForegroundWindow())")} }; function Focus-Owned { [SylviaQaInput]::SetForegroundWindow([IntPtr]$window.Current.NativeWindowHandle)|Out-Null; $window.SetFocus(); Assert-Foreground }
# UIA can invoke covered background controls. Treat the visible tutorial as modal.
$tutorial=$window.FindFirst([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty,'tutorial-skip-btn')))
if($tutorial -and !$tutorial.Current.IsOffscreen -and $Action -notin @('tree','capture','render_capture','close') -and $Id -notlike 'tutorial-*' -and $Name -ne 'Skip'){throw 'Handle the visible tutorial before operating background controls'}
$result=[ordered]@{utc=[DateTime]::UtcNow.ToString('o');pid=$AppPid;executable=$owned.ExecutablePath;action=$Action;selector_id=$Id;selector_name=$Name}
switch($Action){
 'tree' {$result.elements=@($window.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)|ForEach-Object{[ordered]@{runtime_id=($_.GetRuntimeId() -join ',');name=$_.Current.Name;id=$_.Current.AutomationId;type=$_.Current.ControlType.ProgrammaticName;enabled=$_.Current.IsEnabled;offscreen=$_.Current.IsOffscreen;bounds=$_.Current.BoundingRectangle.ToString();patterns=@($_.GetSupportedPatterns().ProgrammaticName)}})}
 'click' {$e=Target;if(!$e.Current.IsEnabled -or $e.Current.IsOffscreen){throw 'Control disabled/offscreen'};[SylviaQaInput]::SetForegroundWindow([IntPtr]$window.Current.NativeWindowHandle)|Out-Null;$e.SetFocus();Assert-Foreground;$r=$e.Current.BoundingRectangle;if($r.IsEmpty){throw 'No target bounds'};[SylviaQaInput]::SetCursorPos([int]($r.X+$r.Width/2),[int]($r.Y+$r.Height/2))|Out-Null;Assert-Foreground;$sent=[SylviaQaInput]::Click();if($sent -ne 2){throw "SendInput inserted $sent of 2 events"};$result.foreground_verified=$true;$result.input_events=$sent}
 'activate' {$e=Target;if(!$e.Current.IsEnabled){throw 'Control disabled'};$p=$null;if($e.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$p)){$p.Invoke();$result.pattern='Invoke'}elseif($e.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$p)){$p.Expand();$result.pattern='ExpandCollapse'}elseif($e.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$p)){$p.Toggle();$result.pattern='Toggle'}else{throw 'No supported activation pattern'}}
 'invoke' {$e=Target;$e.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()}
 'collapse' {$e=Target;$e.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Collapse()}
 'scroll' {$e=Target;$e.GetCurrentPattern([System.Windows.Automation.ScrollItemPattern]::Pattern).ScrollIntoView()}
 'expand' {$e=Target;$e.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Expand()}
 'check' {$e=Target;$p=$e.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern);$result.before_toggle=$p.Current.ToggleState.ToString();if($p.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::On){$p.Toggle()};$result.after_toggle=$p.Current.ToggleState.ToString();if($p.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::On){throw 'Checkbox did not become checked'}}
 'toggle' {$e=Target;$e.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle()}
 'select' {$e=Target;$defaultAction=$null;if($e.Current.ControlType -eq [System.Windows.Automation.ControlType]::ListItem -and $e.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$defaultAction)){$defaultAction.Invoke();$result.selection_method='native option default action'}else{$e.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select();$result.selection_method='SelectionItem'}}
 'value' {$e=Target;$e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($Text)}
 'keys' {Focus-Owned;Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.SendKeys]::SendWait($Text);$result.foreground_verified=$true}
 'capture' {if(!$Output){throw 'Output required'};Focus-Owned;$r=$window.Current.BoundingRectangle;$b=New-Object Drawing.Bitmap([int]$r.Width,[int]$r.Height);$g=[Drawing.Graphics]::FromImage($b);try{$g.CopyFromScreen([int]$r.X,[int]$r.Y,0,0,$b.Size);$b.Save($Output,[Drawing.Imaging.ImageFormat]::Png)}finally{$g.Dispose();$b.Dispose()};$result.screenshot=$Output;$result.foreground_verified=$true}
 'render_capture' {if(!$Output){throw 'Output required'};$r=$window.Current.BoundingRectangle;$b=New-Object Drawing.Bitmap([int]$r.Width,[int]$r.Height);$g=[Drawing.Graphics]::FromImage($b);$dc=$g.GetHdc();try{$ok=[SylviaQaInput]::PrintWindow([IntPtr]$window.Current.NativeWindowHandle,$dc,2)}finally{$g.ReleaseHdc($dc)};try{if(!$ok){throw 'Owned window rendering unavailable'};$b.Save($Output,[Drawing.Imaging.ImageFormat]::Png)}finally{$g.Dispose();$b.Dispose()};$result.screenshot=$Output;$result.capture_method='PrintWindow owned HWND; no foreground assertion'} 
 'close' {$window.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close()}
}
$json=$result|ConvertTo-Json -Depth 8
if($Output -and $Action -notin @('capture','render_capture','check','activate','collapse','scroll')){$json|Set-Content -LiteralPath $Output -Encoding utf8}
$json











