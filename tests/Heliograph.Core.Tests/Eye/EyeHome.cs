using System.Text.Json.Nodes;
using Heliograph.Core.Eye;

namespace Heliograph.Core.Tests.Eye;

public sealed class EyeHome : IDisposable
{
    private readonly TempDir _tmp = new();

    public EyeHome() => Recorder = EyeRecorder.Configure(new EyeRecorder(EyeDir));

    public EyeRecorder Recorder { get; }

    public string Home => _tmp.Path("home");

    public string EyeDir => System.IO.Path.Combine(Home, "eye");

    public string Path(params string[] parts) => _tmp.Path(parts);

    public List<JsonObject> Recorded(EventKind? kind = null, string? name = null) =>
        [.. EyeRecorder.Current.Query(kind: kind, name: name).AsEnumerable().Reverse()];

    public void Dispose()
    {
        EyeRecorder.Reset();
        _tmp.Dispose();
    }
}
