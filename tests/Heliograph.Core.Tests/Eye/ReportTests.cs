using System.Text.Json.Nodes;
using Heliograph.Core.Eye;

namespace Heliograph.Core.Tests.Eye;

[Collection(ProcessStateCollection.Name)]
public sealed class ReportTests : IDisposable
{
    private readonly EyeHome _eye = new();

    public void Dispose() => _eye.Dispose();

    [Fact]
    public void Percentile()
    {
        Assert.Null(EyeReport.Percentile([], 50));
        Assert.Equal(5.0, EyeReport.Percentile([5.0], 95));
        Assert.Equal(2.5, EyeReport.Percentile([1, 2, 3, 4], 50));
        Assert.Equal(95.05, EyeReport.Percentile([.. Enumerable.Range(1, 100).Select(i => (double)i)], 95)!.Value, precision: 6);
    }

    [Fact]
    public void Report_summarises_errors_latency_and_anomalies()
    {
        for (var i = 0; i < 5; i++)
        {
            using (Spans.Start("cdp.saved"))
            {
            }
        }
        for (var i = 0; i < 4; i++)
        {
            Fail("uia.click");
        }

        var report = EyeReport.Build();

        Assert.Equal(9, report.Total);
        Assert.Equal(4, report.Errors);
        Assert.Equal(4.0 / 9, report.ErrorRate, tolerance: 1e-3);
        var ops = report.Ops.ToDictionary(o => o.Name);
        Assert.Equal(4, ops["uia.click"].Errors);
        Assert.Equal(1.0, ops["uia.click"].ErrorRate);
        Assert.NotNull(ops["cdp.saved"].P50Ms);
        Assert.NotNull(ops["cdp.saved"].P95Ms);
        Assert.Equal("uia.click", report.Ops[0].Name);
        var kinds = report.Anomalies.Select(a => (a.Kind, a.Name)).ToHashSet();
        Assert.Contains((AnomalyKind.RepeatedError, "uia.click"), kinds);
        Assert.Contains((AnomalyKind.HighFailureRate, "uia.click"), kinds);
        Assert.Equal(4, report.RecentErrors.Count);
        Assert.Equal("TimeoutException", report.RecentErrors[0].ErrorType);
        Assert.True(report.Slowest.Count <= 10);
    }

    [Fact]
    public void Report_empty_and_window()
    {
        var empty = EyeReport.Build();
        Assert.Equal(0, empty.Total);
        Assert.Equal(0.0, empty.ErrorRate);
        Assert.Empty(empty.Anomalies);

        using (Spans.Start("x"))
        {
        }

        Assert.Equal(0, EyeReport.Build(window: TimeSpan.FromSeconds(-10)).Total);
    }

    [Fact]
    public void Report_is_json_serialisable()
    {
        Fail("a");

        var json = EyeReport.Build().ToJson();

        Assert.Contains("\"recent_errors\"", json, StringComparison.Ordinal);
        Assert.Equal("TimeoutException", (string?)JsonNode.Parse(json)!["recent_errors"]![0]!["error_type"]);
    }

    [Fact]
    public void Anomaly_kinds_keep_their_python_names_in_json()
    {
        for (var i = 0; i < 4; i++)
        {
            Fail("uia.click");
        }

        var kinds = JsonNode.Parse(EyeReport.Build().ToJson())!["anomalies"]!.AsArray().Select(a => (string?)a!["kind"]);

        Assert.Equal(["repeated_error", "high_failure_rate"], kinds);
    }

    [Fact]
    public void Distinct_errors_are_not_flagged_as_repeated()
    {
        for (var i = 0; i < 3; i++)
        {
            Fail("net.get", $"error {i}");
        }

        Assert.DoesNotContain(EyeReport.Build().Anomalies, a => a.Kind == AnomalyKind.RepeatedError);
    }

    private static void Fail(string name, string message = "Timeout waiting for selector") =>
        Assert.Throws<TimeoutException>(() => Spans.Run(_ => throw new TimeoutException(message), name));
}
