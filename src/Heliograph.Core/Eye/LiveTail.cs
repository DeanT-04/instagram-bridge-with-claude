using System.Diagnostics;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Spectre.Console;
using Spectre.Console.Rendering;

namespace Heliograph.Core.Eye;

public sealed record HealthLine(int Spans, double ErrorRate, double? P95Ms, IReadOnlyList<(string Name, int Count)> TopFailing)
{
    public override string ToString() => string.Concat(Segments().Select(s => s.Text));

    public IRenderable ToRenderable()
    {
        var line = new Paragraph();
        foreach (var (text, style) in Segments())
        {
            line.Append(text, style);
        }
        return line;
    }

    private IEnumerable<(string Text, Style Style)> Segments()
    {
        if (Spans == 0)
        {
            yield return ("no spans yet", new Style(decoration: Decoration.Dim));
            yield break;
        }
        yield return ($"spans {Spans}  ", Style.Plain);
        yield return ($"errors {Whole(ErrorRate * 100)}%  ", new Style(ErrorRate > 0.1 ? Color.Red : Color.Green));
        yield return ($"p95 {Whole(P95Ms ?? 0)} ms  ", Style.Plain);
        if (TopFailing.Count > 0)
        {
            yield return ("top failing: " + string.Join(", ", TopFailing.Select(t => $"{t.Name}×{t.Count}")), new Style(Color.Red));
        }
    }

    private static string Whole(double value) =>
        Math.Round(value, MidpointRounding.ToEven).ToString("0", CultureInfo.InvariantCulture);
}

public static class LiveTail
{
    private const int Window = 500;
    private static readonly TimeSpan DefaultPoll = TimeSpan.FromSeconds(0.5);
    private static readonly TimeSpan RefreshEvery = TimeSpan.FromMilliseconds(250);

    /// <summary>The last <paramref name="count"/> events that parse as JSON objects, oldest first.</summary>
    public static List<JsonObject> ReadLast(string path, int count)
    {
        var buffer = new Queue<JsonObject>(count);
        if (!File.Exists(path))
        {
            return [];
        }
        using var reader = new StreamReader(OpenShared(path), Encoding.UTF8);
        while (reader.ReadLine() is { } line)
        {
            if (Parse(line) is { } ev)
            {
                if (buffer.Count == count)
                {
                    buffer.Dequeue();
                }
                buffer.Enqueue(ev);
            }
        }
        return [.. buffer];
    }

    /// <summary>
    /// Yields events appended to <paramref name="path"/> until cancelled, and null on an idle poll. A partial
    /// last line waits for its newline. A file that shrank was rotated, so it is read again from the start.
    /// </summary>
    public static async IAsyncEnumerable<JsonObject?> FollowLines(
        string path, TimeSpan? poll = null, [EnumeratorCancellation] CancellationToken cancellation = default)
    {
        var position = Size(path);
        while (true)
        {
            cancellation.ThrowIfCancellationRequested();
            var size = Size(path);
            if (size < position)
            {
                position = 0;
            }
            var (events, next) = size > position ? ReadFrom(path, position) : ([], position);
            position = next;
            if (events.Count == 0)
            {
                yield return null;
                await Task.Delay(poll ?? DefaultPoll, cancellation).ConfigureAwait(false);
                continue;
            }
            foreach (var ev in events)
            {
                yield return ev;
            }
        }
    }

    public static HealthLine Health(IEnumerable<JsonObject> events)
    {
        var spans = events.Where(e => e.Text("kind") == "span").ToList();
        if (spans.Count == 0)
        {
            return new HealthLine(0, 0, null, []);
        }
        var errors = spans.Where(e => e.Text("status") == "error").ToList();
        var durations = spans.Select(e => e.Number("duration_ms")).Where(d => d is not null and not 0).Select(d => d!.Value).ToList();
        var top = errors
            .GroupBy(e => e.Text("name") ?? "")
            .Select(g => (g.Key, g.Count()))
            .OrderByDescending(t => t.Item2)
            .Take(3)
            .ToList();
        return new HealthLine(spans.Count, (double)errors.Count / spans.Count, EyeReport.Percentile(durations, 95), top);
    }

