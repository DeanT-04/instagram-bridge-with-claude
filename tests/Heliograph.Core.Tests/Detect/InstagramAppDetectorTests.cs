using System.ComponentModel;
using Heliograph.Core.Detect;
using Heliograph.Core.Tests.Eye;

namespace Heliograph.Core.Tests.Detect;

[Collection(ProcessStateCollection.Name)]
public sealed class InstagramAppDetectorTests : IDisposable
{
    private const string PackageJson =
        "{\"Name\":\"Facebook.InstagramBeta\",\"Version\":\"42.0.23.0\"," +
        "\"PackageFamilyName\":\"Facebook.InstagramBeta_8xx8rvfyw5nnt\"," +
        "\"InstallLocation\":\"C:\\\\Program Files\\\\WindowsApps\\\\Facebook.InstagramBeta_42\"}";
    private const string StoreUri = "ms-windows-store://pdp/?ProductId=9NBLGGH5L9XT";
    private const string PowerShellExe = @"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe";

    private readonly EyeHome _eye = new();
    private readonly Rig _rig = new() { Platform = DetectionPlatform.Windows };

    public void Dispose() => _eye.Dispose();

    private InstagramAppDetector Detector() => new(_rig.Seams);

    private void ReturnsStdout(string stdout) =>
        _rig.Runner.Handler = (_, _) => new ProcessResult(0, stdout, "");

    [Fact]
    public void Installed_app_reports_version_aumid_and_open_window()
    {
        ReturnsStdout(PackageJson);
        _rig.Window.Titles = () => ["Instagram"];

        var info = Detector().Detect();

        Assert.True(info.Installed);
        Assert.Equal("42.0.23.0", info.Version);
        Assert.Equal("Facebook.InstagramBeta_8xx8rvfyw5nnt!App", info.Aumid);
        Assert.True(info.WindowOpen);
        Assert.Equal("Instagram", info.WindowTitle);
    }

    [Fact]
    public void Several_registered_versions_take_the_first()
    {
        ReturnsStdout($"[{PackageJson}]");

        Assert.True(Detector().Detect(checkWindow: false).Installed);
    }

    [Fact]
    public void Missing_app_reports_not_installed_without_a_window_check()
    {
        ReturnsStdout("");

        var info = Detector().Detect();

        Assert.True(info.Supported);
        Assert.False(info.Installed);
        Assert.Equal("not installed", info.Reason);
        Assert.Null(info.WindowOpen);
        Assert.Equal(0, _rig.Window.Calls);
    }

    [Fact]
    public void Check_window_false_skips_the_window_probe()
    {
        ReturnsStdout(PackageJson);

        var info = Detector().Detect(checkWindow: false);

        Assert.True(info.Installed);
        Assert.Null(info.WindowOpen);
        Assert.Equal(0, _rig.Window.Calls);
    }

    [Theory]
    [InlineData("powershell")]
    [InlineData("timeout")]
    [InlineData("badjson")]
    [InlineData("denied")]
    public void Query_failures_never_raise(string kind)
    {
        _rig.Runner.Handler = kind switch
        {
            "powershell" => (_, _) => throw new FileNotFoundException("powershell"),
            "timeout" => (_, _) => throw new TimeoutException("Get-AppxPackage timed out"),
            "badjson" => (_, _) => new ProcessResult(0, "{not json", ""),
            _ => (_, _) => new ProcessResult(1, "", "Access denied"),
        };

        var info = Detector().Detect();

        Assert.False(info.Installed);
        Assert.False(string.IsNullOrEmpty(info.Reason));
    }

    [Fact]
    public void Win32_failure_to_start_powershell_reports_not_found()
    {
        _rig.Runner.Handler = (_, _) => throw new Win32Exception(2);

        Assert.Equal("powershell not found", Detector().Detect().Reason);
    }

