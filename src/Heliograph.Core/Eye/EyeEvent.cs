using System.Globalization;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Heliograph.Core.Eye;

public enum EventKind
{
    Span,
    Event,
    Uncaught,
    Artifact,
}

public enum EventStatus
{
    Ok,
    Error,
    Running,
}

public sealed record ErrorInfo(string Type, string Message, string? Traceback = null)
{
    private const int MaxMessage = 2000;
    private const int MaxTraceback = 8000;

    public static ErrorInfo From(Exception exception) => new(
        exception is HeliographException h ? h.ErrorType : exception.GetType().Name,
        Redaction.Truncate(exception.Message, MaxMessage),
        Tail(exception.ToString(), MaxTraceback));

    private static string Tail(string text, int max)
    {
        if (text.Length <= max)
        {
            return text;
        }
        var start = text.Length - max;
        return text[(char.IsLowSurrogate(text[start]) ? start + 1 : start)..];
    }
}

public sealed record Artifact(string Path, string Kind);

public sealed record EyeEvent
{
    private static readonly Dictionary<string, object?> NoAttrs = [];

    private static readonly JsonSerializerOptions LineOptions = new()
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    public EventKind Kind { get; init; } = EventKind.Span;

    public required string Name { get; init; }

    /// <summary>Start time in seconds since the Unix epoch.</summary>
    public double TsEpoch { get; init; } = EyeClock.Now();

    public string Ts => EyeClock.Iso(TsEpoch);

    public required string TraceId { get; init; }

    public string SpanId { get; init; } = EyeClock.NewId();

    public string? ParentId { get; init; }

    public EventStatus Status { get; init; } = EventStatus.Ok;

    public double? DurationMs { get; init; }

    public IReadOnlyDictionary<string, object?> Attrs { get; init; } = NoAttrs;

    public ErrorInfo? Error { get; init; }

    public IReadOnlyList<Artifact> Artifacts { get; init; } = [];

    /// <summary>The redacted record, keyed and ordered as the Python <c>Event</c> model dumps it.</summary>
    public JsonObject ToData() => new()
    {
        ["kind"] = Kind.Wire(),
        ["name"] = Redaction.RedactText(Name),
        ["ts"] = Ts,
        ["ts_epoch"] = TsEpoch,
        ["trace_id"] = Redaction.RedactText(TraceId),
        ["span_id"] = Redaction.RedactText(SpanId),
        ["parent_id"] = ParentId is null ? null : Redaction.RedactText(ParentId),
        ["status"] = Status.Wire(),
        ["duration_ms"] = DurationMs,
        ["attrs"] = Redaction.Redact(Attrs),
        ["error"] = Error is null ? null : new JsonObject
        {
            ["type"] = Redaction.RedactText(Error.Type),
            ["message"] = Redaction.RedactText(Error.Message),
            ["traceback"] = Error.Traceback is null ? null : Redaction.RedactText(Error.Traceback),
        },
        ["artifacts"] = new JsonArray([.. Artifacts.Select(a => new JsonObject
        {
            ["path"] = Redaction.RedactText(a.Path),
            ["kind"] = Redaction.RedactText(a.Kind),
        })]),
    };

    internal static string ToLine(JsonObject data) => data.ToJsonString(LineOptions);
}

internal static class EyeClock
{
    public static double Now() => (DateTime.UtcNow - DateTime.UnixEpoch).TotalSeconds;

    public static string Iso(double epoch) =>
        DateTime.UnixEpoch.AddTicks((long)(epoch * TimeSpan.TicksPerSecond))
            .ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", CultureInfo.InvariantCulture);

    public static string NewId(int length = 16) => Guid.NewGuid().ToString("N")[..length];

    public static string Wire(this EventKind kind) => kind switch
    {
        EventKind.Span => "span",
        EventKind.Event => "event",
        EventKind.Uncaught => "uncaught",
        _ => "artifact",
    };

    public static string Wire(this EventStatus status) => status switch
    {
        EventStatus.Ok => "ok",
        EventStatus.Error => "error",
        _ => "running",
    };
}
