using Heliograph.Core.Eye;
namespace Heliograph.Core.Detect;

/// <summary>Finds Edge, then Chrome, then Chromium. One entry per channel; never throws.</summary>
public sealed class BrowserDetector(DetectionSeams seams)
{
    private static readonly string[] Channels = ["msedge", "chrome", "chromium"];

    private static readonly Dictionary<string, string> WindowsExe = new()
    {
        ["msedge"] = "msedge.exe",
        ["chrome"] = "chrome.exe",
    };

    private static readonly Dictionary<string, string[]> WindowsRelative = new()
    {
        ["msedge"] = [@"Microsoft\Edge\Application\msedge.exe"],
        ["chrome"] = [@"Google\Chrome\Application\chrome.exe"],
    };

    private static readonly Dictionary<string, string[]> MacPaths = new()
    {
        ["msedge"] = ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
        ["chrome"] = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
        ["chromium"] = ["/Applications/Chromium.app/Contents/MacOS/Chromium"],
    };

    private static readonly Dictionary<string, string[]> PosixNames = new()
    {
        ["msedge"] = ["microsoft-edge", "microsoft-edge-stable"],
        ["chrome"] = ["google-chrome", "google-chrome-stable"],
        ["chromium"] = ["chromium", "chromium-browser"],
    };

    public IReadOnlyList<BrowserInfo> FindBrowsers() => Spans.Run(_ => FindBrowsersCore(), "detect.browsers");

    private IReadOnlyList<BrowserInfo> FindBrowsersCore()
    {
        var found = new List<BrowserInfo>();
        foreach (var channel in Channels)
        {
            var hit = FirstExisting(channel);
            if (hit is not null)
            {
                found.Add(new BrowserInfo(channel, hit.Value.Path, hit.Value.Source));
            }
        }
        return found;
    }

    private (string Path, string Source)? FirstExisting(string channel)
    {
        try
        {
            foreach (var (path, source) in Candidates(channel))
            {
                if (seams.Files.FileExists(path))
                {
                    return (path, source);
                }
            }
        }
        catch (Exception error)
        {
            Spans.Current?.Set($"{channel}_error", error.Message).Fail(error);
            return null;
        }
        return null;
    }

    private IEnumerable<(string Path, string Source)> Candidates(string channel) =>
        seams.Platform == DetectionPlatform.Windows ? WindowsCandidates(channel) : PosixCandidates(channel);

    private IEnumerable<(string Path, string Source)> WindowsCandidates(string channel)
    {
        if (!WindowsExe.TryGetValue(channel, out var exe))
        {
            yield break;
        }
        string? registered = null;
        try
        {
            registered = seams.Registry.AppPathDefault(exe);
        }
        catch (Exception)
        {
            registered = null;
        }
        var trimmed = registered?.Trim().Trim('"');
        if (!string.IsNullOrEmpty(trimmed) && PlatformPath.IsAbsolute(trimmed, windows: true)
            && string.Equals(trimmed[(trimmed.LastIndexOf('\\') + 1)..], exe, StringComparison.OrdinalIgnoreCase))
        {
            yield return (trimmed, "registry");
        }
        foreach (var variable in new[] { "PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA" })
        {
            var root = seams.Env.Get(variable);
            if (string.IsNullOrEmpty(root) || !PlatformPath.IsAbsolute(root, windows: true))
            {
                continue;
            }
            foreach (var relative in WindowsRelative[channel])
            {
                yield return (PlatformPath.Join(root, relative, windows: true), "common-location");
            }
        }
    }

    private IEnumerable<(string Path, string Source)> PosixCandidates(string channel)
    {
        if (seams.Platform == DetectionPlatform.MacOS)
        {
            foreach (var path in MacPaths.GetValueOrDefault(channel, []))
            {
                yield return (path, "common-location");
            }
        }
        foreach (var name in PosixNames.GetValueOrDefault(channel, []))
        {
            foreach (var entry in (seams.Env.Get("PATH") ?? "").Split(':'))
            {
                if (PlatformPath.IsAbsolute(entry, windows: false))
                {
                    yield return (PlatformPath.Join(entry, name, windows: false), "path");
                }
            }
        }
    }
}
