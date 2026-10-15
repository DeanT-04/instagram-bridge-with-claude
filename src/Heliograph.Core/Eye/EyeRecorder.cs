using System.Text.Json.Nodes;
using Heliograph.Core.Config;

namespace Heliograph.Core.Eye;

/// <summary>
/// Owns the sinks and fans each event out to them. One process-wide instance is built lazily from
/// <see cref="HeliographSettings"/>; tests and embedders swap it with <see cref="Configure"/>.
/// Recording never throws into application code.
/// </summary>
public sealed class EyeRecorder : IDisposable
{
    private static readonly Lock Gate = new();
    private static EyeRecorder? _current;

    private readonly List<IEventSink> _sinks;

    /// <param name="artifactRetention">Failure artifacts older than this are deleted at start-up. Null or zero keeps them.</param>
    public EyeRecorder(
        string directory, long maxBytes = 10 * 1024 * 1024, int backups = 5, IEnumerable<IEventSink>? extraSinks = null,
        TimeSpan? artifactRetention = null)
    {
        Directory = PrivateDirectory.Ensure(directory);
        try
        {
            PrivateDirectory.Ensure(ArtifactsDir);
            if (artifactRetention is { } keep)
            {
                PrunedArtifacts = ArtifactRetention.Prune(ArtifactsDir, keep);
            }
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            Console.Error.WriteLine($"[heliograph.eye] artifact housekeeping failed: {e.Message}");
        }
        Jsonl = new JsonlSink(Path.Combine(directory, "events.jsonl"), maxBytes, backups);
        Index = new SqliteSink(Path.Combine(directory, "eye.db"));
        _sinks = [Jsonl, Index, .. extraSinks ?? []];
    }

    public static EyeRecorder Current => Volatile.Read(ref _current) ?? Build();

    public string Directory { get; }

    public string ArtifactsDir => Path.Combine(Directory, "artifacts");

    public int PrunedArtifacts { get; }

    public bool Enabled { get; set; } = true;

    public JsonlSink Jsonl { get; }

    public SqliteSink Index { get; }

    public IReadOnlyList<IEventSink> Sinks => _sinks;

    /// <param name="environment">Where to look for the Langfuse keys. Defaults to the process environment.</param>
    public static EyeRecorder FromSettings(HeliographSettings settings, IReadOnlyDictionary<string, string>? environment = null) =>
        new(
            settings.EyePath, settings.EyeMaxBytes, settings.EyeBackups,
            LangfuseSink.FromEnvironment(environment) is { } langfuse ? [langfuse] : null,
            settings.ArtifactRetention);

    /// <summary>Replaces the process-wide recorder, closing the old one.</summary>
    public static EyeRecorder Configure(EyeRecorder recorder)
    {
        lock (Gate)
        {
            _current?.Dispose();
            Volatile.Write(ref _current, recorder);
        }
        return recorder;
    }

    /// <summary>Closes and forgets the process-wide recorder; the next use rebuilds it from settings.</summary>
    public static void Reset()
    {
        lock (Gate)
        {
            _current?.Dispose();
            Volatile.Write(ref _current, null);
        }
    }

    internal static void Record(EyeEvent ev)
    {
        try
        {
            Current.Emit(ev);
        }
        catch (Exception e)
        {
            Console.Error.WriteLine($"[heliograph.eye] could not record {ev.Name}: {e.GetType().Name}: {e.Message}");
        }
    }

    /// <summary>Redacts, serializes and writes <paramref name="ev"/> to every sink. Never throws.</summary>
    public void Emit(EyeEvent ev)
    {
        if (!Enabled)
        {
            return;
        }
        JsonObject data;
        string line;
        try
        {
            data = ev.ToData();
            line = EyeEvent.ToLine(data);
        }
        catch (Exception e)
        {
            Console.Error.WriteLine($"[heliograph.eye] could not serialise event: {e.GetType().Name}: {e.Message}");
            return;
        }
        foreach (var sink in _sinks)
        {
            try
            {
                sink.Write(ev, data, line);
            }
            catch (Exception e)
            {
                Console.Error.WriteLine($"[heliograph.eye] sink {sink.GetType().Name} failed: {e.GetType().Name}: {e.Message}");
            }
        }
    }

    /// <inheritdoc cref="SqliteSink.Query"/>
    public List<JsonObject> Query(
        DateTimeOffset? since = null, DateTimeOffset? until = null, EventStatus? status = null,
        string? name = null, string? traceId = null, EventKind? kind = null, int limit = 200) =>
        Index.Query(since, until, status, name, traceId, kind, limit);

    public void Dispose()
    {
        foreach (var sink in _sinks)
        {
            try
            {
                sink.Dispose();
            }
            catch (Exception)
            {
            }
        }
    }

    private static EyeRecorder Build()
    {
        lock (Gate)
        {
            return _current ??= FromSettings(HeliographSettings.Current);
        }
    }
}
