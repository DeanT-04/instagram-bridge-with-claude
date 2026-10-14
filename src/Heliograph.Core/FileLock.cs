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
    private const int RetryAttempts = 200;

    private static readonly UTF8Encoding StrictUtf8 = new(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true);

    private readonly TimeSpan _timeout = timeout ?? TimeSpan.FromSeconds(10);
    private readonly TimeSpan _staleAfter = staleAfter ?? TimeSpan.FromSeconds(30);
    private readonly TimeSpan _poll = poll ?? TimeSpan.FromMilliseconds(20);

    public string Path { get; } = path;

    /// <summary>Test seam. Runs on release once the lock is known to be ours, before it is removed.</summary>
    internal Action? OnOwnershipVerified { get; init; }

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

    internal void BreakIfStale(string? observedToken) => RemoveIfHeldBy(observedToken, "stale", attempts: 1);

    private void Release(string token) =>
        RemoveIfHeldBy(token, "released", RetryAttempts, OnOwnershipVerified);

    /// <summary>
    /// Moves the lock to a name no one else uses, then checks its token there. Checking at the lock path
    /// would leave a gap in which another process could replace the lock before we remove it.
    /// </summary>
    private void RemoveIfHeldBy(string? token, string kind, int attempts, Action? onVerified = null)
    {
        var tomb = $"{Path}.{Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(6))}.{kind}";
        if (!MoveAside(tomb, attempts))
        {
            return;
        }
        if (Read(tomb) == token)
        {
            onVerified?.Invoke();
            Unlink(tomb);
            return;
        }
        RestoreLiveLockUnlessReplaced(tomb);
    }

    private bool MoveAside(string tomb, int attempts)
    {
        for (var attempt = 1; ; attempt++)
        {
            try
            {
                File.Move(Path, tomb, overwrite: true);
                return true;
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                if (attempt >= attempts || !File.Exists(Path))
                {
                    return false;
                }
                Thread.Sleep(5);
            }
        }
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
        for (var attempt = 0; attempt < RetryAttempts; attempt++)
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
            owner.Release(token);
        }
    }
}
