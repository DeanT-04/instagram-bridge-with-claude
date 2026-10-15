namespace Heliograph.Core.Detect;

/// <summary>Path rules that depend on the target platform, not on the host running the tests.</summary>
public static class PlatformPath
{
    public static bool IsAbsolute(string path, bool windows)
    {
        if (windows)
        {
            return path.StartsWith(@"\\", StringComparison.Ordinal)
                || (path.Length >= 3 && char.IsAsciiLetter(path[0]) && path[1] == ':' && (path[2] == '\\' || path[2] == '/'));
        }
        return path.StartsWith('/');
    }

    public static string Join(string root, string child, bool windows)
    {
        var separator = windows ? '\\' : '/';
        return root.EndsWith(separator) ? root + child : root + separator + child;
    }
}
