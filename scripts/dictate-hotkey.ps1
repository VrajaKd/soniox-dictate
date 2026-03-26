<#
.SYNOPSIS
    Global hotkey listener for Typeza Dictation tool.
    Communicates with WSL2 dictation process via TCP socket.
    Captures audio on Windows and streams to WSL via TCP.

.DESCRIPTION
    Hotkeys:
      Ctrl+Shift+Space  - start/stop recording
      Ctrl+Shift+L      - toggle ET/EN language mode
      Ctrl+Shift+E      - toggle auto-Enter after paste
      Ctrl+Shift+C      - cancel recording
      Ctrl+C            - quit this script

    Audio feedback:
      High beep   = recording started  (from WSL "started" response)
      Low beep    = recording stopped  (from WSL "stopped" response)
      Double beep = text pasted        (from WSL "paste" response)
      Three beeps = cancelled          (from WSL "cancelled" response)

    Audio capture:
      Uses Win32 waveIn API to capture PCM audio from the default microphone.
      Streams raw PCM frames (16kHz, 16-bit, mono) over TCP to WSL.
      Protocol: 0x01 + uint16_LE(length) + PCM data for audio frames.
#>

$PORT = 19876

Add-Type -AssemblyName System.Windows.Forms

Add-Type @"
using System;
using System.Runtime.InteropServices;

public class HotKeyHelper {
    [DllImport("user32.dll")]
    public static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);
    [DllImport("user32.dll")]
    public static extern bool UnregisterHotKey(IntPtr hWnd, int id);
}

public class KeyHelper {
    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
}

public class MsgHelper {
    [StructLayout(LayoutKind.Sequential)]
    public struct MSG {
        public IntPtr hwnd;
        public uint message;
        public IntPtr wParam;
        public IntPtr lParam;
        public uint time;
        public int pt_x;
        public int pt_y;
    }

    [DllImport("user32.dll")]
    public static extern bool PeekMessage(ref MSG lpMsg, IntPtr hWnd, uint wMsgFilterMin, uint wMsgFilterMax, uint wRemoveMsg);

    [DllImport("user32.dll")]
    public static extern uint MsgWaitForMultipleObjects(uint nCount, IntPtr pHandles, bool bWaitAll, uint dwMilliseconds, uint dwWakeMask);

    public const uint WM_HOTKEY = 0x0312;
    public const uint PM_REMOVE = 0x0001;
    public const uint QS_ALLINPUT = 0x04FF;
}
"@

# --- Audio Recorder (Win32 waveIn API) ---

Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;

public class AudioRecorder {
    // waveIn constants
    const int WAVE_FORMAT_PCM = 1;
    const int CALLBACK_FUNCTION = 0x00030000;
    const int WIM_DATA = 0x3C0;
    const int BUFFER_COUNT = 4;
    const int BUFFER_SIZE = 4096;

    // P/Invoke: waveIn functions
    delegate void WaveInProc(IntPtr hwi, uint uMsg, IntPtr dwInstance, IntPtr dwParam1, IntPtr dwParam2);

    [DllImport("winmm.dll")]
    static extern int waveInOpen(out IntPtr phwi, uint uDeviceID, ref WAVEFORMATEX lpFormat,
        WaveInProc dwCallback, IntPtr dwInstance, int fdwOpen);

    [DllImport("winmm.dll")]
    static extern int waveInStart(IntPtr hwi);

    [DllImport("winmm.dll")]
    static extern int waveInStop(IntPtr hwi);

    [DllImport("winmm.dll")]
    static extern int waveInReset(IntPtr hwi);

    [DllImport("winmm.dll")]
    static extern int waveInClose(IntPtr hwi);

    [DllImport("winmm.dll")]
    static extern int waveInPrepareHeader(IntPtr hwi, IntPtr pwh, int cbwh);

    [DllImport("winmm.dll")]
    static extern int waveInUnprepareHeader(IntPtr hwi, IntPtr pwh, int cbwh);

    [DllImport("winmm.dll")]
    static extern int waveInAddBuffer(IntPtr hwi, IntPtr pwh, int cbwh);

    [StructLayout(LayoutKind.Sequential)]
    struct WAVEFORMATEX {
        public short wFormatTag;
        public short nChannels;
        public int nSamplesPerSec;
        public int nAvgBytesPerSec;
        public short nBlockAlign;
        public short wBitsPerSample;
        public short cbSize;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct WAVEHDR {
        public IntPtr lpData;
        public int dwBufferLength;
        public int dwBytesRecorded;
        public IntPtr dwUser;
        public int dwFlags;
        public int dwLoops;
        public IntPtr lpNext;
        public IntPtr reserved;
    }

