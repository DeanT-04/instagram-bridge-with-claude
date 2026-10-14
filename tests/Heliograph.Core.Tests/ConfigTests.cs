using Heliograph.Core.Config;

namespace Heliograph.Core.Tests;

public sealed class ConfigTests : IDisposable
{
    private readonly TempDir _tmp = new();

    public void Dispose() => _tmp.Dispose();

    private Dictionary<string, string> Env(params (string Key, string Value)[] vars)
    {
        var env = new Dictionary<string, string> { ["HELIOGRAPH_HOME"] = _tmp.Path("home") };
        foreach (var (key, value) in vars)
        {
            env[key] = value;
        }
        return env;
    }

    [Fact]
    public void Defaults_derive_from_home()
    {
        var s = HeliographSettings.Load(Env(), envFile: null);

        Assert.Equal(_tmp.Path("home"), s.Home);
        Assert.Equal(Path.Combine(s.Home, "browser-profile"), s.ProfilePath);
        Assert.Equal(Path.Combine(s.Home, "dossiers"), s.DossierPath);
        Assert.Equal(Path.Combine(s.Home, "eye"), s.EyePath);
        Assert.Equal(Path.Combine(s.Home, "state.json"), s.StateFile);
        Assert.Equal(0, s.CdpPort);
        Assert.Equal(BrowserChannel.Auto, s.BrowserChannel);
        Assert.Equal("small", s.WhisperModel);
    }

