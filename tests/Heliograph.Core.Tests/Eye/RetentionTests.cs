using Heliograph.Core.Config;
using Heliograph.Core.Eye;

namespace Heliograph.Core.Tests.Eye;

public sealed class RetentionTests : IDisposable
{
    private readonly TempDir _tmp = new();

    public void Dispose() => _tmp.Dispose();

    [Fact]
    public void Prune_artifacts()
    {
        var folder = _tmp.Path("artifacts");
        var old = Aged(Path.Combine(folder, "uia-click-1.png"), 10);
        var oldNested = Aged(Path.Combine(folder, "screenshots", "app-1.png"), 8);
        var fresh = Aged(Path.Combine(folder, "uia-click-2.txt"), 1);

        Assert.Equal(2, ArtifactRetention.Prune(folder, TimeSpan.FromDays(7)));

        Assert.False(File.Exists(old));
        Assert.False(File.Exists(oldNested));
        Assert.True(File.Exists(fresh));
        Assert.False(Directory.Exists(Path.Combine(folder, "screenshots")));
        Assert.Equal(0, ArtifactRetention.Prune(folder, TimeSpan.Zero));
        Assert.True(File.Exists(fresh));
        Assert.Equal(0, ArtifactRetention.Prune(_tmp.Path("missing"), TimeSpan.FromDays(7)));
    }

    [Fact]
    public void Recorder_prunes_at_startup_with_setting()
    {
        var settings = HeliographSettings.Load(
            new Dictionary<string, string>
            {
                ["HELIOGRAPH_HOME"] = _tmp.Path("home"),
                ["HELIOGRAPH_ARTIFACT_RETENTION_DAYS"] = "3",
            },
            envFile: null);
        var artifacts = Path.Combine(settings.EyePath, "artifacts");
        var (old, fresh) = (Aged(Path.Combine(artifacts, "a.png"), 4), Aged(Path.Combine(artifacts, "b.png"), 2));

        using var recorder = EyeRecorder.FromSettings(settings);

        Assert.Equal(1, recorder.PrunedArtifacts);
        Assert.False(File.Exists(old));
        Assert.True(File.Exists(fresh));
    }

    [Fact]
    public void Default_retention_is_seven_days() =>
        Assert.Equal(TimeSpan.FromDays(7), HeliographSettings.Load(new Dictionary<string, string>(), envFile: null).ArtifactRetention);

    [Fact]
    public void Artifacts_dir_is_created_owner_only()
    {
        using var recorder = new EyeRecorder(_tmp.Path("eye"), artifactRetention: TimeSpan.FromDays(7));

        Assert.True(Directory.Exists(recorder.ArtifactsDir));
        if (!OperatingSystem.IsWindows())
        {
            Assert.Equal(
                UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute,
                File.GetUnixFileMode(recorder.ArtifactsDir));
        }
    }

    private static string Aged(string path, double days)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, "x");
        File.SetLastWriteTimeUtc(path, DateTime.UtcNow.AddDays(-days));
        return path;
    }
}