    /// <summary>The event table for the last <paramref name="rows"/> events, with the health line under it.</summary>
    public static IRenderable Render(IReadOnlyCollection<JsonObject> events, int rows = 25)
    {
        var table = new Table().Expand().Border(TableBorder.Simple);
        table.AddColumn(new TableColumn("time").NoWrap());
        table.AddColumn(new TableColumn("trace").NoWrap());
        table.AddColumn(new TableColumn("name"));
        table.AddColumn(new TableColumn("status").NoWrap());
        table.AddColumn(new TableColumn("ms").RightAligned().NoWrap());
        table.AddColumn(new TableColumn("detail"));
        var dim = new Style(decoration: Decoration.Dim);
        foreach (var ev in events.Skip(Math.Max(0, events.Count - rows)))
        {
            var status = ev.Text("status") ?? "?";
            var detail = ev["error"] is JsonObject { Count: > 0 } error
                ? $"{error.Text("type") ?? "None"}: {error.Text("message") ?? "None"}"
                : "";
            if (ev["artifacts"] is JsonArray { Count: > 0 } artifacts)
            {
                detail += $"  [{artifacts.Count} artifact(s)]";
            }
            var duration = ev.Number("duration_ms");
            table.AddRow(
                new Text(Slice(ev.Text("ts") ?? "", 11, 23), dim),
                new Text(Slice(ev.Text("trace_id") ?? "", 0, 8), dim),
                new Text((ev.Text("parent_id") is { Length: > 0 } ? "  " : "") + (ev.Text("name") ?? ""), new Style(Color.Aqua)),
                new Text(status, StatusStyle(status)),
                new Text(duration is { } ms ? Math.Round(ms, MidpointRounding.ToEven).ToString("0", CultureInfo.InvariantCulture) : ""),
                new Text(detail));
        }
        return new Rows(table, Health(events).ToRenderable());
    }

    /// <summary>Shows recent events. With <paramref name="follow"/> it keeps updating until cancelled.</summary>
    /// <param name="path">Defaults to the process-wide recorder's <c>events.jsonl</c>.</param>
    public static async Task Tail(
        string? path = null, bool follow = true, int lines = 25, IAnsiConsole? console = null, TimeSpan? poll = null,
        CancellationToken cancellation = default)
    {
        path ??= EyeRecorder.Current.Jsonl.Path;
        console ??= AnsiConsole.Console;
        var window = new Queue<JsonObject>(ReadLast(path, Window));
        if (!follow)
        {
            console.Write(Render(window, lines));
            return;
        }
        await console.Live(Render(window, lines)).StartAsync(async live =>
        {
            var sinceRefresh = Stopwatch.StartNew();
            var dirty = false;
            try
            {
                await foreach (var ev in FollowLines(path, poll, cancellation).ConfigureAwait(false))
                {
                    if (ev is not null)
                    {
                        window.Enqueue(ev);
                        if (window.Count > Window)
                        {
                            window.Dequeue();
                        }
                        dirty = true;
                    }
                    if (dirty && (ev is null || sinceRefresh.Elapsed >= RefreshEvery))
                    {
                        live.UpdateTarget(Render(window, lines));
                        live.Refresh();
                        sinceRefresh.Restart();
                        dirty = false;
                    }
                }
            }
            catch (OperationCanceledException)
            {
            }
        }).ConfigureAwait(false);
    }

    private static (List<JsonObject> Events, long Next) ReadFrom(string path, long position)
    {
        byte[] bytes;
        try
        {
            using var stream = OpenShared(path);
            stream.Seek(position, SeekOrigin.Begin);
            using var buffer = new MemoryStream();
            stream.CopyTo(buffer);
            bytes = buffer.ToArray();
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return ([], position);
        }
        var end = Array.LastIndexOf(bytes, (byte)'\n');
        if (end < 0)
        {
            return ([], position);
        }
        var events = Encoding.UTF8.GetString(bytes, 0, end)
            .Split('\n')
            .Select(Parse)
            .OfType<JsonObject>()
            .ToList();
        return (events, position + end + 1);
    }

    private static JsonObject? Parse(string line)
    {
        try
        {
            return JsonNode.Parse(line) as JsonObject;
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static FileStream OpenShared(string path) =>
        new(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);

    private static long Size(string path)
    {
        var file = new FileInfo(path);
        return file.Exists ? file.Length : 0;
    }

    private static string Slice(string text, int start, int end) =>
        start >= text.Length ? "" : text[start..Math.Min(end, text.Length)];

    private static Style StatusStyle(string status) => status switch
    {
        "ok" => new Style(Color.Green),
        "error" => new Style(Color.Red, decoration: Decoration.Bold),
        "running" => new Style(Color.Yellow),
        _ => Style.Plain,
    };
}
