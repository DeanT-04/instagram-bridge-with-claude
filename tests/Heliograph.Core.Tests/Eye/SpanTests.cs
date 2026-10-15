using System.Text.Json.Nodes;
using Heliograph.Core.Eye;
using static Heliograph.Core.Tests.Eye.RedactTests;

namespace Heliograph.Core.Tests.Eye;

[Collection(ProcessStateCollection.Name)]
public sealed class SpanTests : IDisposable
{
    private readonly EyeHome _eye = new();

    public void Dispose() => _eye.Dispose();

    [Fact]
    public void Sync_span_records_ok()
    {
        using (var span = Spans.Start("unit.ok", ("url", "https://x")))
        {
            span.Set("extra", 1);
        }

        var ev = Assert.Single(_eye.Recorded());
        Assert.Equal("unit.ok", (string?)ev["name"]);
        Assert.Equal("ok", (string?)ev["status"]);
        Assert.True((double)ev["duration_ms"]! >= 0);
        AssertJson("""{"url":"https://x","extra":1}""", ev["attrs"]);
        Assert.Null(ev["parent_id"]);
        Assert.Equal(32, ((string?)ev["trace_id"])!.Length);
    }

    [Fact]
    public void Span_records_error_and_rethrows()
    {
        var thrown = Assert.Throws<InvalidOperationException>(
            () => Spans.Run(_ => throw new InvalidOperationException("boom"), "unit.fail"));

        Assert.Equal("boom", thrown.Message);
        var ev = Assert.Single(_eye.Recorded());
        Assert.Equal("error", (string?)ev["status"]);
        Assert.Equal("InvalidOperationException", (string?)ev["error"]!["type"]);
        Assert.Equal("boom", (string?)ev["error"]!["message"]);
        var traceback = (string?)ev["error"]!["traceback"];
        Assert.Contains("System.InvalidOperationException: boom", traceback, StringComparison.Ordinal);
        Assert.Contains(" at ", traceback, StringComparison.Ordinal);
    }

    [Fact]
    public void Heliograph_errors_keep_their_python_type_name()
    {
        Assert.Throws<NotLoggedInException>(() => Spans.Run(_ => throw new NotLoggedInException(), "unit.login"));

        Assert.Equal("NotLoggedInError", (string?)Assert.Single(_eye.Recorded())["error"]!["type"]);
    }

    [Fact]
    public void Nested_spans_share_trace_and_link_parent()
    {
        using (var outer = Spans.Start("outer"))
        using (var inner = Spans.Start("inner"))
        {
            Assert.Same(inner, Spans.Current);
            Assert.Equal(outer.TraceId, Spans.CurrentTraceId);
        }

        Assert.Null(Spans.Current);
        var events = _eye.Recorded().ToDictionary(e => (string)e["name"]!);
        Assert.Equal((string?)events["outer"]["trace_id"], (string?)events["inner"]["trace_id"]);
        Assert.Equal((string?)events["outer"]["span_id"], (string?)events["inner"]["parent_id"]);
    }

    [Fact]
    public void Sibling_top_level_spans_get_distinct_traces()
    {
        using (Spans.Start("a"))
        {
        }
        using (Spans.Start("b"))
        {
        }

        var events = _eye.Recorded();
        Assert.Equal(2, events.Count);
        Assert.NotEqual((string?)events[0]["trace_id"], (string?)events[1]["trace_id"]);
    }

    [Fact]
    public async Task Async_span_and_task_propagation()
    {
        static Task Child(int i) => Spans.RunAsync(async span =>
        {
            span.Set("i", i);
            await Task.Yield();
        }, "child");

        var parent = await Spans.RunAsync(async span =>
        {
            await Task.WhenAll(Child(1), Child(2));
            return span;
        }, "parent");

        var children = _eye.Recorded(name: "child");
        Assert.Equal(2, children.Count);
        Assert.All(children, c => Assert.Equal(parent.SpanId, (string?)c["parent_id"]));
    }

    [Fact]
    public async Task Traced_async_and_sync()
    {
        static Task<string> Fetch(string url, string password = "x") =>
            Spans.RunAsync(_ => Task.FromResult(url.ToUpperInvariant()), args: [("url", url), ("password", password)]);
        static int Add(int a, int b) => Spans.Run(_ => a + b, "custom.name", [("a", a), ("b", b)]);

        Assert.Equal("ABC", await Fetch("abc", password: "hunter2"));
        Assert.Equal(3, Add(1, 2));

        var events = _eye.Recorded();
        Assert.Equal(2, events.Count);
        var (fetch, add) = (events[0], events[1]);
        Assert.Equal("SpanTests.Traced_async_and_sync", (string?)fetch["name"]);
        AssertJson("""{"url":"abc","password":"[REDACTED]"}""", fetch["attrs"]!["args"]);
        Assert.Equal("custom.name", (string?)add["name"]);
        AssertJson("""{"a":1,"b":2}""", add["attrs"]!["args"]);
    }

