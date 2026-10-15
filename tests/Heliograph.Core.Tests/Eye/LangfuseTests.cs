using System.Net;
using System.Text;
using System.Text.Json.Nodes;
using Heliograph.Core.Config;
using Heliograph.Core.Eye;

namespace Heliograph.Core.Tests.Eye;

public sealed class LangfuseTests : IDisposable
{
    private static readonly Dictionary<string, string> Keys = new()
    {
        ["LANGFUSE_PUBLIC_KEY"] = "pk-lf-public",
        ["LANGFUSE_SECRET_KEY"] = "sk-lf-secret",
        ["LANGFUSE_HOST"] = "https://langfuse.example",
    };

    private readonly TempDir _tmp = new();

    public void Dispose() => _tmp.Dispose();

    [Theory]
    [InlineData(null, "sk")]
    [InlineData("pk", null)]
    [InlineData("", "sk")]
    [InlineData("pk", "")]
    public void Export_is_off_unless_both_keys_are_set(string? publicKey, string? secretKey)
    {
        var environment = new Dictionary<string, string>();
        if (publicKey is not null)
        {
            environment["LANGFUSE_PUBLIC_KEY"] = publicKey;
        }
        if (secretKey is not null)
        {
            environment["LANGFUSE_SECRET_KEY"] = secretKey;
        }

        Assert.Null(LangfuseSink.FromEnvironment(environment, new Capture()));
    }

    [Fact]
    public void Recorder_from_settings_adds_the_sink_only_when_keys_are_set()
    {
        var settings = HeliographSettings.Load(
            new Dictionary<string, string> { ["HELIOGRAPH_HOME"] = _tmp.Path("home") }, envFile: null);

        using (var off = EyeRecorder.FromSettings(settings, new Dictionary<string, string>()))
        {
            Assert.DoesNotContain(off.Sinks, s => s is LangfuseSink);
        }
        using var on = EyeRecorder.FromSettings(settings, Keys);
        Assert.Contains(on.Sinks, s => s is LangfuseSink);
    }

    [Fact]
    public void Exports_redacted_span_create_with_basic_auth_on_close()
    {
        var capture = new Capture();
        var sink = LangfuseSink.FromEnvironment(Keys, capture)!;
        var recorder = new EyeRecorder(_tmp.Path("eye"), extraSinks: [sink]);
        recorder.Emit(new EyeEvent
        {
            Name = "cdp.fetch",
            TraceId = "trace0001",
            SpanId = "span0001",
            ParentId = "parent0001",
            TsEpoch = 1_700_000_000.0,
            DurationMs = 1500,
            Status = EventStatus.Error,
            Attrs = new Dictionary<string, object?> { ["password"] = "hunter2", ["url"] = "https://x" },
            Error = new ErrorInfo("TimeoutException", "slow"),
        });

        recorder.Dispose();

        var request = Assert.Single(capture.Requests);
        Assert.Equal("https://langfuse.example/api/public/ingestion", request.Uri.ToString());
        Assert.Equal("Basic " + Convert.ToBase64String(Encoding.UTF8.GetBytes("pk-lf-public:sk-lf-secret")), request.Authorization);
        Assert.DoesNotContain("hunter2", request.Body, StringComparison.Ordinal);
        var item = JsonNode.Parse(request.Body)!["batch"]![0]!;
        Assert.Equal("span-create", (string?)item["type"]);
        var body = item["body"]!;
        Assert.Equal("span0001", (string?)body["id"]);
        Assert.Equal("trace0001", (string?)body["traceId"]);
        Assert.Equal("parent0001", (string?)body["parentObservationId"]);
        Assert.Equal("cdp.fetch", (string?)body["name"]);
        Assert.Equal("2023-11-14T22:13:20.000Z", (string?)body["startTime"]);
        Assert.Equal("2023-11-14T22:13:21.500Z", (string?)body["endTime"]);
        Assert.Equal("ERROR", (string?)body["level"]);
        Assert.Equal("slow", (string?)body["statusMessage"]);
        Assert.Equal(Redaction.Redacted, (string?)body["metadata"]!["attrs"]!["password"]);
    }

    [Fact]
    public void Export_failures_never_reach_the_caller()
    {
        var sink = LangfuseSink.FromEnvironment(Keys, new Capture(fail: true))!;
        var recorder = new EyeRecorder(_tmp.Path("eye"), extraSinks: [sink]);

        recorder.Emit(new EyeEvent { Name = "x", TraceId = "t" });
        recorder.Dispose();

        Assert.True(File.Exists(_tmp.Path("eye", "events.jsonl")));
    }

    private sealed class Capture(bool fail = false) : HttpMessageHandler
    {
        public List<(Uri Uri, string? Authorization, string Body)> Requests { get; } = [];

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            if (fail)
            {
                throw new HttpRequestException("unreachable");
            }
            var body = await request.Content!.ReadAsStringAsync(cancellationToken);
            lock (Requests)
            {
                Requests.Add((request.RequestUri!, request.Headers.Authorization?.ToString(), body));
            }
            return new HttpResponseMessage(HttpStatusCode.OK);
        }
    }
}
