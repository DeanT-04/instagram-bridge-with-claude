using Heliograph.Core.Config;
using Heliograph.Core.Eye;

namespace Heliograph.Core.Detect;

/// <summary>Builds the environment report that `heliograph doctor` prints. Probes run in parallel and never throw.</summary>
public sealed class EnvironmentDetector(HeliographSettings settings, DetectionSeams seams)
{
    private const string FfmpegHint = "scoop install ffmpeg";

    private readonly BrowserDetector _browsers = new(seams);
    private readonly ToolDetector _tools = new(seams);
    private readonly WhisperModelDetector _whisper = new(seams);
    private readonly InstagramAppDetector _app = new(seams);

    public Task<EnvironmentReport> DetectAsync(bool checkWindow = true) =>
        Spans.RunAsync(_ => DetectCoreAsync(checkWindow), "detect.environment");

    private async Task<EnvironmentReport> DetectCoreAsync(bool checkWindow)
    {
        var initialized = seams.Files.DirectoryHasEntries(settings.ProfilePath);
        var loggedIn = LoginStateReader.Read(seams.Files.ReadText(settings.StateFile));

        var browsers = Task.Run(() => _browsers.FindBrowsers());
        var ffmpeg = Task.Run(() => _tools.Detect("ffmpeg", FfmpegHint));
        var ffprobe = Task.Run(() => _tools.Detect("ffprobe", FfmpegHint));
        var whisper = Task.Run(() => _whisper.Detect(settings.WhisperModel));
        var app = Task.Run(() => _app.Detect(checkWindow));
        await Task.WhenAll(browsers, ffmpeg, ffprobe, whisper, app);

        return new EnvironmentReport(
            Os: OsName(seams.Platform),
            Browsers: browsers.Result,
            Ffmpeg: ffmpeg.Result,
            Ffprobe: ffprobe.Result,
            WhisperModel: whisper.Result,
            InstagramApp: app.Result,
            BrowserProfileInitialized: initialized,
            LoggedIn: loggedIn);
    }

    private static string OsName(DetectionPlatform platform) => platform switch
    {
        DetectionPlatform.Windows => "windows",
        DetectionPlatform.MacOS => "macos",
        _ => "linux",
    };
}
