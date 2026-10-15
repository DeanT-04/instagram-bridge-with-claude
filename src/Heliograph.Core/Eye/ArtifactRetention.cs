namespace Heliograph.Core.Eye;

public static class ArtifactRetention
{
    /// <summary>
    /// Deletes files under <paramref name="folder"/> older than <paramref name="maxAge"/>, then the folders that
    /// leaves empty. Returns the number of files removed. A zero <paramref name="maxAge"/> keeps everything.
    /// Never throws.
    /// </summary>
    public static int Prune(string folder, TimeSpan maxAge, DateTime? now = null)
    {
        if (maxAge <= TimeSpan.Zero || !Directory.Exists(folder))
        {
            return 0;
        }
        var cutoff = (now ?? DateTime.UtcNow) - maxAge;
        string[] entries;
        try
        {
            entries = Directory.GetFileSystemEntries(folder, "*", SearchOption.AllDirectories);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return 0;
        }
        var removed = 0;
        foreach (var path in entries.OrderByDescending(p => p.Count(c => c == Path.DirectorySeparatorChar)))
        {
            try
            {
                var attributes = File.GetAttributes(path);
                var isDirectory = attributes.HasFlag(FileAttributes.Directory);
                if (!isDirectory || attributes.HasFlag(FileAttributes.ReparsePoint))
                {
                    if (File.GetLastWriteTimeUtc(path) < cutoff)
                    {
                        if (isDirectory)
                        {
                            Directory.Delete(path);
                        }
                        else
                        {
                            File.Delete(path);
                        }
                        removed++;
                    }
                }
                else if (!Directory.EnumerateFileSystemEntries(path).Any())
                {
                    Directory.Delete(path);
                }
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
            }
        }
        return removed;
    }
}
