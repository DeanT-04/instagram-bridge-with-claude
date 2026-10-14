using System.Collections;
using System.Globalization;

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

/// <summary>
/// Settings from <c>HELIOGRAPH_*</c> environment variables, then <c>~/.heliograph/.env</c>.
/// A <c>.env</c> in the working directory is never read, so a cloned repo cannot redirect paths.
/// Loading creates no directories.
/// </summary>
public sealed record HeliographSettings
{
    private const string Prefix = "HELIOGRAPH_";

    private static readonly Lazy<HeliographSettings> CurrentSettings = new(FromEnvironment);

    private HeliographSettings()
    {
    }

    public static HeliographSettings Current => CurrentSettings.Value;

    public static string DefaultEnvFile => Path.Combine(UserHome, ".heliograph", ".env");

    private static string UserHome => Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

    public string Home { get; init; } = "";

    public string ProfilePath { get; init; } = "";

    public string DossierPath { get; init; } = "";

    public string EyePath { get; init; } = "";

    public string StateFile => Path.Combine(Home, "state.json");

    /// <summary>0 picks a random free port.</summary>
    public int CdpPort { get; init; }

    public BrowserChannel BrowserChannel { get; init; }

    /// <summary>The MCP server closes the browser it launched after this idle time. Zero means never.</summary>
    public TimeSpan BrowserIdle { get; init; }

    public string WhisperModel { get; init; } = "";

    /// <summary>Used instead of <see cref="WhisperModel"/> for non-English speech. Empty keeps it.</summary>
    public string WhisperModelNonEnglish { get; init; } = "";

    public bool WhisperTranslate { get; init; }

    public OcrEngine OcrEngine { get; init; }

    public TimeSpan ReadMinInterval { get; init; }

    public TimeSpan ReadJitter { get; init; }

    public TimeSpan WriteMinInterval { get; init; }

    public TimeSpan WriteJitter { get; init; }

    public long EyeMaxBytes { get; init; }

    public int EyeBackups { get; init; }

    /// <summary>Eye failure artifacts older than this are pruned. Zero keeps them.</summary>
    public TimeSpan ArtifactRetention { get; init; }

    public LogLevel LogLevel { get; init; }

    public static HeliographSettings FromEnvironment()
    {
        var environment = new Dictionary<string, string>();
        foreach (DictionaryEntry entry in Environment.GetEnvironmentVariables())
        {
            environment[(string)entry.Key] = (string?)entry.Value ?? "";
        }
        return Load(environment, DefaultEnvFile);
    }

    public static HeliographSettings Load(IReadOnlyDictionary<string, string> environment, string? envFile)
    {
        var values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (envFile is not null && File.Exists(envFile))
        {
            AddSettings(values, DotEnv.Read(envFile, environment));
        }
        AddSettings(values, environment);

        var field = new FieldReader(values);
        var home = field.Path("home") ?? Path.Combine(UserHome, ".heliograph");
        var settings = new HeliographSettings
        {
            Home = home,
            ProfilePath = field.Path("browser_profile_dir") ?? Path.Combine(home, "browser-profile"),
            DossierPath = field.Path("dossier_dir") ?? Path.Combine(home, "dossiers"),
            EyePath = field.Path("eye_dir") ?? Path.Combine(home, "eye"),
            CdpPort = (int)field.Integer("cdp_port", 0, min: 0, max: 65535),
            BrowserChannel = field.Choice("browser_channel", BrowserChannel.Auto),
            BrowserIdle = field.Duration("browser_idle_minutes", 15, TimeSpan.FromMinutes),
            WhisperModel = field.Text("whisper_model", "small"),
            WhisperModelNonEnglish = field.Text("whisper_model_non_english", "medium"),
            WhisperTranslate = field.Flag("whisper_translate", true),
            OcrEngine = field.Choice("ocr_engine", OcrEngine.Auto),
            ReadMinInterval = field.Duration("read_min_interval", 1.5, TimeSpan.FromSeconds),
            ReadJitter = field.Duration("read_jitter", 1.0, TimeSpan.FromSeconds),
            WriteMinInterval = field.Duration("write_min_interval", 20.0, TimeSpan.FromSeconds),
            WriteJitter = field.Duration("write_jitter", 10.0, TimeSpan.FromSeconds),
            EyeMaxBytes = field.Integer("eye_max_bytes", 10 * 1024 * 1024, min: 1),
            EyeBackups = (int)field.Integer("eye_backups", 5, min: 0, max: int.MaxValue),
            ArtifactRetention = field.Duration("artifact_retention_days", 7, TimeSpan.FromDays),
            LogLevel = field.Choice("log_level", LogLevel.Info, upperCase: true),
        };
        return field.Errors.Count == 0 ? settings : throw new SettingsValidationException(field.Errors);
    }