    [Fact]
    public void Non_windows_is_not_supported_and_runs_nothing()
    {
        _rig.Platform = DetectionPlatform.Linux;

        var info = Detector().Detect();

        Assert.False(info.Supported);
        Assert.False(info.Installed);
        Assert.Empty(_rig.Runner.Calls);
    }

    [Fact]
    public void Installed_package_is_cached_for_the_detector_lifetime()
    {
        ReturnsStdout(PackageJson);
        var detector = Detector();

        detector.Detect(checkWindow: false);
        _rig.Now += 3600;
        detector.Detect(checkWindow: false);

        Assert.Single(_rig.Runner.Calls);
    }

    [Fact]
    public void Not_installed_is_rechecked_after_the_ttl()
    {
        ReturnsStdout("");
        var detector = Detector();

        detector.Detect(checkWindow: false);
        _rig.Now += 30;
        detector.Detect(checkWindow: false);
        Assert.Single(_rig.Runner.Calls);

        _rig.Now += 31;
        detector.Detect(checkWindow: false);
        Assert.Equal(2, _rig.Runner.Calls.Count);
    }

    [Fact]
    public void Failures_are_never_cached()
    {
        var calls = 0;
        _rig.Runner.Handler = (_, _) =>
        {
            calls++;
            return calls == 1 ? throw new TimeoutException("slow") : new ProcessResult(0, PackageJson, "");
        };
        var detector = Detector();

        Assert.False(detector.Detect(checkWindow: false).Installed);
        Assert.True(detector.Detect(checkWindow: false).Installed);
    }

    [Fact]
    public void Powershell_is_invoked_by_absolute_path_from_systemroot()
    {
        _rig.Env.Set("SYSTEMROOT", @"C:\Windows");
        ReturnsStdout("");

        Detector().Detect(checkWindow: false);

        Assert.Equal(PowerShellExe, _rig.Runner.Calls.Single().File);
        Assert.Contains(_rig.Runner.Calls.Single().Arguments, a => a.Contains("Get-AppxPackage"));
    }

    [Fact]
    public void Relative_systemroot_is_not_trusted()
    {
        _rig.Env.Set("SYSTEMROOT", "relative");
        ReturnsStdout("");

        Detector().Detect(checkWindow: false);

        Assert.Equal(PowerShellExe, _rig.Runner.Calls.Single().File);
    }

    [Fact]
    public void Window_matcher_counts_only_app_windows()
    {
        ReturnsStdout(PackageJson);
        var detector = Detector();

        _rig.Window.Titles = () => [];
        Assert.Equal((false, (string?)null, (string?)null), ToTuple(detector.FindWindow()));

        _rig.Window.Titles = () => ["(1) Instagram"];
        Assert.Equal((true, "(1) Instagram", (string?)null), ToTuple(detector.FindWindow()));
    }

    [Fact]
    public void Missing_uia_reports_window_check_unavailable()
    {
        ReturnsStdout(PackageJson);
        _rig.Window.Titles = () => throw new PlatformNotSupportedException("no pywin32");

        var info = Detector().Detect();

        Assert.True(info.Installed);
        Assert.Null(info.WindowOpen);
        Assert.Contains("window check unavailable", info.Reason);
    }

    [Fact]
    public void Install_opens_the_store_page()
    {
        var message = Detector().Install();

        Assert.Equal(new[] { StoreUri }, _rig.Shell.Opened);
        Assert.Contains("Store", message);
    }

    [Fact]
    public void Install_failure_names_the_manual_fallback()
    {
        _rig.Shell.Failure = new IOException("no shell");

        var message = Detector().Install();

        Assert.Contains("Open manually", message);
        Assert.Contains(StoreUri, message);
    }

    [Fact]
    public void Install_off_windows_points_to_the_web_app()
    {
        _rig.Platform = DetectionPlatform.MacOS;

        Assert.Contains("web", Detector().Install().ToLowerInvariant());
        Assert.Empty(_rig.Shell.Opened);
    }

    private static (bool? Open, string? Title, string? Reason) ToTuple(WindowCheck check) =>
        (check.Open, check.Title, check.Reason);
}
