using System.Globalization;
using System.Text.Json;
using Heliograph.Core;
using Heliograph.Core.Config;

try
{
    return args switch
    {
        ["acquire", var path, var interval, var jitter] => Acquire(path, Seconds(interval), Seconds(jitter)),
        ["settings"] => DumpSettings(HeliographSettings.Current),
        ["settings", var envFile] => DumpSettings(HeliographSettings.Load(ProcessEnvironment(), envFile)),
        ["try", var op] => Try(op),
        ["check"] => Check(),
        ["errors"] => DumpErrors(),
        ["eye", .. var rest] => EyeCommands.Run(rest),
        _ => Usage(),
    };
}
catch (SettingsValidationException e)
{
    Console.Error.WriteLine(e.Message);
    return 3;
}

static int Acquire(string path, TimeSpan interval, TimeSpan jitter)
{
    new WriteLimiter(HeliographSettings.Current, path, interval, jitter).TryAcquire();
    return 0;
}

static int Try(string op)
{
    var limiter = new WriteLimiter(HeliographSettings.Current, log: line => Console.WriteLine($"log: {line}"));
    try
    {
        limiter.TryAcquire(op);
        Console.WriteLine($"{op}: allowed; state {limiter.Path}");
        return 0;
    }
    catch (RateLimitedException e)
    {
        Console.WriteLine($"{e.ErrorType}: {e.Message}");
        Console.WriteLine($"retry_after: {e.RetryAfter?.TotalSeconds.ToString("0.000", CultureInfo.InvariantCulture)}");
        return 1;
    }
}

static int Check()
{
    var remaining = new WriteLimiter(HeliographSettings.Current).Remaining();
    Console.WriteLine(remaining.TotalSeconds.ToString("0.000", CultureInfo.InvariantCulture));
    return 0;
}

static int DumpErrors()
{
    const string Raised = "hint passed at the raise site";
    HeliographException[] errors =
    [
        new HeliographException(), new NotLoggedInException(), new AppNotInstalledException(),
        new BrowserNotFoundException(), new DriverUnavailableException(), new RateLimitedException(),
        new UnsafeActionException(), new DownloadBlockedException(), new ElementNotFoundException(),
        new InstagramApiException("unexpected status", status: 500, path: "/api/v1/x/"),
        new HeliographException(hint: Raised), new NotLoggedInException(hint: Raised),
        new AppNotInstalledException(hint: Raised), new BrowserNotFoundException(hint: Raised),
        new DriverUnavailableException(hint: Raised), new RateLimitedException(hint: Raised),
        new UnsafeActionException(hint: Raised), new DownloadBlockedException(hint: Raised),
        new ElementNotFoundException(hint: Raised), new InstagramApiException("unexpected status", hint: Raised),
    ];
    foreach (var error in errors)
    {
        try
        {
            error.TraceId = Guid.NewGuid().ToString("N")[..16];
            throw error;
        }
        catch (HeliographException e)
        {
            Console.WriteLine($"{e.ErrorType} | type={e.GetType().Name} | hint={e.Hint ?? "<none>"} | trace={e.TraceId} | message={e.Message}");
        }
    }
    return 0;
}

static int DumpSettings(HeliographSettings s)
{
    using var json = new Utf8JsonWriter(Console.OpenStandardOutput(), new JsonWriterOptions { Indented = true });
    json.WriteStartObject();
    json.WriteString("home", s.Home);
    json.WriteString("browser_profile_dir", s.ProfilePath);
    json.WriteString("dossier_dir", s.DossierPath);
    json.WriteString("eye_dir", s.EyePath);
    json.WriteNumber("cdp_port", s.CdpPort);
    json.WriteString("browser_channel", s.BrowserChannel.ToString().ToLowerInvariant());
    json.WriteNumber("browser_idle_minutes", s.BrowserIdle.TotalMinutes);
    json.WriteString("whisper_model", s.WhisperModel);
    json.WriteString("whisper_model_non_english", s.WhisperModelNonEnglish);
    json.WriteBoolean("whisper_translate", s.WhisperTranslate);
    json.WriteString("ocr_engine", s.OcrEngine.ToString().ToLowerInvariant());
    json.WriteNumber("read_min_interval", s.ReadMinInterval.TotalSeconds);
    json.WriteNumber("read_jitter", s.ReadJitter.TotalSeconds);
    json.WriteNumber("write_min_interval", s.WriteMinInterval.TotalSeconds);
    json.WriteNumber("write_jitter", s.WriteJitter.TotalSeconds);
    json.WriteNumber("eye_max_bytes", s.EyeMaxBytes);
    json.WriteNumber("eye_backups", s.EyeBackups);
    json.WriteNumber("artifact_retention_days", s.ArtifactRetention.TotalDays);
    json.WriteString("log_level", s.LogLevel.ToString().ToUpperInvariant());
    json.WriteString("state_file", s.StateFile);
    json.WriteEndObject();
    return 0;
}

static Dictionary<string, string> ProcessEnvironment() =>
    Environment.GetEnvironmentVariables().Cast<System.Collections.DictionaryEntry>()
        .ToDictionary(e => (string)e.Key, e => (string?)e.Value ?? "");

static TimeSpan Seconds(string value) => TimeSpan.FromSeconds(double.Parse(value, CultureInfo.InvariantCulture));

static int Usage()
{
    Console.Error.WriteLine("usage: Heliograph.Core.Probe settings [env-file] | check | try <op> | errors | acquire <state-file> <min-interval-s> <jitter-s>");
    Console.Error.WriteLine("       Heliograph.Core.Probe eye tree | error | parallel <n> | spans <n> <interval-ms> | bench <n>");
    Console.Error.WriteLine("       Heliograph.Core.Probe eye report <hours> | tail | crash | redact | payload | prune <folder> <days>");
    return 2;
}
