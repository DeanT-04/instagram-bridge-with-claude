using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace Heliograph.Core.Eye;

public sealed record OpStats(string Name, int Count, int Errors, double ErrorRate, double? P50Ms, double? P95Ms, double? MaxMs);

public sealed record ErrorSummary(string Ts, string Name, string? TraceId, string? ErrorType, string? Message, IReadOnlyList<Artifact> Artifacts);

[JsonConverter(typeof(JsonStringEnumConverter<AnomalyKind>))]
public enum AnomalyKind
{
    [JsonStringEnumMemberName("repeated_error")]
    RepeatedError,

    [JsonStringEnumMemberName("high_failure_rate")]
    HighFailureRate,
}

public sealed record Anomaly(AnomalyKind Kind, string Name, string Detail, int Count);

public sealed record SlowSpan(string Name, string Ts, double? DurationMs, string Status, string? TraceId);

public sealed record EyeReport(
    double WindowSeconds, int Total, int Errors, double ErrorRate, IReadOnlyList<OpStats> Ops,
    IReadOnlyList<SlowSpan> Slowest, IReadOnlyList<ErrorSummary> RecentErrors, IReadOnlyList<Anomaly> Anomalies)
{
    private const int MaxSpans = 50_000;

    /// <summary>Linear-interpolated percentile, <paramref name="pct"/> in 0..100; null when empty.</summary>
    public static double? Percentile(IReadOnlyList<double> values, double pct)
    {
        if (values.Count == 0)
        {
            return null;
        }
        var ordered = values.Order().ToArray();
        var k = (ordered.Length - 1) * pct / 100;
        var lo = (int)k;
        var hi = Math.Min(lo + 1, ordered.Length - 1);
        return Math.Round(ordered[lo] + ((ordered[hi] - ordered[lo]) * (k - lo)), 3);
    }

    /// <summary>Summarizes the spans of the last <paramref name="window"/> (default 24 hours).</summary>
    /// <param name="limit">Max entries in <see cref="Slowest"/> and <see cref="RecentErrors"/>.</param>
    public static EyeReport Build(TimeSpan? window = null, int limit = 10, EyeRecorder? recorder = null)
    {
        var span = window ?? TimeSpan.FromHours(24);
        var spans = (recorder ?? EyeRecorder.Current)
            .Query(since: DateTimeOffset.UtcNow - span, kind: EventKind.Span, limit: MaxSpans)
            .Select(SpanRow.From)
            .ToList();
        var ops = spans
            .GroupBy(s => s.Name)
            .Select(g =>
            {
                var durations = g.Where(s => s.DurationMs is not null).Select(s => s.DurationMs!.Value).ToList();
                var count = g.Count();
                var errors = g.Count(s => s.Failed);
                return new OpStats(
                    g.Key, count, errors, Math.Round((double)errors / count, 4),
                    Percentile(durations, 50), Percentile(durations, 95), durations.Count > 0 ? durations.Max() : null);
            })
            .OrderByDescending(o => o.Errors)
            .ThenByDescending(o => o.P95Ms ?? 0)
            .ToList();
        var slowest = spans
            .OrderByDescending(s => s.DurationMs ?? 0)
            .Take(limit)
            .Select(s => new SlowSpan(s.Name, s.Ts, s.DurationMs, s.Status, s.TraceId))
            .ToList();
        var recentErrors = spans
            .Where(s => s.Failed)
            .Take(limit)
            .Select(s => new ErrorSummary(s.Ts, s.Name, s.TraceId, s.ErrorType, s.ErrorMessage, s.Artifacts))
            .ToList();
        var errorCount = spans.Count(s => s.Failed);
        return new EyeReport(
            span.TotalSeconds, spans.Count, errorCount,
            spans.Count > 0 ? Math.Round((double)errorCount / spans.Count, 4) : 0.0,
            ops, slowest, recentErrors, FindAnomalies(spans, ops));
    }

    public string ToJson() => JsonSerializer.Serialize(this, EyeReportJson.Default.EyeReport);

    private static List<Anomaly> FindAnomalies(List<SpanRow> spans, List<OpStats> ops)
    {
        var repeated = spans
            .Where(s => s.Failed && s.HasError)
            .GroupBy(s => (s.Name, Type: s.ErrorType ?? "", Message: Redaction.Truncate(s.ErrorMessage ?? "", 120)))
            .Select(g => (g.Key, Count: g.Count()))
            .OrderByDescending(g => g.Count)
            .Where(g => g.Count >= 3)
            .Select(g => new Anomaly(AnomalyKind.RepeatedError, g.Key.Name, $"{g.Key.Type}: {g.Key.Message}", g.Count));
        var failing = ops
            .Where(o => o.Count >= 4 && o.ErrorRate >= 0.5)
            .Select(o => new Anomaly(AnomalyKind.HighFailureRate, o.Name, $"{o.Errors}/{o.Count} failed", o.Errors));
        return [.. repeated, .. failing];
    }

    private sealed record SpanRow(
        string Name, string Ts, string Status, string? TraceId, double? DurationMs, bool HasError,
        string? ErrorType, string? ErrorMessage, IReadOnlyList<Artifact> Artifacts)
    {
        public bool Failed => Status == "error";

        public static SpanRow From(JsonObject ev)
        {
            var error = ev["error"] as JsonObject;
            var artifacts = (ev["artifacts"] as JsonArray ?? [])
                .OfType<JsonObject>()
                .Select(a => new Artifact(a.Text("path") ?? "", a.Text("kind") ?? ""))
                .ToList();
            return new SpanRow(
                ev.Text("name") ?? "", ev.Text("ts") ?? "", ev.Text("status") ?? "", ev.Text("trace_id"),
                ev.Number("duration_ms"), error is { Count: > 0 }, error?.Text("type"), error?.Text("message"), artifacts);
        }
    }
}

internal static class JsonFields
{
    public static string? Text(this JsonObject node, string key) =>
        node[key] is JsonValue value && value.TryGetValue(out string? text) ? text : null;

    public static double? Number(this JsonObject node, string key) =>
        node[key] is JsonValue value && value.TryGetValue(out double number) ? number : null;
}

[JsonSourceGenerationOptions(PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower)]
[JsonSerializable(typeof(EyeReport))]
internal sealed partial class EyeReportJson : JsonSerializerContext;