    [Fact]
    public void Traced_error_records_args_without_the_instance()
    {
        Assert.Throws<InvalidOperationException>(() => new Driver().Go(5));

        var ev = Assert.Single(_eye.Recorded());
        Assert.Equal("error", (string?)ev["status"]);
        AssertJson("""{"n":5}""", ev["attrs"]!["args"]);
    }

    [Fact]
    public void Summarize_args_skips_self_and_cls() =>
        AssertJson("""{"n":5}""", Spans.SummarizeArgs([("self", new Driver()), ("cls", typeof(Driver)), ("n", 5)]));

    [Fact]
    public void Traced_without_args_capture()
    {
        Spans.Run(_ => 1);

        AssertJson("{}", Assert.Single(_eye.Recorded())["attrs"]);
    }

    [Fact]
    public void Long_args_are_truncated()
    {
        var text = string.Concat(Enumerable.Repeat("a b ", 500));

        Spans.Run(_ => { }, args: [("text", text), ("note", text)]);

        var args = Assert.Single(_eye.Recorded())["attrs"]!["args"]!;
        Assert.True(((string?)args["text"])!.Length <= 201);
        Assert.Equal(text[..200] + "…", (string?)args["note"]);
    }

    [Fact]
    public void Attach_artifact_and_on_error_hook()
    {
        var shot = _eye.Path("shot.png");

        Assert.Throws<KeyNotFoundException>(() => Spans.Run(
            _ => throw new KeyNotFoundException("x"),
            "ui.click",
            onError: (_, _) => Spans.AttachArtifact(shot, "screenshot")));
        Spans.AttachArtifact(_eye.Path("loose.json"), "snapshot");

        var events = _eye.Recorded();
        Assert.Equal(2, events.Count);
        var (ev, loose) = (events[0], events[1]);
        AssertJson(new JsonArray(new JsonObject { ["path"] = shot, ["kind"] = "screenshot" }).ToJsonString(), ev["artifacts"]);
        Assert.Equal("artifact", (string?)loose["kind"]);
        Assert.Equal("snapshot", (string?)loose["artifacts"]![0]!["kind"]);
    }

    [Fact]
    public void On_error_hook_of_an_outer_span_attaches_to_the_outer_span()
    {
        Assert.Throws<KeyNotFoundException>(() => Spans.Run(
            _ => Spans.Run(_ => throw new KeyNotFoundException("x"), "inner"),
            "outer",
            onError: (_, _) => Spans.AttachArtifact("outer.png", "screenshot")));

        var events = _eye.Recorded().ToDictionary(e => (string)e["name"]!);
        AssertJson("[]", events["inner"]["artifacts"]);
        Assert.Equal("outer.png", (string?)events["outer"]["artifacts"]![0]!["path"]);
    }

    [Fact]
    public void Secrets_are_redacted_in_jsonl()
    {
        using (Spans.Start(
            "http",
            ("headers", new Dictionary<string, object?> { ["Authorization"] = "Bearer placeholder-token-value" }),
            ("note", "sessionid=12345%3Aabc")))
        {
        }

        var text = File.ReadAllText(Path.Combine(_eye.EyeDir, "events.jsonl"));
        Assert.DoesNotContain("placeholder-token-value", text, StringComparison.Ordinal);
        Assert.DoesNotContain("12345%3Aabc", text, StringComparison.Ordinal);
        var first = JsonNode.Parse(text.Split('\n')[0])!;
        Assert.Equal(Redaction.Redacted, (string?)first["attrs"]!["headers"]!["Authorization"]);
    }

    [Fact]
    public void Exception_message_and_traceback_are_redacted()
    {
        Assert.Throws<InvalidOperationException>(() => Spans.Run(
            _ => throw new InvalidOperationException("GET https://i.instagram.com/api?x=1 Cookie sessionid=999%3Asecret"),
            "leaky"));

        var text = File.ReadAllText(Path.Combine(EyeRecorder.Current.Directory, "events.jsonl"));
        Assert.DoesNotContain("999%3Asecret", text, StringComparison.Ordinal);
        var error = JsonNode.Parse(text.TrimEnd('\n').Split('\n')[^1])!["error"]!;
        Assert.Contains(Redaction.Redacted, (string?)error["message"], StringComparison.Ordinal);
        Assert.DoesNotContain("999%3Asecret", (string?)error["traceback"], StringComparison.Ordinal);
    }

    [Fact]
    public void Point_event_joins_current_trace()
    {
        string traceId, spanId;
        using (var outer = Spans.Start("outer"))
        {
            (traceId, spanId) = (outer.TraceId, outer.SpanId);
            Spans.Event("rate_limit.wait", [("seconds", 1.5)]);
        }

        var ev = _eye.Recorded(EventKind.Event)[0];
        Assert.Equal(traceId, (string?)ev["trace_id"]);
        Assert.Equal(spanId, (string?)ev["parent_id"]);
        Assert.Equal(1.5, (double)ev["attrs"]!["seconds"]!);
    }

