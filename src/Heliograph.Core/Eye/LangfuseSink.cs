using System.Collections;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Heliograph.Core.Eye;

/// <summary>
/// Optional export of eye events to Langfuse through its ingestion API. Active only when both
/// <c>LANGFUSE_PUBLIC_KEY</c> and <c>LANGFUSE_SECRET_KEY</c> are set. Only redacted data leaves the machine,
/// batched in the background. Export failures are swallowed: Langfuse must never affect Heliograph.
/// </summary>
public sealed class LangfuseSink : IEventSink
{
    private const string DefaultHost = "https://cloud.langfuse.com";
    private const int BatchSize = 50;
    private static readonly TimeSpan FlushEvery = TimeSpan.FromSeconds(1);
    private static readonly TimeSpan CloseTimeout = TimeSpan.FromSeconds(5);

    private readonly HttpClient _http;
    private readonly Uri _endpoint;
    private readonly Lock _gate = new();
    private readonly Timer _timer;
    private List<string> _pending = [];
    private Task _sending = Task.CompletedTask;

    public LangfuseSink(Uri host, string publicKey, string secretKey, HttpMessageHandler? handler = null)
    {
        _http = handler is null ? new HttpClient() : new HttpClient(handler);
        _http.Timeout = TimeSpan.FromSeconds(10);
        _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue(
            "Basic", Convert.ToBase64String(Encoding.UTF8.GetBytes($"{publicKey}:{secretKey}")));
        _endpoint = new Uri(host.AbsoluteUri.TrimEnd('/') + "/api/public/ingestion");
        _timer = new Timer(_ => Flush(), null, FlushEvery, FlushEvery);
    }

    /// <summary>A sink when both keys are set, else null. Reads the process environment by default.</summary>
    public static LangfuseSink? FromEnvironment(
        IReadOnlyDictionary<string, string>? environment = null, HttpMessageHandler? handler = null)
    {
        environment ??= ProcessEnvironment();
        var publicKey = environment.GetValueOrDefault("LANGFUSE_PUBLIC_KEY");
        var secretKey = environment.GetValueOrDefault("LANGFUSE_SECRET_KEY");
        var host = environment.GetValueOrDefault("LANGFUSE_HOST") is { Length: > 0 } custom ? custom : DefaultHost;
        return string.IsNullOrEmpty(publicKey) || string.IsNullOrEmpty(secretKey) || !Uri.TryCreate(host, UriKind.Absolute, out var uri)
            ? null
            : new LangfuseSink(uri, publicKey, secretKey, handler);
    }

    public void Write(EyeEvent ev, JsonObject data, string line)
    {
        var item = SpanCreate(ev, data);
        lock (_gate)
        {
            _pending.Add(item);
            if (_pending.Count < BatchSize)
            {
                return;
            }
        }
        Flush();
    }

    public void Dispose()
    {
        _timer.Dispose();
        Flush();
        try
        {
            _sending.Wait(CloseTimeout);
        }
        catch (Exception)
        {
        }
        _http.Dispose();
    }

    private void Flush()
    {
        lock (_gate)
        {
            if (_pending.Count == 0)
            {
                return;
            }
            var batch = _pending;
            _pending = [];
            _sending = _sending.ContinueWith(_ => Send(batch), TaskScheduler.Default).Unwrap();
        }
    }

    private async Task Send(List<string> batch)
    {
        try
        {
            using var content = new StringContent($"{{\"batch\":[{string.Join(',', batch)}]}}", Encoding.UTF8, "application/json");
            using var response = await _http.PostAsync(_endpoint, content).ConfigureAwait(false);
        }
        catch (Exception)
        {
        }
    }

    private static string SpanCreate(EyeEvent ev, JsonObject data)
    {
        using var buffer = new MemoryStream();
        using (var json = new Utf8JsonWriter(buffer))
        {
            json.WriteStartObject();
            json.WriteString("id", Guid.NewGuid().ToString());
            json.WriteString("timestamp", EyeClock.Iso(EyeClock.Now()));
            json.WriteString("type", "span-create");
            json.WriteStartObject("body");
            json.WriteString("id", data.Text("span_id"));
            json.WriteString("traceId", data.Text("trace_id"));
            json.WriteString("parentObservationId", data.Text("parent_id"));
            json.WriteString("name", data.Text("name"));
            json.WriteString("startTime", EyeClock.Iso(ev.TsEpoch));
            json.WriteString("endTime", EyeClock.Iso(ev.TsEpoch + ((ev.DurationMs ?? 0) / 1000)));
            json.WriteString("level", ev.Status == EventStatus.Error ? "ERROR" : "DEFAULT");
            json.WriteString("statusMessage", (data["error"] as JsonObject)?.Text("message"));
            json.WriteStartObject("metadata");
            json.WritePropertyName("attrs");
            Write(json, data["attrs"]);
            json.WritePropertyName("artifacts");
            Write(json, data["artifacts"]);
            json.WriteString("span_id", data.Text("span_id"));
            json.WriteString("parent_id", data.Text("parent_id"));
            json.WriteEndObject();
            json.WriteEndObject();
            json.WriteEndObject();
        }
        return Encoding.UTF8.GetString(buffer.ToArray());
    }

    private static void Write(Utf8JsonWriter json, JsonNode? node)
    {
        if (node is null)
        {
            json.WriteNullValue();
            return;
        }
        node.WriteTo(json);
    }

    private static Dictionary<string, string> ProcessEnvironment()
    {
        var environment = new Dictionary<string, string>();
        foreach (DictionaryEntry entry in Environment.GetEnvironmentVariables())
        {
            environment[(string)entry.Key] = (string?)entry.Value ?? "";
        }
        return environment;
    }
}
