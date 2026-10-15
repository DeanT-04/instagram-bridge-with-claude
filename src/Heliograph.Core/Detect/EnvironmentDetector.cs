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
        var initialized = await Guard("browser_profile", () => seams.Files.DirectoryHasEntries(settings.ProfilePath), _ => false);
        var loggedIn = await Guard<bool?>("login_state",
            () => LoginStateReader.Read(seams.Files.ReadText(settings.StateFile)), _ => null);

        var browsers = Guard<IReadOnlyList<BrowserInfo>>("browsers", _browsers.FindBrowsers, _ => []);
        var ffmpeg = Guard("ffmpeg", () => _tools.Detect("ffmpeg", FfmpegHint), e => Missing("ffmpeg", e));
        var ffprobe = Guard("ffprobe", () => _tools.Detect("ffprobe", FfmpegHint), e => Missing("ffprobe", e));
        var whisper = Guard("whisper_model", () => _whisper.Detect(settings.WhisperModel),
            e => new WhisperModelInfo(false, null, e.Message));
        var app = Guard("instagram_app", () => _app.Detect(checkWindow),
            e => new InstagramAppInfo(true, false, null, null, null, null, e.Message));
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

    /// <summary>Runs one probe on the pool. A throw becomes the fallback and is noted on the environment span.</summary>
    private static Task<T> Guard<T>(string name, Func<T> probe, Func<Exception, T> fallback) =>
        Task.Run(() =>
        {
            try
            {
                return probe();
            }
            catch (Exception error)
            {
                Spans.Current?.Set($"{name}_error", error.Message);
                return fallback(error);
            }
        });

    private static ToolInfo Missing(string tool, Exception error) => new(false, null, null, $"{tool}: {error.Message}");

    private static string OsName(DetectionPlatform platform) => platform switch
    {
        DetectionPlatform.Windows => "windows",
        DetectionPlatform.MacOS => "macos",
        _ => "linux",
    };
}
