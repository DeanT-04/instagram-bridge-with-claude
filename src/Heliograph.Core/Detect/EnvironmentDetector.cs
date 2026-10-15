using Heliograph.Core.Config;

namespace Heliograph.Core.Detect;

/// <summary>Builds the environment report that `heliograph doctor` prints. Every probe degrades instead of throwing.</summary>
public sealed class EnvironmentDetector(HeliographSettings settings, DetectionSeams seams)
{
    private const string FfmpegHint = "scoop install ffmpeg";

    private readonly BrowserDetector _browsers = new(seams);
    private readonly ToolDetector _tools = new(seams);
    private readonly WhisperModelDetector _whisper = new(seams);
    private readonly InstagramAppDetector _app = new(seams);

    public Task<EnvironmentReport> DetectAsync(bool checkWindow = true) => Task.FromResult(Detect(checkWindow));

    private EnvironmentReport Detect(bool checkWindow)
    {
        var initialized = seams.Files.DirectoryHasEntries(settings.ProfilePath);
        var loggedIn = LoginStateReader.Read(seams.Files.ReadText(settings.StateFile));

        return new EnvironmentReport(
            Os: OsName(seams.Platform),
            Browsers: _browsers.FindBrowsers(),
            Ffmpeg: _tools.Detect("ffmpeg", FfmpegHint),
            Ffprobe: _tools.Detect("ffprobe", FfmpegHint),
            WhisperModel: _whisper.Detect(settings.WhisperModel),
            InstagramApp: _app.Detect(checkWindow),
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
