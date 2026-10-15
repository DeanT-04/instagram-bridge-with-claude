using Heliograph.Core.Detect;
using Heliograph.Core.Tests.Eye;

namespace Heliograph.Core.Tests.Detect;

[Collection(ProcessStateCollection.Name)]
public sealed class BrowserDetectorTests : IDisposable
{
    private const string EdgeRegistry = @"C:\Edge\msedge.exe";
    private const string ChromeWin = @"C:\PF\Google\Chrome\Application\chrome.exe";

    private readonly EyeHome _eye = new();
    private readonly Rig _rig = new() { Platform = DetectionPlatform.Windows };

    public void Dispose() => _eye.Dispose();

    private IReadOnlyList<BrowserInfo> Find() => new BrowserDetector(_rig.Seams).FindBrowsers();

    private static (string Channel, string Path, string Source) Row(BrowserInfo b) => (b.Channel, b.Path, b.Source);

    [Fact]
    public void Windows_registry_is_tried_before_common_locations()
    {
        _rig.Registry.Values["msedge.exe"] = EdgeRegistry;
        _rig.Env.Set("PROGRAMFILES", @"C:\PF");
        _rig.Files.Existing.Add(EdgeRegistry);
        _rig.Files.Existing.Add(ChromeWin);

        var found = Find().Select(Row).ToArray();

        Assert.Equal(new[] { ("msedge", EdgeRegistry, "registry"), ("chrome", ChromeWin, "common-location") }, found);
    }

    [Fact]
    public void Program_files_x86_is_searched_before_program_files()
    {
        _rig.Env.Set("PROGRAMFILES(X86)", @"C:\X86").Set("PROGRAMFILES", @"C:\PF");
        _rig.Files.Existing.Add(@"C:\X86\Google\Chrome\Application\chrome.exe");
        _rig.Files.Existing.Add(ChromeWin);

        var chrome = Find().Single();

        Assert.Equal(@"C:\X86\Google\Chrome\Application\chrome.exe", chrome.Path);
    }

    [Fact]
    public void Local_app_data_holds_edge_when_program_files_does_not()
    {
        _rig.Env.Set("LOCALAPPDATA", @"C:\Users\Deano\AppData\Local");
        _rig.Files.Existing.Add(@"C:\Users\Deano\AppData\Local\Microsoft\Edge\Application\msedge.exe");

        var edge = Find().Single();

        Assert.Equal(("msedge", @"C:\Users\Deano\AppData\Local\Microsoft\Edge\Application\msedge.exe", "common-location"), Row(edge));
    }

    [Fact]
    public void Chrome_is_chosen_when_edge_is_absent()
    {
        _rig.Env.Set("PROGRAMFILES", @"C:\PF");
        _rig.Files.Existing.Add(ChromeWin);

        var found = Find();

        Assert.Equal("chrome", Assert.Single(found).Channel);
    }

    [Fact]
    public void Edge_comes_before_chrome_in_the_result()
    {
        _rig.Env.Set("PROGRAMFILES", @"C:\PF");
        _rig.Files.Existing.Add(ChromeWin);
        _rig.Files.Existing.Add(@"C:\PF\Microsoft\Edge\Application\msedge.exe");

        var channels = Find().Select(b => b.Channel).ToArray();

        Assert.Equal(new[] { "msedge", "chrome" }, channels);
    }

    [Fact]
    public void A_broken_registry_key_degrades_to_common_locations_without_throwing()
    {
        _rig.Registry.Broken = true;
        _rig.Env.Set("PROGRAMFILES", @"C:\PF");
        _rig.Files.Existing.Add(ChromeWin);

        var found = Find();

        Assert.Equal(("chrome", ChromeWin, "common-location"), Row(Assert.Single(found)));
    }

    [Fact]
    public void Linux_finds_chromium_on_path()
    {
        _rig.Platform = DetectionPlatform.Linux;
        _rig.Env.Set("PATH", "/usr/bin:/bin");
        _rig.Files.Existing.Add("/usr/bin/chromium");

        var found = Find().Select(Row).ToArray();

        Assert.Equal(new[] { ("chromium", "/usr/bin/chromium", "path") }, found);
    }

    [Fact]
    public void Linux_ignores_relative_path_entries()
    {
        _rig.Platform = DetectionPlatform.Linux;
        _rig.Env.Set("PATH", ".:relative:/usr/bin");
        _rig.Files.Existing.Add("./chromium");
        _rig.Files.Existing.Add("relative/chromium");

        Assert.Empty(Find());
    }

    [Fact]
    public void Macos_resolves_browsers_under_applications()
    {
        _rig.Platform = DetectionPlatform.MacOS;
        const string chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
        _rig.Files.Existing.Add(chrome);

        Assert.Equal(("chrome", chrome, "common-location"), Row(Assert.Single(Find())));
    }

    [Fact]
    public void No_browser_present_returns_empty()
    {
        Assert.Empty(Find());
    }
}
