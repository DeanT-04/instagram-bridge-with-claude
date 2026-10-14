using Heliograph.Core.Config;

namespace Heliograph.Core;

public sealed class FileLock(
    string path, TimeSpan? timeout = null, TimeSpan? staleAfter = null, TimeSpan? poll = null)
{
    public string Path { get; } = path + (timeout ?? staleAfter ?? poll)?.ToString();

    public IDisposable Hold() => new Released();

    internal void BreakIfStale(string? observedToken) => _ = observedToken;

    private sealed class Released : IDisposable
    {
        public void Dispose()
        {
        }
    }
}

public sealed class WriteLimiter(
    HeliographSettings settings,
    string? path = null,
    TimeSpan? minInterval = null,
    TimeSpan? jitter = null,
    Func<double>? clock = null,
    Func<double>? random = null,
    Func<TimeSpan, Task>? sleep = null,
    string name = "write",
    TimeSpan? lockTimeout = null,
    Action<string>? log = null)
{
    public string Path { get; } = path ?? settings.Home + (minInterval ?? jitter ?? lockTimeout)?.ToString()
        + (clock, random, sleep, name, log).ToString();

    public TimeSpan Remaining() => TimeSpan.Zero;

    public void TryAcquire(string op = "write") => _ = op;

    public Task<TimeSpan> AcquireAsync() => Task.FromResult(TimeSpan.Zero);
}
