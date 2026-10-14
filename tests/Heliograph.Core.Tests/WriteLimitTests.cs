using System.Diagnostics;
using System.Text.Json;
using Heliograph.Core.Config;

namespace Heliograph.Core.Tests;

public sealed class WriteLimitTests : IDisposable
{
    private readonly TempDir _tmp = new();
    private readonly HeliographSettings _settings;
    private double _now = 1_000_000.0;

    public WriteLimitTests() =>
        _settings = HeliographSettings.Load(
            new Dictionary<string, string> { ["HELIOGRAPH_HOME"] = _tmp.Path("home") }, envFile: null);

    public void Dispose() => _tmp.Dispose();

    private WriteLimiter Limiter(
        string path, double minInterval, double jitter, double random = 0.5, TimeSpan? lockTimeout = null,
        Func<TimeSpan, Task>? sleep = null, Action<string>? log = null) =>
        new(_settings, path, TimeSpan.FromSeconds(minInterval), TimeSpan.FromSeconds(jitter),
            clock: () => _now, random: () => random, sleep: sleep, lockTimeout: lockTimeout, log: log);

    private static void Age(string path, double seconds) =>
        File.SetLastWriteTimeUtc(path, DateTime.UtcNow.AddSeconds(-seconds));

    [Fact]
    public void Two_limiters_on_one_file_share_the_budget()
    {
        var path = _tmp.Path("ratelimit.json");
        var a = Limiter(path, 20, 10);
        var b = Limiter(path, 20, 10);

        a.TryAcquire();
        var error = Assert.Throws<RateLimitedException>(() => b.TryAcquire("like"));

        Assert.Equal(25.0, error.RetryAfter!.Value.TotalSeconds, precision: 6);
        Assert.Equal(25.0, b.Remaining().TotalSeconds, precision: 6);
        _now += 25.0;
        b.TryAcquire();
        using var state = JsonDocument.Parse(File.ReadAllText(path));
        Assert.Equal(_now, state.RootElement.GetProperty("last").GetDouble());
    }

    [Fact]
    public void Refusal_names_the_operation_and_the_reset_time()
    {
        var path = _tmp.Path("ratelimit.json");
        Limiter(path, 20, 10).TryAcquire();

        var error = Assert.Throws<RateLimitedException>(() => Limiter(path, 20, 10).TryAcquire("like"));

        Assert.StartsWith("like: writes are rate limited", error.Detail, StringComparison.Ordinal);
        Assert.Contains("25.0 s", error.Hint, StringComparison.Ordinal);
        Assert.Contains("1970-01-12T13:47:05Z", error.Hint, StringComparison.Ordinal);
    }

    [Fact]
    public void State_file_keeps_the_python_json_shape()
    {
        var path = _tmp.Path("ratelimit.json");

        Limiter(path, 20, 10).TryAcquire();

        using var state = JsonDocument.Parse(File.ReadAllText(path));
        var names = state.RootElement.EnumerateObject().Select(p => p.Name).Order().ToArray();
        Assert.Equal(["last", "next_allowed"], names);
        Assert.Equal(1_000_025.0, state.RootElement.GetProperty("next_allowed").GetDouble());
        Assert.Equal(1_000_000.0, state.RootElement.GetProperty("last").GetDouble());
    }

    [Fact]
    public async Task Acquire_queues_behind_reserved_slots()
    {
        var slept = new List<double>();
        var limiter = Limiter(_tmp.Path("r.json"), 5, 0, sleep: wait =>
        {
            slept.Add(wait.TotalSeconds);
            return Task.CompletedTask;
        });

        Assert.Equal(TimeSpan.Zero, await limiter.AcquireAsync());
        Assert.Equal(5.0, (await limiter.AcquireAsync()).TotalSeconds, precision: 6);
        Assert.Equal(10.0, (await limiter.AcquireAsync()).TotalSeconds, precision: 6);
        Assert.Equal([5.0, 10.0], slept);
    }

    [Fact]
    public void Jitter_and_interval_come_from_settings()
    {
        var settings = HeliographSettings.Load(
            new Dictionary<string, string>
            {
                ["HELIOGRAPH_HOME"] = _tmp.Path("home"),
                ["HELIOGRAPH_WRITE_MIN_INTERVAL"] = "30",
                ["HELIOGRAPH_WRITE_JITTER"] = "4",
            },
            envFile: null);
        var limiter = new WriteLimiter(settings, clock: () => _now, random: () => 1.0);

        limiter.TryAcquire();

        Assert.Equal(Path.Combine(settings.Home, "ratelimit.json"), limiter.Path);
        Assert.Equal(34.0, limiter.Remaining().TotalSeconds, precision: 6);
    }

    [Fact]
    public void Corrupt_or_far_future_state_does_not_block_forever()
    {
        var path = _tmp.Path("r.json");
        var log = new List<string>();
        File.WriteAllText(path, "{not json");
        var limiter = Limiter(path, 1, 0, log: log.Add);

        limiter.TryAcquire();
        Assert.Contains(log, line => line.Contains("unreadable", StringComparison.Ordinal));

        File.WriteAllText(path, """{"next_allowed": 1e12}""");
        var error = Assert.Throws<RateLimitedException>(() => limiter.TryAcquire());
        Assert.True(error.RetryAfter <= TimeSpan.FromHours(24));
    }

    [Theory]
    [InlineData("[1, 2]")]
    [InlineData("null")]
    [InlineData("""{"next_allowed": null}""")]
    [InlineData("""{"last": 5}""")]
    [InlineData("")]
    public void Unusable_state_reads_as_an_empty_window(string content)
    {
        var path = _tmp.Path("r.json");
        File.WriteAllText(path, content);

        Assert.Equal(TimeSpan.Zero, Limiter(path, 1, 0).Remaining());
    }

