# The dictation port's test speech: synthetic voices from Windows' own speech synthesiser (SAPI,
# System.Speech), never a recording of a person. The dictation engine vectors record each clip's
# SHA-256, so a different Windows voice build shows up as a hash mismatch rather than a silent
# change.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File docs/rust-port/tools/sapi_speech.ps1 <folder>
#
# Writes 16-bit mono WAV files: three single sentences at 16, 48 and 44.1 kHz, and a 16 kHz clip
# of several sentences with long pauses, longer than one 30-second Whisper window.
param([Parameter(Mandatory = $true)][string]$Folder)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
New-Item -ItemType Directory -Force $Folder | Out-Null

$clips = @(
  @{ name = 'zira-16k'; voice = 'Microsoft Zira Desktop'; rate = 16000
     ssml = 'The ranger draws her bow and aims at the goblin by the bridge.' },
  @{ name = 'hazel-48k'; voice = 'Microsoft Hazel Desktop'; rate = 48000
     ssml = 'I open the old wooden chest and look for the silver key.' },
  @{ name = 'zira-44k'; voice = 'Microsoft Zira Desktop'; rate = 44100
     ssml = 'Roll for initiative. The dragon wakes up, and it is very angry.' },
  @{ name = 'zira-long-16k'; voice = 'Microsoft Zira Desktop'; rate = 16000
     ssml = 'The party reaches the gates of the old city at dusk. <break time="3s"/> A guard asks for a password, and the bard tries to remember it. <break time="4s"/> Somewhere behind the walls a bell rings three times. <break time="6s"/> The wizard casts a light spell, and the shadows move away from the stones. <break time="2s"/> We walk into the market square and look for the inn called the Sleeping Griffin. <break time="5s"/> I ask the innkeeper for a room and a hot meal for four travellers.' },
  @{ name = 'hazel-windows-16k'; voice = 'Microsoft Hazel Desktop'; rate = 16000
     ssml = 'Long ago, before the kingdoms of the north were founded, a small village stood at the edge of a great forest. The people there were farmers and woodcutters, and every autumn they gathered in the square to share the harvest. One year a stranger arrived with a map drawn on the skin of a dragon. He said that a tower stood in the middle of the forest, and that a library was hidden beneath it, full of books that no one had read for a thousand years. Three young friends decided to follow the map. The first was a hunter who could track a deer through snow. The second was a healer who knew every herb in the valley. The third was a storyteller who remembered every song her grandmother had sung. They walked for nine days, crossed two rivers, and climbed a hill covered in white stones before they finally saw the tower rising above the trees.' }
)

foreach ($clip in $clips) {
  $synthesiser = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $synthesiser.SelectVoice($clip.voice)
  $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo($clip.rate,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
  $path = Join-Path $Folder ($clip.name + '.wav')
  $synthesiser.SetOutputToWaveFile($path, $format)
  $synthesiser.SpeakSsml('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">' +
    $clip.ssml + '</speak>')
  $synthesiser.Dispose()
  $hash = (Get-FileHash -Algorithm SHA256 $path).Hash.ToLower()
  '{0} {1} {2}' -f $clip.name, (Get-Item $path).Length, $hash
}
