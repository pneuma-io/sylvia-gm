# The host owns this private stdin/stdout protocol. No audio or transcript is logged or saved.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
function Reply($value) { [Console]::WriteLine(($value | ConvertTo-Json -Compress -Depth 4)) }
try {
    Add-Type -AssemblyName System.Speech
    $request = [Console]::ReadLine() | ConvertFrom-Json
    # Only the installed Microsoft desktop dictation engine is selected, never a cloud SDK.
    $engines = @([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() |
        Where-Object { $_.AdditionalInfo['Vendor'] -eq 'Microsoft' -and
            $_.AdditionalInfo.ContainsKey('Dictation') -and $_.Id -like 'MS-*-DESK' })
    if ($request.mode -eq 'capabilities') {
        Reply @{ engines = @($engines | ForEach-Object {
            @{ id = $_.Id; language = $_.Culture.Name; label = $_.Culture.DisplayName }
        }) }
        exit
    }
    if ($request.mode -ne 'start' -or !($engines | Where-Object { $_.Id -eq $request.engine })) {
        Reply @{ error = 'unavailable' }; exit
    }
    Add-Type -ReferencedAssemblies System.Speech -TypeDefinition @'
using System;
using System.Text;
using System.Speech.Recognition;
using System.Threading;

// Event handlers run in the recognizer's CLR callbacks, without a PowerShell runspace.
public sealed class SylviaLocalDictation : IDisposable {
    private readonly SpeechRecognitionEngine engine;
    private readonly ManualResetEvent completed = new ManualResetEvent(false);
    private readonly StringBuilder text = new StringBuilder();
    private Exception failure;
    public SylviaLocalDictation(string id) {
        engine = new SpeechRecognitionEngine(id);
        // Disable acoustic learning for this instance without changing the user's persisted settings.
        engine.UpdateRecognizerSetting("AdaptationOn", 0);
        engine.SpeechRecognized += delegate(object sender, SpeechRecognizedEventArgs e) {
            lock (text) {
                if (text.Length + e.Result.Text.Length > 32000) {
                    failure = new InvalidOperationException();
                    engine.RecognizeAsyncCancel();
                } else {
                    if (text.Length > 0) text.Append(' ');
                    text.Append(e.Result.Text);
                }
            }
        };
        engine.RecognizeCompleted += delegate(object sender, RecognizeCompletedEventArgs e) {
            if (e.Error != null) failure = e.Error;
            completed.Set();
        };
        engine.LoadGrammar(new DictationGrammar());
    }
    public void Start() {
        engine.SetInputToDefaultAudioDevice();
        engine.RecognizeAsync(RecognizeMode.Multiple);
    }
    public string Stop() {
        if (!completed.WaitOne(0)) engine.RecognizeAsyncStop();
        if (!completed.WaitOne(30000)) throw new TimeoutException();
        if (failure != null) throw failure;
        lock (text) { return text.ToString(); }
    }
    public void Dispose() {
        engine.Dispose();
        completed.Dispose();
        lock (text) { text.Clear(); }
    }
}
'@
    $capture = $null
    try {
        $capture = New-Object SylviaLocalDictation([string]$request.engine)
        try { $capture.Start() } catch {
            $cause = $_.Exception
            while ($cause.InnerException) { $cause = $cause.InnerException }
            $code = if ($cause -is [UnauthorizedAccessException] -or $cause.HResult -eq -2147024891) {
                'permission'
            } else { 'device' }
            Reply @{ error = $code }; exit
        }
        Reply @{ status = 'recording' }
        if ([Console]::ReadLine() -eq 'stop') {
            try { Reply @{ text = $capture.Stop() } } catch { Reply @{ error = 'provider' } }
        }
    } finally { if ($capture) { $capture.Dispose() } }
} catch { Reply @{ error = 'unavailable' } }