    [Fact]
    public void Numeric_strings_in_the_state_are_honoured()
    {
        var path = _tmp.Path("r.json");
        File.WriteAllText(path, """{"next_allowed": "1000012.5"}""");

        Assert.Equal(12.5, Limiter(path, 1, 0).Remaining().TotalSeconds, precision: 6);
    }

    [Fact]
    public void Stale_lock_is_broken()
    {
        var lockPath = _tmp.Path("x.lock");
        File.WriteAllText(lockPath, "12345");
        Age(lockPath, 120);

        using (new FileLock(lockPath, TimeSpan.FromSeconds(1), staleAfter: TimeSpan.FromSeconds(30)).Hold())
        {
            Assert.True(File.Exists(lockPath));
        }

        Assert.False(File.Exists(lockPath));
    }

    [Fact]
    public void Held_lock_times_out_and_the_limiter_never_writes_without_it()
    {
        var lockPath = _tmp.Path("x.lock");
        File.WriteAllText(lockPath, "busy");

        Assert.Throws<TimeoutException>(
            () => new FileLock(lockPath, TimeSpan.FromSeconds(0.2), staleAfter: TimeSpan.FromSeconds(60)).Hold());

        var statePath = _tmp.Path("r.json");
        File.WriteAllText(statePath + ".lock", "busy");
        var error = Assert.Throws<RateLimitedException>(
            () => Limiter(statePath, 1, 0, lockTimeout: TimeSpan.FromSeconds(0.2)).TryAcquire());

        Assert.Equal(TimeSpan.FromSeconds(1), error.RetryAfter);
        Assert.False(File.Exists(statePath));
    }

    [Fact]
    public void Limit_holds_across_processes()
    {
        var path = _tmp.Path("ratelimit.json");
        var probe = Path.Combine(AppContext.BaseDirectory, "Heliograph.Core.Probe.dll");
        var dotnet = Environment.GetEnvironmentVariable("DOTNET_HOST_PATH") ?? "dotnet";
        using (var child = Process.Start(new ProcessStartInfo(dotnet, [probe, "acquire", path, "60", "0"])
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        })!)
        {
            Assert.True(child.WaitForExit(60_000), "probe timed out");
            Assert.True(child.ExitCode == 0, child.StandardError.ReadToEnd());
        }

        var limiter = new WriteLimiter(_settings, path, TimeSpan.FromSeconds(60), TimeSpan.Zero);
        var error = Assert.Throws<RateLimitedException>(() => limiter.TryAcquire());

        Assert.InRange(error.RetryAfter!.Value.TotalSeconds, 55.0, 60.0);
    }

    [Fact]
    public void Lock_holds_an_ownership_token_and_release_spares_foreign_locks()
    {
        var lockPath = _tmp.Path("x.lock");

        using (new FileLock(lockPath, TimeSpan.FromSeconds(1)).Hold())
        {
            var token = File.ReadAllText(lockPath).Split(':');
            Assert.Equal(Environment.ProcessId.ToString(System.Globalization.CultureInfo.InvariantCulture), token[0]);
            Assert.Equal(16, token[1].Length);
            File.WriteAllText(lockPath, "someone-else");
        }

        Assert.Equal("someone-else", File.ReadAllText(lockPath));
    }

    [Fact]
    public void Breaking_a_lock_that_was_just_replaced_puts_it_back()
    {
        var lockPath = _tmp.Path("x.lock");
        using (new FileLock(lockPath, TimeSpan.FromSeconds(1)).Hold())
        {
            var live = File.ReadAllText(lockPath);
            Age(lockPath, 120);
            var b = new FileLock(lockPath, TimeSpan.FromSeconds(0.2), staleAfter: TimeSpan.FromSeconds(30));

            b.BreakIfStale(observedToken: "999:stale-token");

            Assert.Equal(live, File.ReadAllText(lockPath));
            Assert.Empty(Directory.GetFiles(_tmp.Root, "*.stale"));
            File.SetLastWriteTimeUtc(lockPath, DateTime.UtcNow);
            Assert.Throws<TimeoutException>(() => new FileLock(lockPath, TimeSpan.FromSeconds(0.2)).Hold());
        }

        Assert.False(File.Exists(lockPath));
    }

    [Fact]
    public async Task Concurrent_breakers_of_a_stale_lock_never_both_hold_it()
    {
        var lockPath = _tmp.Path("x.lock");
        var inside = 0;
        var peak = 0;

        for (var round = 0; round < 15; round++)
        {
            File.WriteAllText(lockPath, "1:crashed");
            Age(lockPath, 120);
            using var barrier = new Barrier(6);
            var workers = Enumerable.Range(0, 6).Select(_ => Task.Factory.StartNew(() =>
            {
                barrier.SignalAndWait();
                var fileLock = new FileLock(
                    lockPath, TimeSpan.FromSeconds(5), TimeSpan.FromSeconds(30), TimeSpan.FromMilliseconds(1));
                using (fileLock.Hold())
                {
                    InterlockedMax(ref peak, Interlocked.Increment(ref inside));
                    Thread.Sleep(2);
                    Interlocked.Decrement(ref inside);
                }
            }, TaskCreationOptions.LongRunning)).ToArray();
            await Task.WhenAll(workers);

            Assert.False(File.Exists(lockPath));
        }

        Assert.Equal(1, peak);
        Assert.Empty(Directory.GetFiles(_tmp.Root, "*.stale"));
    }

    private static void InterlockedMax(ref int target, int value)
    {
        var seen = Volatile.Read(ref target);
        while (value > seen)
        {
            var previous = Interlocked.CompareExchange(ref target, value, seen);
            if (previous == seen)
            {
                return;
            }
            seen = previous;
        }
    }
}
