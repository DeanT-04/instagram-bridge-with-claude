using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Text.Json.Nodes;

namespace Heliograph.Core.Eye;

/// <summary>
/// A span that is open in the current async flow. Disposing it records it. Nested spans share the
/// trace id and link the outer span id as their parent. Single use.
/// </summary>
public sealed class ActiveSpan : IDisposable
{
    private readonly ActiveSpan? _previous;
    private readonly Action<ActiveSpan, Exception>? _onError;
    private readonly double _wall;
    private readonly long _started;
    private Exception? _error;
    private bool _closed;

    internal ActiveSpan(string name, Dictionary<string, object?> attrs, Action<ActiveSpan, Exception>? onError)
    {
        _previous = Spans.Flow.Value;
        _onError = onError;
        Name = name;
        TraceId = _previous?.TraceId ?? EyeClock.NewId(32);
        SpanId = EyeClock.NewId();
        ParentId = _previous?.SpanId;
        Attrs = attrs;
        Spans.Flow.Value = this;
        _wall = EyeClock.Now();
        _started = Stopwatch.GetTimestamp();
    }

    public string Name { get; }

    public string TraceId { get; }

    public string SpanId { get; }

    public string? ParentId { get; }

    public Dictionary<string, object?> Attrs { get; }

    public List<Artifact> Artifacts { get; } = [];

    public ActiveSpan Set(string key, object? value)
    {
        Attrs[key] = value;
        return this;
    }

    /// <summary>
    /// Marks the span failed. Returns false so it fits an exception filter, which records the error
    /// before the stack unwinds and leaves the exception to propagate unchanged.
    /// </summary>
    public bool Fail(Exception exception)
    {
        _error ??= exception;
        return false;
    }

    public void Dispose()
    {
        if (_closed)
        {
            return;
        }
        _closed = true;
        var durationMs = Stopwatch.GetElapsedTime(_started).TotalMilliseconds;
        ErrorInfo? error = null;
        if (_error is not null)
        {
            RunErrorHook(_error);
            error = ErrorInfo.From(_error);
        }
        Spans.Flow.Value = _previous;
        EyeRecorder.Record(new EyeEvent
        {
            Name = Name,
            TsEpoch = _wall,
            TraceId = TraceId,
            SpanId = SpanId,
            ParentId = ParentId,
            Status = error is null ? EventStatus.Ok : EventStatus.Error,
            DurationMs = Math.Round(durationMs, 3),
            Attrs = Attrs,
            Error = error,
            Artifacts = Artifacts,
        });
    }

    private void RunErrorHook(Exception error)
    {
        try
        {
            _onError?.Invoke(this, error);
        }
        catch (Exception hookError)
        {
            Attrs["on_error_failed"] = $"{hookError.GetType().Name}: {hookError.Message}";
        }
    }
}

public static class Spans
{
    private const int MaxRepr = 200;

    private static readonly HashSet<string> PersonalContentArgs = ["text", "comment_text", "message"];

    internal static readonly AsyncLocal<ActiveSpan?> Flow = new();

    public static ActiveSpan? Current => Flow.Value;

    public static string? CurrentTraceId => Flow.Value?.TraceId;

    public static ActiveSpan Start(string name, params (string Key, object? Value)[] attrs) => Start(name, null, attrs);

    /// <param name="onError">Runs before the span is written when it failed, for example to attach a screenshot.</param>
    public static ActiveSpan Start(
        string name, Action<ActiveSpan, Exception>? onError, params (string Key, object? Value)[] attrs)
    {
        var values = new Dictionary<string, object?>(attrs.Length);
        foreach (var (key, value) in attrs)
        {
            values[key] = value;
        }
        return new ActiveSpan(name, values, onError);
    }

    /// <summary>
    /// Runs <paramref name="body"/> inside a span. The name defaults to <c>File.Member</c> of the caller, and
    /// <paramref name="args"/> are summarized into <c>attrs["args"]</c>. Exceptions are recorded and rethrown.
    /// </summary>
    public static void Run(
        Action<ActiveSpan> body, string? name = null, (string Name, object? Value)[]? args = null,
        Action<ActiveSpan, Exception>? onError = null,
        [CallerFilePath] string file = "", [CallerMemberName] string member = "")
    {
        using var span = Open(name, args, onError, file, member);
        try
        {
            body(span);
        }
        catch (Exception e) when (span.Fail(e))
        {
            throw;
        }
    }

