using Heliograph.Core.Detect;

namespace Heliograph.Core.Tests.Detect;

public sealed class ToolDetectorTests
{
    private const string FfmpegHint = "scoop install ffmpeg";

    private readonly Rig _rig = new() { Platform = DetectionPlatform.Windows };


    private ToolInfo Detect(string name, string? hint = null) => new ToolDetector(_rig.Seams).Detect(name, hint);

    [Fact]
    public void Missing_tool_reports_not_found_with_install_hint()
    {
        var info = Detect("ffmpeg", FfmpegHint);

        Assert.False(info.Found);
        Assert.Contains("not found on PATH", info.Reason);
        Assert.Contains(FfmpegHint, info.Reason);
    }

    [Fact]
    public void Found_tool_reports_path_and_first_version_line()
    {
        _rig.Env.Set("PATH", @"C:\ff\bin");
        _rig.Files.Existing.Add(@"C:\ff\bin\ffmpeg.exe");
        _rig.Runner.Handler = (_, _) => new ProcessResult(
            0, "ffmpeg version 9.0 Copyright (c) 2000 the FFmpeg developers\nmore", "");

        var info = Detect("ffmpeg");

        Assert.True(info.Found);
        Assert.Equal(@"C:\ff\bin\ffmpeg.exe", info.Path);
        Assert.Equal("ffmpeg version 9.0", info.Version);
        Assert.Equal(@"C:\ff\bin\ffmpeg.exe", _rig.Runner.Calls.Single().File);
        Assert.Equal(new[] { "-version" }, _rig.Runner.Calls.Single().Arguments);
    }

    [Fact]
    public void Version_falls_back_to_stderr_when_stdout_is_empty()
    {
        _rig.Env.Set("PATH", @"C:\ff\bin");
        _rig.Files.Existing.Add(@"C:\ff\bin\ffprobe.exe");
        _rig.Runner.Handler = (_, _) => new ProcessResult(0, "", "ffprobe version 7.1\nbuilt");

        Assert.Equal("ffprobe version 7.1", Detect("ffprobe").Version);
    }

    [Fact]
    public void Failing_version_check_keeps_found_and_records_reason()
    {
        _rig.Env.Set("PATH", @"C:\ff\bin");
        _rig.Files.Existing.Add(@"C:\ff\bin\ffmpeg.exe");
        _rig.Runner.Handler = (_, _) => throw new IOException("exec format error");

        var info = Detect("ffmpeg");

        Assert.True(info.Found);
        Assert.Null(info.Version);
        Assert.Contains("exec format", info.Reason);
    }

    [Fact]
    public void Timed_out_version_check_keeps_found()
    {
        _rig.Env.Set("PATH", @"C:\ff\bin");
        _rig.Files.Existing.Add(@"C:\ff\bin\ffmpeg.exe");
        _rig.Runner.Handler = (_, _) => throw new TimeoutException("too slow");

        var info = Detect("ffmpeg");

        Assert.True(info.Found);
        Assert.Contains("version check failed", info.Reason);
    }

    [Fact]
    public void Windows_search_skips_current_directory_and_relative_entries()
    {
        _rig.Env.Set("PATH", @".;relative;C:\bin");
        _rig.Files.Existing.Add(@".\ffmpeg.exe");
        _rig.Files.Existing.Add(@"relative\ffmpeg.exe");
        _rig.Files.Existing.Add(@"C:\bin\ffmpeg.exe");
        _rig.Runner.Handler = (_, _) => new ProcessResult(0, "ffmpeg version 9.0", "");

        var info = Detect("ffmpeg");

        Assert.Equal(@"C:\bin\ffmpeg.exe", info.Path);
    }

    [Fact]
    public void Windows_search_appends_pathext_extensions()
    {
        _rig.Env.Set("PATH", @"C:\bin").Set("PATHEXT", ".COM;.EXE");
        _rig.Files.Existing.Add(@"C:\bin\ffmpeg");
        _rig.Files.Existing.Add(@"C:\bin\ffmpeg.exe");
        _rig.Runner.Handler = (_, _) => new ProcessResult(0, "ffmpeg version 9.0", "");

        Assert.Equal(@"C:\bin\ffmpeg.exe", Detect("ffmpeg").Path);
    }

    [Fact]
    public void Posix_search_uses_path_entries()
    {
        _rig.Platform = DetectionPlatform.Linux;
        _rig.Env.Set("PATH", "/usr/bin");
        _rig.Files.Existing.Add("/usr/bin/ffprobe");
        _rig.Runner.Handler = (_, _) => new ProcessResult(0, "ffprobe version 7.1", "");

        var info = Detect("ffprobe");

        Assert.Equal("/usr/bin/ffprobe", info.Path);
        Assert.Equal("/usr/bin/ffprobe", _rig.Runner.Calls.Single().File);
    }
}
