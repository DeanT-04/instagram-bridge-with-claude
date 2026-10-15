namespace Heliograph.Core.Detect;

public sealed record BrowserInfo(string Channel, string Path, string Source);

public sealed record ToolInfo(bool Found, string? Path, string? Version, string? Reason);

public sealed record InstagramAppInfo(
    bool Supported,
    bool Installed,
    string? Version,
    string? Aumid,
    bool? WindowOpen,
    string? WindowTitle,
    string? Reason);

public sealed record WindowCheck(bool? Open, string? Title, string? Reason);

public sealed record WhisperModelInfo(bool Found, string? Path, string? Reason);

public sealed record EnvironmentReport(
    string Os,
    IReadOnlyList<BrowserInfo> Browsers,
    ToolInfo Ffmpeg,
    ToolInfo Ffprobe,
    WhisperModelInfo WhisperModel,
    InstagramAppInfo InstagramApp,
    bool BrowserProfileInitialized,
    bool? LoggedIn)
{
    public bool NeedsLogin => !BrowserProfileInitialized || LoggedIn == false;

    public BrowserInfo? PreferredBrowser => Browsers.Count > 0 ? Browsers[0] : null;

    public IReadOnlyList<string> CriticalMissing => Browsers.Count > 0 ? [] : ["browser"];

    public bool Ok => CriticalMissing.Count == 0;
}