    /// <inheritdoc cref="Run(Action{ActiveSpan}, string?, ValueTuple{string, object?}[]?, Action{ActiveSpan, Exception}?, string, string)"/>
    public static T Run<T>(
        Func<ActiveSpan, T> body, string? name = null, (string Name, object? Value)[]? args = null,
        Action<ActiveSpan, Exception>? onError = null,
        [CallerFilePath] string file = "", [CallerMemberName] string member = "")
    {
        using var span = Open(name, args, onError, file, member);
        try
        {
            return body(span);
        }
        catch (Exception e) when (span.Fail(e))
        {
            throw;
        }
    }

    /// <inheritdoc cref="Run(Action{ActiveSpan}, string?, ValueTuple{string, object?}[]?, Action{ActiveSpan, Exception}?, string, string)"/>
    public static async Task RunAsync(
        Func<ActiveSpan, Task> body, string? name = null, (string Name, object? Value)[]? args = null,
        Action<ActiveSpan, Exception>? onError = null,
        [CallerFilePath] string file = "", [CallerMemberName] string member = "")
    {
        using var span = Open(name, args, onError, file, member);
        try
        {
            await body(span).ConfigureAwait(false);
        }
        catch (Exception e) when (span.Fail(e))
        {
            throw;
        }
    }

    /// <inheritdoc cref="Run(Action{ActiveSpan}, string?, ValueTuple{string, object?}[]?, Action{ActiveSpan, Exception}?, string, string)"/>
    public static async Task<T> RunAsync<T>(
        Func<ActiveSpan, Task<T>> body, string? name = null, (string Name, object? Value)[]? args = null,
        Action<ActiveSpan, Exception>? onError = null,
        [CallerFilePath] string file = "", [CallerMemberName] string member = "")
    {
        using var span = Open(name, args, onError, file, member);
        try
        {
            return await body(span).ConfigureAwait(false);
        }
        catch (Exception e) when (span.Fail(e))
        {
            throw;
        }
    }

    /// <summary>
    /// A safe, truncated, redacted summary of call arguments. <c>self</c> and <c>cls</c> are skipped, sensitive
    /// names are masked, and message bodies are reduced to their length.
    /// </summary>
    public static JsonObject SummarizeArgs(IEnumerable<(string Name, object? Value)> args)
    {
        var summary = new Dictionary<string, object?>();
        foreach (var (key, value) in args)
        {
            if (key is "self" or "cls")
            {
                continue;
            }
            summary[key] = Redaction.IsSensitiveKey(key) ? Redaction.Redacted
                : PersonalContentArgs.Contains(key) && value is string text ? $"<{text.EnumerateRunes().Count()} chars>"
                : Short(value);
        }
        return Redaction.Redact(summary)!.AsObject();
    }

    /// <summary>Links a file to the current span, or writes a standalone artifact event outside any span.</summary>
    public static void AttachArtifact(string path, string kind)
    {
        var artifact = new Artifact(path, kind);
        if (Flow.Value is { } active)
        {
            active.Artifacts.Add(artifact);
            return;
        }
        EyeRecorder.Record(new EyeEvent
        {
            Kind = EventKind.Artifact,
            Name = $"artifact.{kind}",
            TraceId = EyeClock.NewId(32),
            Artifacts = [artifact],
        });
    }

    /// <summary>Records a point-in-time event, joining the current trace when there is one.</summary>
    public static void Event(string name, (string Key, object? Value)[]? attrs = null, bool error = false)
    {
        var active = Flow.Value;
        var values = new Dictionary<string, object?>();
        foreach (var (key, value) in attrs ?? [])
        {
            values[key] = value;
        }
        EyeRecorder.Record(new EyeEvent
        {
            Kind = EventKind.Event,
            Name = name,
            TraceId = active?.TraceId ?? EyeClock.NewId(32),
            ParentId = active?.SpanId,
            Status = error ? EventStatus.Error : EventStatus.Ok,
            Attrs = values,
        });
    }

    private static ActiveSpan Open(
        string? name, (string Name, object? Value)[]? args, Action<ActiveSpan, Exception>? onError, string file, string member)
    {
        var attrs = args is null ? [] : new Dictionary<string, object?> { ["args"] = SummarizeArgs(args) };
        return new ActiveSpan(name ?? $"{Path.GetFileNameWithoutExtension(file)}.{member}", attrs, onError);
    }

    private static object? Short(object? value) => value switch
    {
        null or bool or int or long or double or float or decimal or short or sbyte or byte or ushort or uint or ulong => value,
        string text => Clip(text),
        _ => Clip(Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture) ?? ""),
    };

    private static string Clip(string text) => text.Length <= MaxRepr ? text : Redaction.Truncate(text, MaxRepr) + "…";
}
