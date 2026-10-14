namespace Heliograph.Core.Config;

/// <summary>
/// Python's <c>Path.resolve()</c>: an absolute path with every symlink and junction on it followed.
/// Components that do not exist yet are kept as written.
/// </summary>
internal static class RealPath
{
    private const int MaxLinksFollowed = 40;

    public static string Resolve(string path)
    {
        var pending = new Stack<string>();
        var current = Restart(Path.GetFullPath(path), pending);
        for (var followed = 0; pending.Count > 0;)
        {
            var next = Path.Combine(current, pending.Pop());
            var target = followed < MaxLinksFollowed ? FinalTarget(next) : null;
            if (target is null)
            {
                current = next;
                continue;
            }
            followed++;
            current = Restart(target, pending);
        }
        return current;
    }

    private static string Restart(string absolute, Stack<string> pending)
    {
        var root = Path.GetPathRoot(absolute)!;
        var parts = absolute[root.Length..].Split(
            [Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar], StringSplitOptions.RemoveEmptyEntries);
        for (var i = parts.Length - 1; i >= 0; i--)
        {
            pending.Push(parts[i]);
        }
        return root;
    }

    private static string? FinalTarget(string path)
    {
        try
        {
            var target = new FileInfo(path).ResolveLinkTarget(returnFinalTarget: true);
            return target is null ? null : Path.GetFullPath(target.FullName);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return null;
        }
    }
}