    [Fact]
    public void Jsonl_rotation()
    {
        var path = _eye.Path("e.jsonl");
        var sink = new JsonlSink(path, maxBytes: 200, backups: 2);

        for (var i = 0; i < 20; i++)
        {
            sink.Append(new string('x', 60));
        }

        Assert.True(new FileInfo(path).Length <= 200);
        Assert.True(File.Exists(path + ".1") && File.Exists(path + ".2"));
        Assert.False(File.Exists(path + ".3"));
    }

    [Fact]
    public void Failing_sink_never_breaks_caller()
    {
        using var recorder = new EyeRecorder(_eye.Path("eye2"), extraSinks: [new BrokenSink()]);

        recorder.Emit(new EyeEvent { Name = "x", TraceId = "t" });

        Assert.Equal("x", (string?)recorder.Query()[0]["name"]);
    }

    [Fact]
    public void Threads_started_without_context_flow_do_not_inherit_span()
    {
        object? seen = "unset";
        using (Spans.Start("main"))
        {
            Thread thread;
            using (ExecutionContext.SuppressFlow())
            {
                thread = new Thread(() => seen = Spans.Current);
                thread.Start();
            }
            thread.Join();
        }

        Assert.Null(seen);
    }

    [Fact]
    public void Excepthook_records_uncaught()
    {
        try
        {
            throw new DivideByZeroException("nope");
        }
        catch (DivideByZeroException e)
        {
            UncaughtExceptions.Record(e);
        }

        var ev = Assert.Single(_eye.Recorded(EventKind.Uncaught));
        Assert.Equal("uncaught.main", (string?)ev["name"]);
        Assert.Equal("DivideByZeroException", (string?)ev["error"]!["type"]);
        Assert.Equal("error", (string?)ev["status"]);
    }

    [Fact]
    public void Sqlite_query_filters()
    {
        foreach (var name in new[] { "cdp.a", "cdp.b", "uia.c" })
        {
            using (Spans.Start(name))
            {
            }
        }
        Assert.Throws<ArgumentException>(() => Spans.Run(_ => throw new ArgumentException(), "cdp.bad"));

        var index = EyeRecorder.Current;
        Assert.Equal(
            new HashSet<string> { "cdp.a", "cdp.b", "cdp.bad" },
            index.Query(name: "cdp.%").Select(e => (string)e["name"]!).ToHashSet());
        Assert.Equal(["cdp.bad"], index.Query(status: EventStatus.Error).Select(e => (string)e["name"]!));
        var traceId = (string?)index.Query(name: "uia.c")[0]["trace_id"];
        Assert.Single(index.Query(traceId: traceId));
        Assert.Empty(index.Query(since: DateTimeOffset.MaxValue));
    }

    [Fact]
    public void Eye_files_are_owner_only_on_posix()
    {
        var dir = _eye.Path("eye2");
        using var recorder = new EyeRecorder(dir);
        using (Spans.Start("perm"))
        {
        }

        recorder.Emit(new EyeEvent { Name = "x", TraceId = "t" });

        if (!OperatingSystem.IsWindows())
        {
            const UnixFileMode Dir = UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute;
            const UnixFileMode File600 = UnixFileMode.UserRead | UnixFileMode.UserWrite;
            Assert.Equal(Dir, File.GetUnixFileMode(dir));
            Assert.Equal(File600, File.GetUnixFileMode(Path.Combine(dir, "events.jsonl")));
            Assert.Equal(File600, File.GetUnixFileMode(Path.Combine(dir, "eye.db")));
        }
        Assert.True(File.Exists(Path.Combine(dir, "events.jsonl")));
    }

    [Fact]
    public void Summarize_args_keeps_only_length_of_message_text() =>
        AssertJson(
            """{"text":"<21 chars>","username":"alice","confirm":true}""",
            Spans.SummarizeArgs([("text", "meet me at 5, private"), ("username", "alice"), ("confirm", true)]));

    [Fact]
    public void Event_lines_keep_the_python_key_order()
    {
        using (Spans.Start("order"))
        {
        }

        var line = File.ReadAllText(Path.Combine(_eye.EyeDir, "events.jsonl")).TrimEnd('\n');
        var keys = JsonNode.Parse(line)!.AsObject().Select(p => p.Key);
        Assert.Equal(
            ["kind", "name", "ts", "ts_epoch", "trace_id", "span_id", "parent_id", "status", "duration_ms", "attrs", "error", "artifacts"],
            keys);
        Assert.Matches(@"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$", (string?)JsonNode.Parse(line)!["ts"]);
    }

    private sealed class Driver
    {
        public void Go(int n) => Spans.Run(_ => throw new InvalidOperationException($"bad {n}"), "drv.go", [("n", n)]);
    }

    private sealed class BrokenSink : IEventSink
    {
        public void Write(EyeEvent ev, JsonObject data, string line) => throw new IOException("disk full");

        public void Dispose()
        {
        }
    }
}