    [Fact]
    public void Unset_variables_take_the_python_defaults()
    {
        var s = HeliographSettings.Load(new Dictionary<string, string>(), envFile: null);

        Assert.Equal(
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".heliograph"), s.Home);
        Assert.Equal(TimeSpan.FromMinutes(15), s.BrowserIdle);
        Assert.Equal("medium", s.WhisperModelNonEnglish);
        Assert.True(s.WhisperTranslate);
        Assert.Equal(OcrEngine.Auto, s.OcrEngine);
        Assert.Equal(TimeSpan.FromSeconds(1.5), s.ReadMinInterval);
        Assert.Equal(TimeSpan.FromSeconds(1), s.ReadJitter);
        Assert.Equal(TimeSpan.FromSeconds(20), s.WriteMinInterval);
        Assert.Equal(TimeSpan.FromSeconds(10), s.WriteJitter);
        Assert.Equal(10 * 1024 * 1024, s.EyeMaxBytes);
        Assert.Equal(5, s.EyeBackups);
        Assert.Equal(TimeSpan.FromDays(7), s.ArtifactRetention);
        Assert.Equal(LogLevel.Info, s.LogLevel);
    }

    [Fact]
    public void Dossier_dir_is_never_relative_to_the_working_directory()
    {
        var s = HeliographSettings.Load(new Dictionary<string, string>(), envFile: null);

        Assert.True(Path.IsPathFullyQualified(s.DossierPath));
        Assert.NotEqual(Path.GetFullPath("dossiers"), s.DossierPath);
    }

    [Fact]
    public void Env_overrides()
    {
        var s = HeliographSettings.Load(
            Env(
                ("HELIOGRAPH_CDP_PORT", "9333"),
                ("HELIOGRAPH_BROWSER_CHANNEL", "chrome"),
                ("HELIOGRAPH_DOSSIER_DIR", _tmp.Path("d")),
                ("HELIOGRAPH_LOG_LEVEL", "debug"),
                ("HELIOGRAPH_WRITE_MIN_INTERVAL", "30")),
            envFile: null);

        Assert.Equal(9333, s.CdpPort);
        Assert.Equal(BrowserChannel.Chrome, s.BrowserChannel);
        Assert.Equal(_tmp.Path("d"), s.DossierPath);
        Assert.Equal(LogLevel.Debug, s.LogLevel);
        Assert.Equal(TimeSpan.FromSeconds(30), s.WriteMinInterval);
    }

    [Fact]
    public void Env_names_ignore_case_and_paths_expand_the_user_home()
    {
        var s = HeliographSettings.Load(
            Env(("heliograph_eye_dir", "~/eye-elsewhere"), ("Heliograph_Whisper_Translate", "off")),
            envFile: null);

        var userHome = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        Assert.Equal(Path.Combine(userHome, "eye-elsewhere"), s.EyePath);
        Assert.False(s.WhisperTranslate);
    }

    [Fact]
    public void Explicit_env_file_is_read()
    {
        var envFile = _tmp.Path("user.env");
        File.WriteAllText(envFile, "HELIOGRAPH_WHISPER_MODEL=tiny\n");

        Assert.Equal("tiny", HeliographSettings.Load(Env(), envFile).WhisperModel);
    }

    [Fact]
    public void Process_env_beats_the_env_file()
    {
        var envFile = _tmp.Path("user.env");
        File.WriteAllText(envFile, "HELIOGRAPH_WHISPER_MODEL=tiny\nHELIOGRAPH_CDP_PORT=9000\n");

        var s = HeliographSettings.Load(Env(("HELIOGRAPH_WHISPER_MODEL", "base")), envFile);

        Assert.Equal("base", s.WhisperModel);
        Assert.Equal(9000, s.CdpPort);
    }

    [Fact]
    public void Env_file_accepts_dotenv_syntax()
    {
        var envFile = _tmp.Path("user.env");
        File.WriteAllText(
            envFile,
            """
            # comment line
            export HELIOGRAPH_WHISPER_MODEL = "large v3"
            HELIOGRAPH_WHISPER_MODEL_NON_ENGLISH='medium.en'
            HELIOGRAPH_CDP_PORT=9222 # inline comment
            OTHER_TOOL_SETTING=ignored
            HELIOGRAPH_NOT_A_SETTING=ignored
            """);

        var s = HeliographSettings.Load(Env(), envFile);

        Assert.Equal("large v3", s.WhisperModel);
        Assert.Equal("medium.en", s.WhisperModelNonEnglish);
        Assert.Equal(9222, s.CdpPort);
    }

    [Fact]
    public void Missing_env_file_is_fine()
    {
        var s = HeliographSettings.Load(Env(), _tmp.Path("absent.env"));

        Assert.Equal("small", s.WhisperModel);
    }

    [Fact]
    public void Default_env_file_is_the_users_own()
    {
        var userHome = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

        Assert.Equal(Path.Combine(userHome, ".heliograph", ".env"), HeliographSettings.DefaultEnvFile);
    }

    [Theory]
    [InlineData("HELIOGRAPH_BROWSER_CHANNEL", "firefox", "browser_channel")]
    [InlineData("HELIOGRAPH_CDP_PORT", "70000", "cdp_port")]
    [InlineData("HELIOGRAPH_READ_JITTER", "-1", "read_jitter")]
    [InlineData("HELIOGRAPH_CDP_PORT", "abc", "cdp_port")]
    [InlineData("HELIOGRAPH_WHISPER_TRANSLATE", "maybe", "whisper_translate")]
    [InlineData("HELIOGRAPH_EYE_MAX_BYTES", "0", "eye_max_bytes")]
    public void Invalid_values_are_rejected(string name, string value, string field)
    {
        var error = Assert.Throws<SettingsValidationException>(
            () => HeliographSettings.Load(Env((name, value)), envFile: null));

        Assert.Contains(error.Errors, e => e.StartsWith(field, StringComparison.Ordinal));
    }

    [Fact]
    public void Every_invalid_value_is_reported_at_once()
    {
        var error = Assert.Throws<SettingsValidationException>(
            () => HeliographSettings.Load(
                Env(("HELIOGRAPH_CDP_PORT", "-5"), ("HELIOGRAPH_OCR_ENGINE", "tesseract")), envFile: null));

        Assert.Equal(2, error.Errors.Count);
    }

    [Fact]
    public void Current_is_cached()
    {
        Assert.Same(HeliographSettings.Current, HeliographSettings.Current);
    }

    [Fact]
    public void No_dirs_are_created_on_load()
    {
        var s = HeliographSettings.Load(Env(), envFile: null);

        Assert.False(Directory.Exists(s.Home));
        Assert.False(Directory.Exists(s.ProfilePath));
        Assert.False(Directory.Exists(s.DossierPath));
    }

    [Fact]
    public void Ensure_private_dir_creates_the_path()
    {
        var p = PrivateDirectory.Ensure(_tmp.Path("a", "b"));

        Assert.True(Directory.Exists(p));
        Assert.Equal(_tmp.Path("a", "b"), p);
    }

    [Fact]
    public void Ensure_private_dir_creates_missing_parents_private()
    {
        var p = PrivateDirectory.Ensure(_tmp.Path("home", "eye"));

        Assert.True(Directory.Exists(p));
        if (!OperatingSystem.IsWindows())
        {
            Assert.Equal(UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute,
                File.GetUnixFileMode(_tmp.Path("home")));
            Assert.Equal(UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute,
                File.GetUnixFileMode(p));
        }
    }
}

[Collection(ProcessStateCollection.Name)]
public sealed class WorkingDirectoryConfigTests : IDisposable
{
    private readonly TempDir _tmp = new();
    private readonly string _cwd = Environment.CurrentDirectory;

    public void Dispose()
    {
        Environment.CurrentDirectory = _cwd;
        _tmp.Dispose();
    }

    [Fact]
    public void Working_directory_dotenv_is_ignored()
    {
        Environment.CurrentDirectory = _tmp.Root;
        File.WriteAllText(_tmp.Path(".env"), "HELIOGRAPH_WHISPER_MODEL=tiny\n");

        Assert.NotEqual("tiny", HeliographSettings.FromEnvironment().WhisperModel);
    }
}
