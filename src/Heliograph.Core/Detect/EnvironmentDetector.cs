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
        var initialized = Guard("browser_profile", () => seams.Files.DirectoryHasEntries(settings.ProfilePath), _ => false);
        var loggedIn = Guard<bool?>("login_state",
            () => LoginStateReader.Read(seams.Files.ReadText(settings.StateFile)), _ => null);

        var browsers = Guard<IReadOnlyList<BrowserInfo>>("browsers", _browsers.FindBrowsers, _ => []);
        var ffmpeg = Guard("ffmpeg", () => _tools.Detect("ffmpeg", FfmpegHint), e => Missing("ffmpeg", e));
        var ffprobe = Guard("ffprobe", () => _tools.Detect("ffprobe", FfmpegHint), e => Missing("ffprobe", e));
        var whisper = Guard("whisper_model", () => _whisper.Detect(settings.WhisperModel),
            e => new WhisperModelInfo(false, null, e.Message));
        var app = Guard("instagram_app", () => _app.Detect(checkWindow),
            e => new InstagramAppInfo(true, false, null, null, null, null, e.Message));
        await Task.WhenAll(initialized, loggedIn, browsers, ffmpeg, ffprobe, whisper, app);

        // Span writes happen here, on the flow that owns the environment span, never on the probe threads.
        var outcomes = new (string Name, Exception? Error)[]
        {
            (initialized.Result.Name, initialized.Result.Error),
            (loggedIn.Result.Name, loggedIn.Result.Error),
            (browsers.Result.Name, browsers.Result.Error),
            (ffmpeg.Result.Name, ffmpeg.Result.Error),
            (ffprobe.Result.Name, ffprobe.Result.Error),
            (whisper.Result.Name, whisper.Result.Error),
            (app.Result.Name, app.Result.Error),
        };
        foreach (var (name, error) in outcomes)
        {
            if (error is not null)
            {
                Spans.Current?.Set(name, error.Message).Fail(error);
            }
        }

        return new EnvironmentReport(
            Os: OsName(seams.Platform),
            Browsers: browsers.Result.Value,
            Ffmpeg: ffmpeg.Result.Value,
            Ffprobe: ffprobe.Result.Value,
            WhisperModel: whisper.Result.Value,
            InstagramApp: app.Result.Value,
            BrowserProfileInitialized: initialized.Result.Value,
            LoggedIn: loggedIn.Result.Value);
    }

    /// <summary>Runs one probe on the pool. A throw becomes the fallback and is returned for the caller to record.</summary>
    private static Task<Probed<T>> Guard<T>(string name, Func<T> probe, Func<Exception, T> fallback) =>
        Task.Run(() =>
        {
            try
            {
                return new Probed<T>(probe(), null, name);
            }
            catch (Exception error)
            {
                return new Probed<T>(fallback(error), error, $"{name}_error");
            }
        });

    private static ToolInfo Missing(string tool, Exception error) => new(false, null, null, $"{tool}: {error.Message}");

    private static string OsName(DetectionPlatform platform) => platform switch
    {
        DetectionPlatform.Windows => "windows",
        DetectionPlatform.MacOS => "macos",
        _ => "linux",
    };

    private sealed record Probed<T>(T Value, Exception? Error, string Name);
}
