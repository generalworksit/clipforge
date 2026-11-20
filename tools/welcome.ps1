# First-run welcome for run.bat: a plain explanation before the setup starts.
Add-Type -AssemblyName PresentationFramework
$text = @"
ClipForge turns long videos into short vertical clips with captions, on this PC.

This first start sets everything up for you, usually in 5 to 15 minutes:
  - FFmpeg, Python and Node.js are installed if they are missing
  - the app's own packages go into this folder only

Windows may ask for permission while installing. Click Yes.
You can watch the progress in the black window.

When it is done, ClipForge opens in your browser at http://127.0.0.1:8000
Everything runs on this PC. Your videos never leave it.

Next time, double-click run.bat and it starts in a few seconds.
"@
[void][System.Windows.MessageBox]::Show($text, "Welcome to ClipForge", "OK", "Information")
