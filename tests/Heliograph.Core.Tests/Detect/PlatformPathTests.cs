using Heliograph.Core.Detect;

namespace Heliograph.Core.Tests.Detect;

public sealed class PlatformPathTests
{
    [Theory]
    [InlineData(@"C:\Windows", true)]
    [InlineData("c:/tools", true)]
    [InlineData(@"\\server\share", true)]
    [InlineData(@"relative\ffmpeg.exe", false)]
    [InlineData(".", false)]
    public void Windows_absolute_paths(string path, bool expected) =>
        Assert.Equal(expected, PlatformPath.IsAbsolute(path, windows: true));

    [Theory]
    [InlineData("/usr/bin", true)]
    [InlineData("relative/ffmpeg", false)]
    [InlineData(".", false)]
    public void Posix_absolute_paths(string path, bool expected) =>
        Assert.Equal(expected, PlatformPath.IsAbsolute(path, windows: false));

    [Fact]
    public void Join_uses_the_platform_separator_not_the_host()
    {
        Assert.Equal(@"C:\PF\Microsoft\Edge\msedge.exe",
            PlatformPath.Join(@"C:\PF", @"Microsoft\Edge\msedge.exe", windows: true));
        Assert.Equal(@"C:\PF\x", PlatformPath.Join(@"C:\PF\", "x", windows: true));
        Assert.Equal("/Applications/Chromium.app", PlatformPath.Join("/Applications", "Chromium.app", windows: false));
    }
}
