namespace Heliograph.Core.Config;

public static class PrivateDirectory
{
    private const UnixFileMode OwnerOnly = UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute;

    /// <summary>
    /// Creates <paramref name="path"/> and its missing parents. On POSIX every directory created here is
    /// mode 0700 and <paramref name="path"/> itself is set to 0700; existing parents are left alone.
    /// On Windows the user profile is already private, so it only creates.
    /// </summary>
    public static string Ensure(string path)
    {
        if (OperatingSystem.IsWindows())
        {
            Directory.CreateDirectory(path);
            return path;
        }
        // Directory.CreateDirectory(path, mode) gives only the leaf that mode, so create each missing level.
        var missing = new Stack<string>();
        for (var dir = Path.GetFullPath(path); dir is not null && !Directory.Exists(dir); dir = Path.GetDirectoryName(dir))
        {
            missing.Push(dir);
        }
        foreach (var dir in missing)
        {
            Directory.CreateDirectory(dir, OwnerOnly);
        }
        try
        {
            File.SetUnixFileMode(path, OwnerOnly);
        }
        catch (UnauthorizedAccessException)
        {
        }
        return path;
    }
}
