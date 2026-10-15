using System.Diagnostics;
using System.Globalization;
using Heliograph.Core.Eye;

internal static class EyeCommands
{
    public static int Run(string[] args) => args switch
    {
        ["tree"] => Tree(),
        ["error"] => Error(),
        ["parallel", var tasks] => Parallel(int.Parse(tasks, CultureInfo.InvariantCulture)),
        ["spans", var count, var intervalMs] => Write(int.Parse(count, CultureInfo.InvariantCulture), int.Parse(intervalMs, CultureInfo.InvariantCulture)),
        ["bench", var count] => Bench(int.Parse(count, CultureInfo.InvariantCulture)),
        ["report", var hours] => Report(double.Parse(hours, CultureInfo.InvariantCulture)),
        ["tail"] => Tail(),
        ["crash"] => Crash(),
        ["redact"] => RedactLines(),
        ["payload"] => Payload(),
        ["prune", var folder, var days] => Prune(folder, double.Parse(days, CultureInfo.InvariantCulture)),
        _ => 2,
    };

    private static int Tree()
    {
        Spans.Run(root =>
        {
            root.Set("lane", 1);
            Spans.Run(fetch =>
            {
                fetch.Set("url", "https://www.instagram.com/api/v1/feed/");
                Spans.Run(_ => { }, "lane.parse");
            }, "lane.fetch");
            try
            {
                Spans.Run(_ => throw new TimeoutException("selector timed out"), "lane.fail");
            }
            catch (TimeoutException)
            {
            }
            Spans.Event("lane.mark", [("step", 2)]);
        }, "lane.root");
        Console.WriteLine($"wrote span tree to {EyeRecorder.Current.Directory}");
        return 0;
    }

    private static int Error()
    {
        try
        {
            Spans.Run(_ => throw new InvalidOperationException("boom while fetching (Cookie sessionid=4242%3Asecret)"), "lane.error");
        }
        catch (InvalidOperationException e)
        {
            Console.WriteLine($"caught {e.GetType().Name}: {e.Message}");
        }
        return 0;
    }

    private static int Parallel(int tasks)
    {
        Task.WaitAll([.. Enumerable.Range(0, tasks).Select(i => Spans.RunAsync(async _ =>
        {
            for (var step = 0; step < 3; step++)
            {
                await Spans.RunAsync(async inner =>
                {
                    await Task.Delay(Random.Shared.Next(1, 15));
                    await Spans.RunAsync(_ => Task.Delay(Random.Shared.Next(1, 5)), $"task.{i}.step.leaf");
                }, $"task.{i}.step");
            }
        }, $"task.{i}"))]);
        var events = EyeRecorder.Current.Query(kind: EventKind.Span, limit: 10_000);
        var byId = events.ToDictionary(e => (string)e["span_id"]!);
        var mismatches = 0;
        foreach (var ev in events.Where(e => e["parent_id"] is not null))
        {
            var name = (string)ev["name"]!;
            var parent = byId[(string)ev["parent_id"]!];
            var expected = name.EndsWith(".leaf", StringComparison.Ordinal) ? name[..^5] : name[..name.LastIndexOf(".step", StringComparison.Ordinal)];
            var ok = (string?)parent["name"] == expected && (string?)parent["trace_id"] == (string?)ev["trace_id"];
            if (!ok)
            {
                mismatches++;
                Console.WriteLine($"MISMATCH {name} under {(string?)parent["name"]}");
            }
        }
        Console.WriteLine($"tasks={tasks} spans={events.Count} traces={events.Select(e => (string?)e["trace_id"]).Distinct().Count()} mismatches={mismatches}");
        return mismatches == 0 ? 0 : 1;
    }

    private static int Write(int count, int intervalMs)
    {
        for (var i = 0; i < count; i++)
        {
            using (Spans.Start("writer.span", ("i", i)))
            {
            }
            Console.WriteLine($"{DateTime.UtcNow:HH:mm:ss.fff} wrote writer.span i={i}");
            Thread.Sleep(intervalMs);
        }
        return 0;
    }

    private static int Bench(int count)
    {
        var recorder = EyeRecorder.Current;
        var clock = Stopwatch.StartNew();
        for (var i = 0; i < count; i++)
        {
            using var span = Spans.Start("bench.span", ("i", i), ("url", "https://www.instagram.com/p/abc/"));
        }
        clock.Stop();
        var rows = recorder.Query(name: "bench.span", limit: count + 1).Count;
        Console.WriteLine(string.Create(
            CultureInfo.InvariantCulture,
            $"spans={count} rows={rows} seconds={clock.Elapsed.TotalSeconds:0.000} rate={count / clock.Elapsed.TotalSeconds:0.0}"));
        return rows == count ? 0 : 1;
    }

    private static int Report(double hours)
    {
        Console.WriteLine(EyeReport.Build(TimeSpan.FromHours(hours), limit: 10).ToJson());
        return 0;
    }

    private static int Tail()
    {
        using var stop = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) =>
        {
            e.Cancel = true;
            stop.Cancel();
        };
        LiveTail.Tail(poll: TimeSpan.FromSeconds(0.5), cancellation: stop.Token).GetAwaiter().GetResult();
        return 0;
    }

    private static int Crash()
    {
        UncaughtExceptions.Install();
        Spans.Start("lane.crash.outer");
        throw new InvalidOperationException("probe crash on purpose");
    }

    private static int RedactLines()
    {
        while (Console.In.ReadLine() is { } line)
        {
            Console.WriteLine(Redaction.RedactText(line));
        }
        return 0;
    }

    private static int Payload()
    {
        using (Spans.Start(
            "lane.payload",
            ("headers", new Dictionary<string, object?>
            {
                ["Cookie"] = "sessionid=12345%3AsecretCookie; csrftoken=Csrf0123456789abcdef",
                ["X-CSRFToken"] = "Csrf0123456789abcdef",
                ["Authorization"] = "Bearer IGT:2:eyJkc191c2VyX2lkIjoiMTIzNDUifQ==",
                ["Accept"] = "application/json",
            }),
            ("cookies", new object?[] { new Dictionary<string, object?> { ["name"] = "sessionid", ["value"] = "12345%3AsecretCookie" } }),
            ("note", "retry after csrftoken=Csrf0123456789abcdef with Authorization: Bearer placeholder-token-value")))
        {
        }
        Console.WriteLine($"wrote lane.payload to {EyeRecorder.Current.Directory}");
        return 0;
    }

    private static int Prune(string folder, double days)
    {
        Console.WriteLine($"removed={ArtifactRetention.Prune(folder, TimeSpan.FromDays(days))}");
        return 0;
    }

}
