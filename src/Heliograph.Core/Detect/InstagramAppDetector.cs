using System.ComponentModel;
using System.Text.Json;

namespace Heliograph.Core.Detect;

/// <summary>Detects the Microsoft Store Instagram app on Windows and whether its window is open.</summary>
public sealed class InstagramAppDetector(DetectionSeams seams)
{
    public const string StoreProductId = "9NBLGGH5L9XT";
    public const string StoreUri = $"ms-windows-store://pdp/?ProductId={StoreProductId}";

    private const string PackageName = "Facebook.InstagramBeta";
    private const string DefaultFamily = "Facebook.InstagramBeta_8xx8rvfyw5nnt";
    private const string Query =
        "Get-AppxPackage -Name Facebook.InstagramBeta | Select-Object Name,Version,PackageFamilyName,InstallLocation | ConvertTo-Json -Compress";
    private const double NotInstalledTtlSeconds = 60;

    private static readonly TimeSpan QueryTimeout = TimeSpan.FromSeconds(30);

    private Package? _installed;
    private double? _notInstalledAt;

    private sealed record Package(string? Version, string? Family);

    private sealed record QueryResult(Package? Package, string? Error);

    public InstagramAppInfo Detect(bool checkWindow = true)
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

        var window = FindWindow();
        var reason = window.Reason;
        return new InstagramAppInfo(true, true, package.Version, aumid, window.Open, window.Title, reason);
    }

    public WindowCheck FindWindow()
    {
        IReadOnlyList<string> titles;
        try
        {
            titles = seams.Window.FindWindowTitles();
        }
        catch (Exception error)
        {
            return new WindowCheck(null, null, $"window check unavailable: {error.Message}");
        }

        var match = titles.FirstOrDefault(title => title.Contains("Instagram", StringComparison.OrdinalIgnoreCase));
        return match is null ? new WindowCheck(false, null, null) : new WindowCheck(true, match, null);
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

    private static Package ParsePackage(string json)
    {
        using var document = JsonDocument.Parse(json);
        var root = document.RootElement;
        if (root.ValueKind == JsonValueKind.Array)
        {
            root = root.EnumerateArray().First();
        }
        return new Package(Text(root, "Version"), Text(root, "PackageFamilyName"));
    }

    private static string? Text(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;

    private string PowerShellPath()
    {
        var root = seams.Env.Get("SYSTEMROOT");
        if (string.IsNullOrEmpty(root) || !PlatformPath.IsAbsolute(root, windows: true))
        {
            root = @"C:\Windows";
        }
        return PlatformPath.Join(root, @"System32\WindowsPowerShell\v1.0\powershell.exe", windows: true);
    }

    private static string Trim(string text)
    {
        var trimmed = text.Trim();
        return trimmed.Length > 200 ? trimmed[..200] : trimmed;
    }
}
