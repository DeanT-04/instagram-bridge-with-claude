using Heliograph.Core.Config;
using Heliograph.Core.Detect;
using Heliograph.Core.Detect.Production;

internal static class DetectCommands
{
    public static int Run()
    {
        var settings = HeliographSettings.Current;
        var detector = new EnvironmentDetector(settings, SystemSeams.ForCurrentProcess());
        var report = detector.DetectAsync().GetAwaiter().GetResult();
        Print("os", report.Os);
        Print("browser", report.PreferredBrowser is null ? "none" : $"{report.PreferredBrowser.Channel} {report.PreferredBrowser.Path}");
        Print("ffmpeg", report.Ffmpeg.Found ? report.Ffmpeg.Path : $"missing: {report.Ffmpeg.Reason}");
        Print("ffprobe", report.Ffprobe.Found ? report.Ffprobe.Path : "missing");
        Print("whisper_model", report.WhisperModel.Found ? report.WhisperModel.Path : $"missing: {report.WhisperModel.Reason}");
        Print("instagram_app", report.InstagramApp.Installed ? $"{report.InstagramApp.Version} {report.InstagramApp.Aumid}" : "missing");
        Print("browser_profile_initialized", report.BrowserProfileInitialized);
        Print("logged_in", report.LoggedIn);
        Print("needs_login", report.NeedsLogin);
        Print("ok", report.Ok);
        return 0;
    }

    private static void Print(string key, object? value) =>
        Console.WriteLine($"{key}={(value is bool b ? (b ? "true" : "false") : value?.ToString() ?? "null")}");
}
