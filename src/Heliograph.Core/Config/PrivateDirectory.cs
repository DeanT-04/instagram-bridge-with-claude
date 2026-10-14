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
        Directory.CreateDirectory(path, OwnerOnly);
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
