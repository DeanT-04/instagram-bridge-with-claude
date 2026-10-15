using Heliograph.Core.Eye;
using System.Text.RegularExpressions;

namespace Heliograph.Core.Detect;

/// <summary>Finds a command on PATH and reads its version. A failed version check keeps the tool found.</summary>
public sealed partial class ToolDetector(DetectionSeams seams)
{
    private static readonly TimeSpan VersionTimeout = TimeSpan.FromSeconds(10);

    public ToolInfo Detect(string name, string? hint = null) =>
        Spans.Run(_ => DetectCore(name, hint), "detect.tool", [("tool", name)]);

    private ToolInfo DetectCore(string name, string? hint)
    {
        var path = FindOnPath(name);
        if (path is null)
        {
            var reason = $"{name} not found on PATH";
            return new ToolInfo(false, null, null, hint is null ? reason : $"{reason}. {hint}");
        }

        try
        {
            var result = seams.Runner.Run(path, ["-version"], VersionTimeout);
            var output = string.IsNullOrWhiteSpace(result.Stdout) ? result.Stderr : result.Stdout;
            return new ToolInfo(true, path, ShortVersion(output), null);
        }
        catch (Exception error)
        {
            Spans.Current?.Set("version_error", error.Message).Fail(error);
            return new ToolInfo(true, path, null, $"version check failed: {error.Message}");
        }
    }

    private string? FindOnPath(string name)
    {
        var windows = seams.Platform == DetectionPlatform.Windows;
        var separator = windows ? ';' : ':';
        var entries = (seams.Env.Get("PATH") ?? "").Split(separator);
        var names = windows ? WindowsNames(name) : [name];

        foreach (var entry in entries)
        {
            var directory = entry.Trim().Trim('"');
            if (!PlatformPath.IsAbsolute(directory, windows))
            {
                continue;
            }
            foreach (var candidate in names)
            {
                var path = PlatformPath.Join(directory, candidate, windows);
                if (seams.Files.FileExists(path))
                {
                    return path;
                }
            }
        }
        return null;
    }

    private IEnumerable<string> WindowsNames(string name)
    {
        var extensions = (seams.Env.Get("PATHEXT") ?? ".EXE;.COM")
            .Split(';', StringSplitOptions.RemoveEmptyEntries)
            .Select(extension => extension.ToLowerInvariant())
            .Where(extension => extension is ".exe" or ".com");
        foreach (var extension in extensions)
        {
            yield return name + extension;
        }
    }

    internal static string ShortVersion(string output)
    {
        var firstLine = output.Split('\n')[0].Trim();
        var match = VersionPrefix().Match(firstLine);
        return match.Success ? match.Value : firstLine;
    }

    [GeneratedRegex(@"^\S+ version \S+")]
    private static partial Regex VersionPrefix();
}