    // Thread-safe queue (manual lock — avoids ConcurrentQueue assembly dependency)
    private readonly object _lock = new object();
    private readonly Queue<byte[]> _queue = new Queue<byte[]>();

    private IntPtr _hwi;
    private WaveInProc _callback;  // prevent GC of delegate
    private IntPtr[] _headers;
    private GCHandle[] _bufferHandles;
    private volatile bool _recording;

    public void Start() {
        var fmt = new WAVEFORMATEX {
            wFormatTag = WAVE_FORMAT_PCM,
            nChannels = 1,
            nSamplesPerSec = 16000,
            nAvgBytesPerSec = 32000,
            nBlockAlign = 2,
            wBitsPerSample = 16,
            cbSize = 0
        };

        _callback = WaveCallback;

        // WAVE_MAPPER = 0xFFFFFFFF (default input device)
        int result = waveInOpen(out _hwi, 0xFFFFFFFF, ref fmt, _callback, IntPtr.Zero, CALLBACK_FUNCTION);
        if (result != 0) throw new Exception("waveInOpen failed with error code " + result);

        int hdrSize = Marshal.SizeOf(typeof(WAVEHDR));
        _headers = new IntPtr[BUFFER_COUNT];
        _bufferHandles = new GCHandle[BUFFER_COUNT];

        for (int i = 0; i < BUFFER_COUNT; i++) {
            byte[] buf = new byte[BUFFER_SIZE];
            _bufferHandles[i] = GCHandle.Alloc(buf, GCHandleType.Pinned);

            var hdr = new WAVEHDR {
                lpData = _bufferHandles[i].AddrOfPinnedObject(),
                dwBufferLength = BUFFER_SIZE,
                dwBytesRecorded = 0,
                dwUser = IntPtr.Zero,
                dwFlags = 0,
                dwLoops = 0,
                lpNext = IntPtr.Zero,
                reserved = IntPtr.Zero
            };

            _headers[i] = Marshal.AllocHGlobal(hdrSize);
            Marshal.StructureToPtr(hdr, _headers[i], false);

            waveInPrepareHeader(_hwi, _headers[i], hdrSize);
            waveInAddBuffer(_hwi, _headers[i], hdrSize);
        }

        _recording = true;
        waveInStart(_hwi);
    }

    private void WaveCallback(IntPtr hwi, uint uMsg, IntPtr dwInstance, IntPtr dwParam1, IntPtr dwParam2) {
        if (uMsg != WIM_DATA) return;

        var hdr = (WAVEHDR)Marshal.PtrToStructure(dwParam1, typeof(WAVEHDR));

        if (hdr.dwBytesRecorded > 0) {
            byte[] data = new byte[hdr.dwBytesRecorded];
            Marshal.Copy(hdr.lpData, data, 0, hdr.dwBytesRecorded);
            lock (_lock) {
                if (_queue.Count < 200) {
                    _queue.Enqueue(data);
                }
            }
        }

        // Re-add buffer only if still recording
        if (_recording) {
            int hdrSize = Marshal.SizeOf(typeof(WAVEHDR));
            waveInAddBuffer(hwi, dwParam1, hdrSize);
        }
    }

    public void Stop() {
        _recording = false;
        waveInStop(_hwi);
        waveInReset(_hwi);  // Returns all pending buffers (triggers WIM_DATA for each)
        Thread.Sleep(100);   // Let callbacks complete

        int hdrSize = Marshal.SizeOf(typeof(WAVEHDR));
        for (int i = 0; i < BUFFER_COUNT; i++) {
            waveInUnprepareHeader(_hwi, _headers[i], hdrSize);
            Marshal.FreeHGlobal(_headers[i]);
            _bufferHandles[i].Free();
        }

        waveInClose(_hwi);
    }

