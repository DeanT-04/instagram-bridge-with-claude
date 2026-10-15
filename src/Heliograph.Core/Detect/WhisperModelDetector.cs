namespace Heliograph.Core.Detect;

/// <summary>Finds a faster-whisper model in the Hugging Face hub cache, or at an absolute directory.</summary>
public sealed class WhisperModelDetector(DetectionSeams seams)
{
    private const string ModelFile = "model.bin";

    public WhisperModelInfo Detect(string model)
    {
        var windows = seams.Platform == DetectionPlatform.Windows;
        if (PlatformPath.IsAbsolute(model, windows))
        {
            var direct = PlatformPath.Join(model, ModelFile, windows);
            return seams.Files.FileExists(direct)
                ? new WhisperModelInfo(true, direct, null)
                : new WhisperModelInfo(false, null, $"no {ModelFile} in {model}");
        }

        var found = FindInCache(model, windows);
        return found is null
            ? new WhisperModelInfo(false, null, $"whisper model '{model}' is not downloaded. Run setup --with-whisper to download it.")
            : new WhisperModelInfo(true, found, null);
    }

    private string? FindInCache(string model, bool windows)
    {
        var root = HubCache(windows);
        if (!PlatformPath.IsAbsolute(root, windows))
        {
            return null;
        }
        var snapshots = PlatformPath.Join(
            PlatformPath.Join(root, $"models--Systran--faster-whisper-{model}", windows), "snapshots", windows);

        foreach (var snapshot in seams.Files.ListDirectories(snapshots))
        {
            var candidate = PlatformPath.Join(snapshot, ModelFile, windows);
            if (seams.Files.FileExists(candidate))
            {
                return candidate;
            }
        }
        return null;
    }

    private string HubCache(bool windows)
    {
        var override_ = seams.Env.Get("HF_HUB_CACHE");
        if (!string.IsNullOrEmpty(override_))
        {
            return override_;
        }
        var home = seams.Env.Get(windows ? "USERPROFILE" : "HOME") ?? "";
        var cache = PlatformPath.Join(PlatformPath.Join(home, ".cache", windows), "huggingface", windows);
        return PlatformPath.Join(cache, "hub", windows);
    }
}
