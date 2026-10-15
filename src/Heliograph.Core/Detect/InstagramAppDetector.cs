using Heliograph.Core.Eye;
using System.ComponentModel;
using System.Text.Json;

namespace Heliograph.Core.Detect;

/// <summary>Detects the Microsoft Store Instagram app on Windows and whether its window is open.</summary>
public sealed class InstagramAppDetector(DetectionSeams seams)
{
    public const string StoreProductId = "9NBLGGH5L9XT";
    public const string StoreUri = $"ms-windows-store://pdp/?ProductId={StoreProductId}";

    private const string DefaultFamily = "Facebook.InstagramBeta_8xx8rvfyw5nnt";
    private const string Query =
        "Get-AppxPackage -Name Facebook.InstagramBeta | Select-Object Name,Version,PackageFamilyName,InstallLocation | ConvertTo-Json -Compress";
    private const double NotInstalledTtlSeconds = 60;

    private static readonly TimeSpan QueryTimeout = TimeSpan.FromSeconds(30);

    private readonly object _cacheLock = new();
    private Package? _installed;
    private double? _notInstalledAt;

    private sealed record Package(string? Version, string? Family);

    private sealed record QueryResult(Package? Package, string? Error);

    public InstagramAppInfo Detect(bool checkWindow = true) =>
        Spans.Run(_ => DetectCore(checkWindow), "detect.instagram_app", [("check_window", checkWindow)]);

    private InstagramAppInfo DetectCore(bool checkWindow)
    {
        if (seams.Platform != DetectionPlatform.Windows)
        {
            return new InstagramAppInfo(false, false, null, null, null, null, "Windows only");
        }

        var query = Lookup();
        if (query.Package is null)
        {
            return new InstagramAppInfo(true, false, null, null, null, null, query.Error ?? "not installed");
        }

        var package = query.Package;
        var aumid = $"{package.Family ?? DefaultFamily}!App";
        if (!checkWindow)
        {
            return new InstagramAppInfo(true, true, package.Version, aumid, null, null, null);
        }

        var window = FindWindow(aumid);
        return new InstagramAppInfo(true, true, package.Version, aumid, window.Open, window.Title, window.Reason);
    }

    /// <summary>Forgets the cached Get-AppxPackage answer, for tests or after installing the app.</summary>
    public void ClearCache()
    {
        lock (_cacheLock)
        {
            _installed = null;
            _notInstalledAt = null;
        }
    }

    /// <summary>Windows owned by the app's AUMID. The seam returns only those, never a title match.</summary>
    public WindowCheck FindWindow(string aumid) =>
        Spans.Run(_ => FindWindowCore(aumid), "detect.instagram_window");

    private WindowCheck FindWindowCore(string aumid)
    {
        IReadOnlyList<string> titles;
        try
        {
            titles = seams.Window.FindWindowTitles(aumid);
        }
        catch (Exception error)
        {
            Spans.Current?.Set("window_error", error.Message).Fail(error);
            return new WindowCheck(null, null, $"window check unavailable: {error.Message}");
        }

        return titles.Count == 0 ? new WindowCheck(false, null, null) : new WindowCheck(true, titles[0], null);
    }

    public string Install()
    {
        if (seams.Platform != DetectionPlatform.Windows)
        {
            return "Instagram runs in the web app on this platform: open https://www.instagram.com";
        }
        try
        {
            seams.Shell.Open(StoreUri);
            return $"Opened the Microsoft Store page for Instagram ({StoreUri}).";
        }
        catch (Exception)
        {
            return $"Open manually: {StoreUri}";
        }
    }

    private QueryResult Lookup()
    {
        lock (_cacheLock)
        {
            var now = seams.Clock();
            if (_installed is not null)
            {
                return new QueryResult(_installed, null);
            }
            if (_notInstalledAt is { } stamp && now - stamp <= NotInstalledTtlSeconds)
            {
                return new QueryResult(null, null);
            }

            var result = RunQuery();
            if (result.Error is not null)
            {
                Spans.Current?.Set("query_error", result.Error).Fail(new InvalidOperationException(result.Error));
                return result;
            }
            if (result.Package is null)
            {
                _notInstalledAt = now;
                return result;
            }
            _installed = result.Package;
            return result;
        }
    }

    private QueryResult RunQuery()
    {
        try
        {
            var run = seams.Runner.Run(PowerShellPath(), ["-NoProfile", "-NonInteractive", "-Command", Query], QueryTimeout);
            if (run.ExitCode != 0)
            {
                return new QueryResult(null, $"Get-AppxPackage failed: {Trim(run.Stderr)}");
            }
            return string.IsNullOrWhiteSpace(run.Stdout)
                ? new QueryResult(null, null)
                : new QueryResult(ParsePackage(run.Stdout), null);
        }
        catch (Win32Exception)
        {
            return new QueryResult(null, "powershell not found");
        }
        catch (FileNotFoundException)
        {
            return new QueryResult(null, "powershell not found");
        }
        catch (TimeoutException)
        {
            return new QueryResult(null, "Get-AppxPackage timed out");
        }
        catch (Exception error)
        {
            return new QueryResult(null, $"Get-AppxPackage failed: {Trim(error.Message)}");
        }
    }

    /// <summary>The first registered package, or null when the answer holds no object.</summary>
    private static Package? ParsePackage(string json)
    {
        using var document = JsonDocument.Parse(json);
        var root = document.RootElement;
        if (root.ValueKind == JsonValueKind.Array)
        {
            root = root.EnumerateArray().FirstOrDefault();
        }
        if (root.ValueKind != JsonValueKind.Object)
        {
            return null;
        }
        return new Package(Text(root, "Version"), Text(root, "PackageFamilyName"));
    }

    private static string? Text(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;

    private string PowerShellPath()
    {
        var root = seams.Env.Get("SYSTEMROOT");
        var driveRooted = root is { Length: >= 3 } && char.IsAsciiLetter(root[0]) && root[1] == ':';
        if (!driveRooted || !PlatformPath.IsAbsolute(root!, windows: true))
        {
            root = @"C:\Windows";
        }
        return PlatformPath.Join(root!, @"System32\WindowsPowerShell\v1.0\powershell.exe", windows: true);
    }

    private static string Trim(string text)
    {
        var trimmed = text.Trim();
        return trimmed.Length > 200 ? trimmed[..200] : trimmed;
    }
}