    /// <summary>Dequeue one audio chunk, or null if empty.</summary>
    public byte[] Dequeue() {
        lock (_lock) {
            if (_queue.Count > 0) {
                return _queue.Dequeue();
            }
            return null;
        }
    }
}
"@

# --- State ---
$recording = $false
$recorder = $null
$responseBuffer = ""

# --- TCP connection to WSL (raw stream, no StreamReader/StreamWriter) ---
$client = $null
$stream = $null

function Connect-WSL {
    try {
        $script:client = New-Object System.Net.Sockets.TcpClient
        $script:client.Connect("localhost", $PORT)
        $script:stream = $script:client.GetStream()
        Write-Host "Connected to WSL dictation server on port $PORT" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "ERROR: Cannot connect to WSL dictation server on port $PORT" -ForegroundColor Red
        Write-Host "  Start the server first: ./scripts/django-command.sh local dictate" -ForegroundColor Yellow
        return $false
    }
}

function Send-Command($cmd) {
    try {
        $bytes = [System.Text.Encoding]::ASCII.GetBytes("$cmd`n")
        $script:stream.Write($bytes, 0, $bytes.Length)
        $script:stream.Flush()
    } catch {
        Write-Host "ERROR: Lost connection to WSL server" -ForegroundColor Red
    }
}

function Send-AudioFrames {
    # Drain audio queue and send framed PCM over TCP
    $chunk = $script:recorder.Dequeue()
    while ($chunk -ne $null) {
        $len = $chunk.Length
        # Frame: 0x01 + uint16_LE(length) + PCM data
        $script:stream.WriteByte(0x01)
        $lenBytes = [BitConverter]::GetBytes([uint16]$len)
        $script:stream.Write($lenBytes, 0, 2)
        $script:stream.Write($chunk, 0, $len)
        $chunk = $script:recorder.Dequeue()
    }
    $script:stream.Flush()
}

function Send-AudioEnd {
    # Send end-of-audio marker (0x02)
    try {
        $script:stream.WriteByte(0x02)
        $script:stream.Flush()
    } catch {}
}

function Stop-Recording {
    # Stop audio capture, drain remaining frames, send end marker
    if ($script:recording -and $script:recorder) {
        $script:recorder.Stop()
        Send-AudioFrames
        Send-AudioEnd
        $script:recording = $false
        $script:recorder = $null
    }
}

function Read-Responses {
    # Read all available response bytes, accumulate lines, dispatch complete ones
    try {
        while ($script:stream -and $script:stream.DataAvailable) {
            $b = $script:stream.ReadByte()
            if ($b -eq -1) { break }
            if ($b -eq 10) {  # \n — line complete
                $line = $script:responseBuffer.TrimEnd("`r")
                $script:responseBuffer = ""
                if ($line) { Handle-Response $line }
            } else {
                $script:responseBuffer += [char]$b
            }
        }
    } catch {
        Write-Host "ERROR: Read failed: $_" -ForegroundColor Red
    }
}

function Handle-Response($response) {
    switch ($response) {
        "started" {
            try {
                $script:recorder = New-Object AudioRecorder
                $script:recorder.Start()
                $script:recording = $true
                [Console]::Beep(1000, 200)
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Recording started" -ForegroundColor Green
            } catch {
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ERROR: Audio capture failed: $_" -ForegroundColor Red
                $script:recording = $false
                $script:recorder = $null
                Send-Command "cancel"
            }
        }
        "stopped" {
            Stop-Recording
            [Console]::Beep(400, 200)
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Recording stopped (finalizing...)" -ForegroundColor Yellow
        }
        "paste" {
            Start-Sleep -Milliseconds 100
            [System.Windows.Forms.SendKeys]::SendWait("^v")
            [Console]::Beep(800, 80); Start-Sleep -Milliseconds 60; [Console]::Beep(800, 80)
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Auto-pasted at cursor" -ForegroundColor Green
        }
        "paste_enter" {
            Start-Sleep -Milliseconds 100
            [System.Windows.Forms.SendKeys]::SendWait("^v")
            Start-Sleep -Milliseconds 50
            [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
            [Console]::Beep(800, 80); Start-Sleep -Milliseconds 60; [Console]::Beep(800, 80)
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Auto-pasted + Enter" -ForegroundColor Green
        }
        "paste_ctrl_enter" {
            Start-Sleep -Milliseconds 100
            [System.Windows.Forms.SendKeys]::SendWait("^v")
            # Longer delay to let the terminal process the paste fully
            Start-Sleep -Milliseconds 300
            # Use keybd_event for Enter — bypasses SendKeys limitations with terminals
            [KeyHelper]::keybd_event(0x0D, 0, 0, [UIntPtr]::Zero)        # VK_RETURN down
            [KeyHelper]::keybd_event(0x0D, 0, 2, [UIntPtr]::Zero)        # VK_RETURN up
            [Console]::Beep(800, 80); Start-Sleep -Milliseconds 60; [Console]::Beep(800, 80)
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Auto-pasted + Enter (keybd_event)" -ForegroundColor Green
        }
        "cancelled" {
            Stop-Recording
            [Console]::Beep(300, 100); Start-Sleep -Milliseconds 50
            [Console]::Beep(300, 100); Start-Sleep -Milliseconds 50
            [Console]::Beep(300, 100)
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Recording cancelled" -ForegroundColor Red
        }
        "error" {
            Stop-Recording
            [Console]::Beep(200, 500)
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error from server" -ForegroundColor Red
        }
        default {
            if ($response.StartsWith("lang:")) {
                $mode = $response.Substring(5)
                [Console]::Beep(600, 100)
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Language: $mode" -ForegroundColor Cyan
            }
            elseif ($response.StartsWith("enter:")) {
                $mode = $response.Substring(6)
                [Console]::Beep(600, 100)
                switch ($mode) {
                    "off"          { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Auto-Enter: OFF" -ForegroundColor DarkGray }
                    "enter"        { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Auto-Enter: ENTER" -ForegroundColor Cyan }
                    "ctrl+enter"   { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Auto-Enter: CTRL+ENTER" -ForegroundColor Cyan }
                }
            }
        }
    }
}

# --- Main ---

# Connect to WSL server
if (-not (Connect-WSL)) {
    exit 1
}

# Register global hotkeys
# MOD_CTRL = 0x0002, MOD_SHIFT = 0x0004
$MOD = 0x0002 -bor 0x0004  # Ctrl+Shift

try {
    $r1 = [HotKeyHelper]::RegisterHotKey([IntPtr]::Zero, 1, $MOD, 0x20)  # Space
    $r2 = [HotKeyHelper]::RegisterHotKey([IntPtr]::Zero, 2, $MOD, 0x4C)  # L
    $r3 = [HotKeyHelper]::RegisterHotKey([IntPtr]::Zero, 3, $MOD, 0x43)  # C
    $r4 = [HotKeyHelper]::RegisterHotKey([IntPtr]::Zero, 4, $MOD, 0x45)  # E

    if (-not $r1) { Write-Host "WARNING: Failed to register Ctrl+Shift+Space" -ForegroundColor Red }
    if (-not $r2) { Write-Host "WARNING: Failed to register Ctrl+Shift+L" -ForegroundColor Red }
    if (-not $r3) { Write-Host "WARNING: Failed to register Ctrl+Shift+C" -ForegroundColor Red }
    if (-not $r4) { Write-Host "WARNING: Failed to register Ctrl+Shift+E" -ForegroundColor Red }

    Write-Host ""
    Write-Host "=== Dictation Hotkeys Active ===" -ForegroundColor Green
    Write-Host "  Ctrl+Shift+Space  = start/stop recording"
    Write-Host "  Ctrl+Shift+L      = toggle ET/EN"
    Write-Host "  Ctrl+Shift+E      = toggle auto-Enter"
    Write-Host "  Ctrl+Shift+C      = cancel recording"
    Write-Host "  Ctrl+C            = quit"
    Write-Host ""
    Write-Host "Auto-Enter modes (Ctrl+Shift+E cycles):" -ForegroundColor DarkGray
    Write-Host "  OFF          = paste only" -ForegroundColor DarkGray
    Write-Host "  ENTER        = paste + Enter (Slack, chat)" -ForegroundColor DarkGray
    Write-Host "  CTRL+ENTER   = paste + Ctrl+Enter (Claude Code)" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "Audio: Windows waveIn capture (16kHz, 16-bit, mono)" -ForegroundColor DarkGray
    Write-Host ""

    $msg = New-Object MsgHelper+MSG
    while ($true) {
        # Wait for hotkey message or 20ms timeout (faster polling for audio)
        [MsgHelper]::MsgWaitForMultipleObjects(
            0, [IntPtr]::Zero, $false, 20, [MsgHelper]::QS_ALLINPUT
        ) | Out-Null

        # Process hotkey messages
        while ([MsgHelper]::PeekMessage([ref]$msg, [IntPtr]::Zero, 0, 0, [MsgHelper]::PM_REMOVE)) {
            if ($msg.message -eq [MsgHelper]::WM_HOTKEY) {
                $id = $msg.wParam.ToInt32()
                switch ($id) {
                    1 { Send-Command "toggle" }
                    2 { Send-Command "language" }
                    3 { Send-Command "cancel" }
                    4 { Send-Command "enter" }
                }
            }
        }

        # Stream audio frames if recording
        if ($script:recording -and $script:recorder) {
            Send-AudioFrames
        }

        # Read responses from WSL (non-blocking)
        Read-Responses
    }
}
finally {
    # Stop recording if still active
    if ($script:recording -and $script:recorder) {
        try { $script:recorder.Stop() } catch {}
        $script:recording = $false
        $script:recorder = $null
    }

    [HotKeyHelper]::UnregisterHotKey([IntPtr]::Zero, 1)
    [HotKeyHelper]::UnregisterHotKey([IntPtr]::Zero, 2)
    [HotKeyHelper]::UnregisterHotKey([IntPtr]::Zero, 3)
    [HotKeyHelper]::UnregisterHotKey([IntPtr]::Zero, 4)
    if ($script:client) { $script:client.Close() }
    Write-Host "Hotkeys unregistered. Disconnected."
}
