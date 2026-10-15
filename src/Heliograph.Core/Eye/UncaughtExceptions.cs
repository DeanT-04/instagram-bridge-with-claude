namespace Heliograph.Core.Eye;

/// <summary>Records unhandled exceptions, on the main thread or any other, as <c>uncaught</c> events.</summary>
public static class UncaughtExceptions
{
    private static int _installed;
    private static int _mainThreadId;

    /// <summary>Hooks <see cref="AppDomain.UnhandledException"/>. Idempotent. Call it from the main thread.</summary>
    public static void Install()
    {
        if (Interlocked.Exchange(ref _installed, 1) == 1)
        {
            return;
        }
        _mainThreadId = Environment.CurrentManagedThreadId;
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
        {
            if (e.ExceptionObject is Exception exception)
            {
                Record(exception, Environment.CurrentManagedThreadId == _mainThreadId ? "main" : Thread.CurrentThread.Name ?? "thread");
            }
        };
    }

    /// <summary>Writes an <c>uncaught</c> event for <paramref name="exception"/>. Never throws.</summary>
    public static void Record(Exception exception, string where = "main")
    {
        try
        {
            var active = Spans.Current;
            EyeRecorder.Record(new EyeEvent
            {
                Kind = EventKind.Uncaught,
                Name = $"uncaught.{where}",
                TraceId = active?.TraceId ?? EyeClock.NewId(32),
                ParentId = active?.SpanId,
                Status = EventStatus.Error,
                Error = ErrorInfo.From(exception),
            });
        }
        catch (Exception)
        {
        }
    }
}