    private static void AddSettings(
        Dictionary<string, string> values, IEnumerable<KeyValuePair<string, string>> source)
    {
        foreach (var (key, value) in source)
        {
            if (key.StartsWith(Prefix, StringComparison.OrdinalIgnoreCase))
            {
                values[key[Prefix.Length..]] = value;
            }
        }
    }

    private sealed class FieldReader(Dictionary<string, string> values)
    {
        public List<string> Errors { get; } = [];

        public string Text(string name, string fallback) => values.GetValueOrDefault(name, fallback);

        public string? Path(string name)
        {
            if (!values.TryGetValue(name, out var raw))
            {
                return null;
            }
            if (raw == "~" || raw.StartsWith("~/", StringComparison.Ordinal) || raw.StartsWith("~\\", StringComparison.Ordinal))
            {
                raw = UserHome + raw[1..];
            }
            return System.IO.Path.GetFullPath(raw.Length == 0 ? "." : raw);
        }

        public long Integer(string name, long fallback, long min, long max = long.MaxValue)
        {
            if (!values.TryGetValue(name, out var raw))
            {
                return fallback;
            }
            if (!long.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var value))
            {
                return Fail(name, raw, "should be a valid integer", fallback);
            }
            if (value < min || value > max)
            {
                return Fail(name, raw, max == long.MaxValue ? $"should be at least {min}" : $"should be between {min} and {max}", fallback);
            }
            return value;
        }

        public TimeSpan Duration(string name, double fallback, Func<double, TimeSpan> unit)
        {
            if (!values.TryGetValue(name, out var raw))
            {
                return unit(fallback);
            }
            if (!double.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out var value))
            {
                return Fail(name, raw, "should be a valid number", unit(fallback));
            }
            if (!(value >= 0))
            {
                return Fail(name, raw, "should be greater than or equal to 0", unit(fallback));
            }
            try
            {
                return unit(value);
            }
            catch (OverflowException)
            {
                return Fail(name, raw, "should be a finite number of reasonable size", unit(fallback));
            }
        }

        public bool Flag(string name, bool fallback)
        {
            if (!values.TryGetValue(name, out var raw))
            {
                return fallback;
            }
            switch (raw.Trim().ToLowerInvariant())
            {
                case "1" or "on" or "t" or "true" or "y" or "yes":
                    return true;
                case "0" or "off" or "f" or "false" or "n" or "no":
                    return false;
                default:
                    return Fail(name, raw, "should be a valid boolean", fallback);
            }
        }

        public T Choice<T>(string name, T fallback, bool upperCase = false)
            where T : struct, Enum
        {
            if (!values.TryGetValue(name, out var raw))
            {
                return fallback;
            }
            var wanted = upperCase ? raw.ToUpperInvariant() : raw;
            foreach (var option in Enum.GetValues<T>())
            {
                if (Wire(option, upperCase) == wanted)
                {
                    return option;
                }
            }
            var options = string.Join(", ", Enum.GetValues<T>().Select(o => $"'{Wire(o, upperCase)}'"));
            return Fail(name, raw, $"should be one of {options}", fallback);
        }

        private static string Wire<T>(T option, bool upperCase)
            where T : struct, Enum =>
            upperCase ? option.ToString().ToUpperInvariant() : option.ToString().ToLowerInvariant();

        private T Fail<T>(string name, string raw, string problem, T fallback)
        {
            Errors.Add($"{name}: input {problem} (got '{raw}')");
            return fallback;
        }
    }
}

public sealed class SettingsValidationException(IReadOnlyList<string> errors)
    : Exception($"{errors.Count} invalid Heliograph setting(s):\n{string.Join('\n', errors)}")
{
    public IReadOnlyList<string> Errors { get; } = errors;
}
