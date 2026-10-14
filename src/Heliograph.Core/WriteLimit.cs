using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;
using Heliograph.Core.Config;

namespace Heliograph.Core;

/// <summary>
/// <c>minInterval + U(0, jitter)</c> between writes, shared by every Heliograph process through
/// <c>ratelimit.json</c> under a <see cref="FileLock"/>. A write reserves a slot: under the lock it reads
/// <c>next_allowed</c>, takes <c>max(now, next_allowed)</c> and stores <c>slot + gap</c>. The lock is
/// never held while sleeping, so concurrent writers queue in slot order.
/// </summary>
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
    private static readonly double FarFutureCap = TimeSpan.FromHours(24).TotalSeconds;

    private readonly TimeSpan _minInterval = minInterval ?? settings.WriteMinInterval;
    private readonly TimeSpan _jitter = jitter ?? settings.WriteJitter;
    private readonly Func<double> _clock = clock ?? (() => (DateTime.UtcNow - DateTime.UnixEpoch).TotalSeconds);
    private readonly Func<double> _random = random ?? Random.Shared.NextDouble;
    private readonly Func<TimeSpan, Task> _sleep = sleep ?? (wait => Task.Delay(wait));
    private readonly TimeSpan _lockTimeout = lockTimeout ?? TimeSpan.FromSeconds(10);

    public string Path { get; } = path ?? System.IO.Path.Combine(settings.Home, "ratelimit.json");

    /// <summary>Time until the next write is allowed, without reserving it.</summary>
    public TimeSpan Remaining() => TimeSpan.FromSeconds(Math.Max(0.0, ReadNextAllowed() - _clock()));

    /// <summary>Reserves a write slot now, or throws <see cref="RateLimitedException"/> without waiting.</summary>
    public void TryAcquire(string op = "write")
    {
        var (now, wait) = Reserve(block: false);
        if (wait <= 0)
        {
            return;
        }
        var resetAt = DateTime.UnixEpoch.AddSeconds(now + wait);
        throw new RateLimitedException(
            $"{op}: writes are rate limited (shared across Heliograph processes)",
            TimeSpan.FromSeconds(wait),
            hint: string.Create(
                CultureInfo.InvariantCulture,
                $"The next write is allowed in {wait:0.0} s, at {resetAt:yyyy-MM-dd'T'HH:mm:ss'Z'}."));
    }

    /// <summary>Reserves the next slot and waits for it. Returns the time waited.</summary>
    public async Task<TimeSpan> AcquireAsync()
    {
        var (_, seconds) = await Task.Run(() => Reserve(block: true));
        var wait = TimeSpan.FromSeconds(seconds);
        if (wait > TimeSpan.Zero)
        {
            log?.Invoke(string.Create(
                CultureInfo.InvariantCulture, $"ratelimit.wait limiter={name} seconds={seconds:0.000} shared=true"));
            await _sleep(wait);
        }
        return wait;
    }

    private (double Now, double Wait) Reserve(bool block)
    {
        PrivateDirectory.Ensure(System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(Path))!);
        try
        {
            using (new FileLock(Path + ".lock", _lockTimeout).Hold())
            {
                var now = _clock();
                // A far-future value (clock change, corrupted file) must not block forever.
                var nextAllowed = Math.Min(ReadNextAllowed(), now + FarFutureCap);
                var wait = Math.Max(0.0, nextAllowed - now);
                if (wait > 0 && !block)
                {
                    return (now, wait);
                }
                var slot = now + wait;
                Write(new WriteLimitState(slot + Gap(), slot));
                return (now, wait);
            }
        }
        catch (TimeoutException e)
        {
            throw new RateLimitedException(
                "the shared write limiter is busy", TimeSpan.FromSeconds(1), hint: e.Message, inner: e);
        }
    }

    private double Gap() => _minInterval.TotalSeconds + _jitter.TotalSeconds * _random();

    private double ReadNextAllowed()
    {
        byte[] bytes;
        try
        {
            bytes = File.ReadAllBytes(Path);
        }
        catch (Exception e) when (e is FileNotFoundException or DirectoryNotFoundException)
        {
            return 0.0;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return Unreadable(e.Message);
        }
        try
        {
            return JsonSerializer.Deserialize(bytes, WriteLimitJson.Default.WriteLimitState)?.NextAllowed ?? 0.0;
        }
        catch (JsonException e)
        {
            return Unreadable(e.Message);
        }
    }

    private double Unreadable(string reason)
    {
        log?.Invoke($"{Path} is unreadable ({reason}); treating the write window as empty");
        return 0.0;
    }

    private void Write(WriteLimitState state)
    {
        var tmp = $"{Path}.{Environment.ProcessId}.tmp";
        File.WriteAllBytes(tmp, JsonSerializer.SerializeToUtf8Bytes(state, WriteLimitJson.Default.WriteLimitState));
        File.Move(tmp, Path, overwrite: true);
    }
}

internal sealed record WriteLimitState(double? NextAllowed, double? Last);

[JsonSourceGenerationOptions(
    PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower,
    NumberHandling = JsonNumberHandling.AllowReadingFromString)]
[JsonSerializable(typeof(WriteLimitState))]
internal sealed partial class WriteLimitJson : JsonSerializerContext;
