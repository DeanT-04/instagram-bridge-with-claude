namespace Heliograph.Core.Config;

public enum BrowserChannel
{
    Auto,
    MsEdge,
    Chrome,
    Chromium,
}

public enum OcrEngine
{
    Auto,
    RapidOcr,
    Windows,
    Off,
}

public enum LogLevel
{
    Debug,
    Info,
    Warning,
    Error,
    Critical,
}

public sealed record HeliographSettings
{
    private HeliographSettings()
    {
    }

    public static HeliographSettings Current { get; } = new();

    public static string DefaultEnvFile => "";

    public string Home { get; init; } = "";

    public string ProfilePath { get; init; } = "";

    public string DossierPath { get; init; } = "";

    public string EyePath { get; init; } = "";

    public string StateFile => Home;

    public int CdpPort { get; init; }

    public BrowserChannel BrowserChannel { get; init; }

    public TimeSpan BrowserIdle { get; init; }

    public string WhisperModel { get; init; } = "";

    public string WhisperModelNonEnglish { get; init; } = "";

    public bool WhisperTranslate { get; init; }

    public OcrEngine OcrEngine { get; init; }

    public TimeSpan ReadMinInterval { get; init; }

    public TimeSpan ReadJitter { get; init; }

    public TimeSpan WriteMinInterval { get; init; }

    public TimeSpan WriteJitter { get; init; }

    public long EyeMaxBytes { get; init; }

    public int EyeBackups { get; init; }

    public TimeSpan ArtifactRetention { get; init; }

    public LogLevel LogLevel { get; init; }

    public static HeliographSettings FromEnvironment() => new();

    public static HeliographSettings Load(IReadOnlyDictionary<string, string> environment, string? envFile) =>
        environment.Count + (envFile?.Length ?? 0) >= 0 ? new() : new();
}

public sealed class SettingsValidationException(IReadOnlyList<string> errors) : Exception(string.Join('\n', errors))
{
    public IReadOnlyList<string> Errors { get; } = errors;
}

public static class PrivateDirectory
{
    public static string Ensure(string path) => path;
}
