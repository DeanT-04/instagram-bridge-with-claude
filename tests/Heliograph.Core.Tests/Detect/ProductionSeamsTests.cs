using Heliograph.Core.Detect;
using Heliograph.Core.Detect.Production;

namespace Heliograph.Core.Tests.Detect;

public sealed class ProductionSeamsTests
{
    private const string Aumid = "Facebook.InstagramBeta_8xx8rvfyw5nnt!App";

    [Fact]
    public void Current_process_platform_matches_the_host()
    {
        var expected = OperatingSystem.IsWindows() ? DetectionPlatform.Windows
            : OperatingSystem.IsMacOS() ? DetectionPlatform.MacOS
            : DetectionPlatform.Linux;
        Assert.Equal(expected, SystemSeams.ForCurrentProcess().Platform);
    }

    [Fact]
    public void Environment_reads_the_real_variables()
    {
        var seams = SystemSeams.ForCurrentProcess();
        Assert.Equal(Environment.GetEnvironmentVariable("PATH"), seams.Env.Get("PATH"));
        Assert.Null(seams.Env.Get("HELIOGRAPH_TEST_UNSET_VARIABLE_42"));
    }

    [Fact]
    public void Files_report_a_real_file_and_degrade_on_missing_paths()
    {
        var path = Path.GetTempFileName();
        try
        {
            File.WriteAllText(path, "{\"cdp\":{}}");
            var files = SystemSeams.ForCurrentProcess().Files;
            Assert.True(files.FileExists(path));
            Assert.Equal("{\"cdp\":{}}", files.ReadText(path));
        }
        finally
        {
            File.Delete(path);
        }

        var missing = Path.Combine(Path.GetTempPath(), "heliograph-missing-" + Guid.NewGuid().ToString("N"));
        var seams = SystemSeams.ForCurrentProcess().Files;
        Assert.False(seams.FileExists(missing));
        Assert.False(seams.DirectoryHasEntries(missing));
        Assert.Empty(seams.ListDirectories(missing));
        Assert.Null(seams.ReadText(missing));
    }

    [Fact]
    public void Process_runner_captures_stdout_and_exit_code()
    {
        var result = new ProcessRunner().Run("dotnet", ["--version"], TimeSpan.FromSeconds(60));
        Assert.Equal(0, result.ExitCode);
        Assert.False(string.IsNullOrWhiteSpace(result.Stdout));
    }

    [Fact]
    public void Process_runner_kills_and_throws_on_timeout()
    {
        Assert.Throws<TimeoutException>(() => new ProcessRunner().Run("dotnet", ["--info"], TimeSpan.FromMilliseconds(1)));
    }

    [Fact]
    public void Process_runner_throws_when_the_program_is_missing()
    {
        Assert.ThrowsAny<Exception>(() => new ProcessRunner().Run("heliograph-no-such-program-42", [], TimeSpan.FromSeconds(5)));
    }

    [Theory]
    [InlineData("ms-windows-store://pdp/?ProductId=9NBLGGH5L9XT", true)]
    [InlineData("ms-windows-store://pdp/?ProductId=", false)]
    [InlineData("ms-windows-store://pdp/?ProductId=9NBLGGH5L9XT&x=1", false)]
    [InlineData("https://www.instagram.com", false)]
    [InlineData("file:///C:/Windows/System32/calc.exe", false)]
    [InlineData("C:\\Windows\\System32\\calc.exe", false)]
    public void Shell_opens_only_the_store_page(string target, bool allowed) =>
        Assert.Equal(allowed, ShellLauncher.IsAllowed(target));

    [Fact]
    public void Shell_refuses_any_other_target_without_launching()
    {
        Assert.Throws<ArgumentException>(() => new ShellLauncher().Open("https://www.instagram.com"));
    }

    [Fact]
    public void Registry_returns_null_for_a_missing_or_path_like_exe()
    {
        var registry = new WindowsRegistry();
        Assert.Null(registry.AppPathDefault("heliograph-missing-42.exe"));
        Assert.Null(registry.AppPathDefault(@"..\msedge.exe"));
        Assert.Null(registry.AppPathDefault(""));
    }

    [Fact]
    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    public void Window_probe_returns_no_titles_when_no_window_matches_or_off_windows()
    {
        Assert.Empty(new AppWindowProbe().FindWindowTitles("Facebook.Heliograph-no-such-app_8xx!App"));
    }

    [Fact]
    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    public void Window_probe_rejects_an_aumid_without_a_package_family()
    {
        Assert.Empty(new AppWindowProbe().FindWindowTitles("no-bang-here"));
        Assert.Empty(new AppWindowProbe().FindWindowTitles(Aumid[(Aumid.IndexOf('!') + 1)..]));
    }

    [Fact]
    public void Bounded_read_drains_past_the_cap_and_keeps_the_first_chunk()
    {
        var text = ProcessRunner.ReadBounded(new StringReader(new string('x', ProcessRunner.MaxOutputChars * 3)));
        Assert.Equal(ProcessRunner.MaxOutputChars, text.Length);
    }
}
