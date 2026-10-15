using Heliograph.Core.Config;
using Heliograph.Core.Detect;

namespace Heliograph.Core.Tests.Detect;

public sealed class EnvironmentDetectorTests
{
    private const string PackageJson =
        "{\"Name\":\"Facebook.InstagramBeta\",\"Version\":\"42.0.23.0\",\"PackageFamilyName\":\"Facebook.InstagramBeta_8xx8rvfyw5nnt\"}";
    private const string ChromeWin = @"C:\PF\Google\Chrome\Application\chrome.exe";

    private readonly Rig _rig = new() { Platform = DetectionPlatform.Windows };
    private readonly HeliographSettings _settings;

    public EnvironmentDetectorTests()
    {
        _settings = HeliographSettings.Load(
            new Dictionary<string, string> { ["HELIOGRAPH_HOME"] = @"C:\Users\me\.heliograph" }, envFile: null);
    }


    private EnvironmentDetector Detector() => new(_settings, _rig.Seams);

    private void ServeProcesses()
    {
        _rig.Runner.Handler = (file, _) => file switch
        {
            var f when f.EndsWith("powershell.exe", StringComparison.OrdinalIgnoreCase) => new ProcessResult(0, PackageJson, ""),
            var f when f.Contains("ffprobe", StringComparison.OrdinalIgnoreCase) => new ProcessResult(0, "ffprobe version 7.1", ""),
            _ => new ProcessResult(0, "ffmpeg version 9.0", ""),
        };
    }

    [Fact]
    public async Task Nothing_installed_is_not_ok_and_needs_login()
    {
        _rig.Runner.Handler = (_, _) => new ProcessResult(0, "", "");

        var report = await Detector().DetectAsync(checkWindow: false);

        Assert.Equal("windows", report.Os);
        Assert.Equal(["browser"], report.CriticalMissing);
        Assert.False(report.Ok);
        Assert.False(report.BrowserProfileInitialized);
        Assert.Null(report.LoggedIn);
        Assert.True(report.NeedsLogin);
        Assert.False(report.Ffmpeg.Found);
        Assert.False(report.WhisperModel.Found);
    }

    [Fact]
    public async Task Full_environment_is_ok_and_reports_every_check()
    {
        _rig.Env.Set("PROGRAMFILES", @"C:\PF").Set("PATH", @"C:\ff").Set("USERPROFILE", @"C:\Users\me");
        _rig.Files.Existing.Add(ChromeWin);
        _rig.Files.Existing.Add(@"C:\ff\ffmpeg.exe");
        _rig.Files.Existing.Add(@"C:\ff\ffprobe.exe");
        _rig.Files.NonEmpty.Add(_settings.ProfilePath);
        _rig.Files.Texts[_settings.StateFile] = "{\"cdp\":{\"default\":{\"logged_in\":true}}}";
        _rig.Files.Existing.Add(@"C:\Users\me\.cache\huggingface\hub\models--Systran--faster-whisper-small\snapshots\abc\model.bin");
        _rig.Files.Children[@"C:\Users\me\.cache\huggingface\hub\models--Systran--faster-whisper-small\snapshots"] =
            [@"C:\Users\me\.cache\huggingface\hub\models--Systran--faster-whisper-small\snapshots\abc"];
        _rig.Window.Titles = () => ["Instagram"];
        ServeProcesses();

        var report = await Detector().DetectAsync();

        Assert.True(report.Ok);
        Assert.Empty(report.CriticalMissing);
        Assert.Equal("chrome", report.PreferredBrowser?.Channel);
        Assert.True(report.Ffmpeg.Found);
        Assert.True(report.Ffprobe.Found);
        Assert.True(report.WhisperModel.Found);
        Assert.True(report.InstagramApp.Installed);
        Assert.True(report.LoggedIn);
        Assert.False(report.NeedsLogin);
    }

    [Fact]
    public async Task Missing_ffmpeg_carries_the_scoop_hint()
    {
        _rig.Runner.Handler = (_, _) => new ProcessResult(0, "", "");

        var report = await Detector().DetectAsync(checkWindow: false);

        Assert.Contains("scoop install ffmpeg", report.Ffmpeg.Reason);
    }

    [Fact]
    public async Task Logged_out_profile_needs_login_even_when_initialized()
    {
        _rig.Files.NonEmpty.Add(_settings.ProfilePath);
        _rig.Files.Texts[_settings.StateFile] = "{\"cdp\":{\"default\":{\"logged_in\":false}}}";
        _rig.Runner.Handler = (_, _) => new ProcessResult(0, "", "");

        var report = await Detector().DetectAsync(checkWindow: false);

        Assert.False(report.LoggedIn ?? true);
        Assert.True(report.NeedsLogin);
    }

    [Fact]
    public async Task Second_detection_reuses_the_cached_app_query()
    {
        ServeProcesses();
        var detector = Detector();

        await detector.DetectAsync(checkWindow: false);
        await detector.DetectAsync(checkWindow: false);

        Assert.Single(_rig.Runner.Calls, c => c.File.EndsWith("powershell.exe", StringComparison.OrdinalIgnoreCase));
    }

    [Theory]
    [InlineData(null, null)]
    [InlineData("not json", null)]
    [InlineData("{\"cdp\":{\"default\":{\"logged_in\":\"yes\"}}}", null)]
    [InlineData("{\"cdp\":{\"default\":{\"logged_in\":true}}}", true)]
    [InlineData("{\"cdp\":{\"other\":{\"logged_in\":true}}}", null)]
    public void Login_state_is_read_from_state_json(string? json, bool? expected) =>
        Assert.Equal(expected, LoginStateReader.Read(json));
}
