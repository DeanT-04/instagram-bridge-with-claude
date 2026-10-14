using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;

namespace Heliograph.Core;

/// <summary>
/// An exclusive lock file usable across processes. A lock older than <c>staleAfter</c> is broken.
/// Release removes only our own lock.
/// </summary>
public sealed class FileLock(
    string path, TimeSpan? timeout = null, TimeSpan? staleAfter = null, TimeSpan? poll = null)
{
    private static readonly UTF8Encoding StrictUtf8 = new(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true);

    private readonly TimeSpan _timeout = timeout ?? TimeSpan.FromSeconds(10);
    private readonly TimeSpan _staleAfter = staleAfter ?? TimeSpan.FromSeconds(30);
    private readonly TimeSpan _poll = poll ?? TimeSpan.FromMilliseconds(20);

    public string Path { get; } = path;

    public IDisposable Hold()
    {
        var token = $"{Environment.ProcessId}:{Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(8))}";
        var waited = Stopwatch.StartNew();
        while (!TryCreate(token))
        {
            if (waited.Elapsed >= _timeout)
            {
                throw new TimeoutException($"could not acquire {Path} within {_timeout.TotalSeconds}s");
            }
            Thread.Sleep(_poll);
        }
        return new Holder(this, token);
    }

    internal void BreakIfStale(string? observedToken)
    {
        var tomb = $"{Path}.{Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(6))}.stale";
        try
        {
            File.Move(Path, tomb, overwrite: true);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return;
        }
        if (Read(tomb) == observedToken)
        {
            Unlink(tomb);
            return;
        }
        RestoreLiveLockUnlessReplaced(tomb);
    }

    private void RestoreLiveLockUnlessReplaced(string tomb)
    {
        try
        {
            File.Move(tomb, Path, overwrite: false);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            Unlink(tomb);
        }
    }

    private bool TryCreate(string token)
    {
        var options = new FileStreamOptions
        {
            Mode = FileMode.CreateNew,
            Access = FileAccess.Write,
            Share = FileShare.ReadWrite,
        };
        if (!OperatingSystem.IsWindows())
        {
            options.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
        }
        FileStream stream;
        try
        {
            stream = new FileStream(Path, options);
        }
        catch (UnauthorizedAccessException)
        {
            return false;
        }
        catch (IOException e) when (e is not DirectoryNotFoundException)
        {
            if (Read() == token)
            {
                return true;
            }
            BreakIfStale();
            return false;
        }
        using (stream)
        {
            stream.Write(Encoding.UTF8.GetBytes(token));
            stream.Flush(flushToDisk: true);
        }
        return Read() == token;
    }

    private void BreakIfStale()
    {
        var info = new FileInfo(Path);
        if (info.Exists && DateTime.UtcNow - info.LastWriteTimeUtc > _staleAfter)
        {
            BreakIfStale(Read());
        }
    }

    private string? Read(string? path = null)
    {
        try
        {
            using var stream = new FileStream(path ?? Path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            using var reader = new StreamReader(stream, StrictUtf8);
            return reader.ReadToEnd();
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or DecoderFallbackException)
        {
            return null;
        }
    }

    private static void Unlink(string path)
    {
        for (var attempt = 0; attempt < 200; attempt++)
        {
            try
            {
                File.Delete(path);
                return;
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                Thread.Sleep(5);
            }
        }
        File.Delete(path);
    }

    private sealed class Holder(FileLock owner, string token) : IDisposable
    {
        public void Dispose()
        {
            if (owner.Read() == token)
            {
                Unlink(owner.Path);
            }
        }
    }
}
