using Heliograph.Core.Detect;
using Heliograph.Core.Tests.Eye;

namespace Heliograph.Core.Tests.Detect;

[Collection(ProcessStateCollection.Name)]
public sealed class WhisperModelDetectorTests : IDisposable
{
    private const string SmallModel =
        @"C:\Users\me\.cache\huggingface\hub\models--Systran--faster-whisper-small\snapshots\abc\model.bin";

    private readonly EyeHome _eye = new();
    private readonly Rig _rig = new() { Platform = DetectionPlatform.Windows };

    public void Dispose() => _eye.Dispose();

    [Fact]
    public void Cached_model_under_the_hub_cache_is_found()
    {
        _rig.Env.Set("USERPROFILE", @"C:\Users\me");
        _rig.Files.Children[@"C:\Users\me\.cache\huggingface\hub\models--Systran--faster-whisper-small\snapshots"] =
            [@"C:\Users\me\.cache\huggingface\hub\models--Systran--faster-whisper-small\snapshots\abc"];
        _rig.Files.Existing.Add(SmallModel);

        var info = new WhisperModelDetector(_rig.Seams).Detect("small");

        Assert.True(info.Found);
        Assert.Equal(SmallModel, info.Path);
    }

    [Fact]
    public void Hf_hub_cache_environment_variable_overrides_the_default_root()
    {
        _rig.Env.Set("USERPROFILE", @"C:\Users\me").Set("HF_HUB_CACHE", @"D:\hf");
        _rig.Files.Children[@"D:\hf\models--Systran--faster-whisper-small\snapshots"] =
            [@"D:\hf\models--Systran--faster-whisper-small\snapshots\abc"];
        _rig.Files.Existing.Add(@"D:\hf\models--Systran--faster-whisper-small\snapshots\abc\model.bin");

        Assert.True(new WhisperModelDetector(_rig.Seams).Detect("small").Found);
    }

    [Fact]
    public void Missing_model_hint_names_the_download_step()
    {
        _rig.Env.Set("USERPROFILE", @"C:\Users\me");

        var info = new WhisperModelDetector(_rig.Seams).Detect("small");

        Assert.False(info.Found);
        Assert.Contains("small", info.Reason);
        Assert.Contains("setup --with-whisper", info.Reason);
    }

    [Fact]
    public void Absolute_model_directory_is_checked_directly()
    {
        _rig.Files.Existing.Add(@"C:\models\mine\model.bin");

        var info = new WhisperModelDetector(_rig.Seams).Detect(@"C:\models\mine");

        Assert.True(info.Found);
        Assert.Equal(@"C:\models\mine\model.bin", info.Path);
    }
}
