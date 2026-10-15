using System.Text;
using System.Text.Json.Nodes;

namespace Heliograph.Core.Eye;

/// <summary>Anything that persists eye events. <c>line</c> is the redacted JSON of <c>data</c>.</summary>
public interface IEventSink : IDisposable
{
    void Write(EyeEvent ev, JsonObject data, string line);
}

/// <summary>Append-only JSON lines with size-based rotation (<c>events.jsonl.1</c> ...).</summary>
public sealed class JsonlSink(string path, long maxBytes = 10 * 1024 * 1024, int backups = 5) : IEventSink
{
    private readonly Lock _gate = new();

    public string Path { get; } = path;

    public void Write(EyeEvent ev, JsonObject data, string line) => Append(line);

    /// <summary>Appends <paramref name="line"/>, rotating first if the file would grow past the size cap.</summary>
    public void Append(string line)
    {
        var bytes = Encoding.UTF8.GetBytes(line + "\n");
        lock (_gate)
        {
            var stream = Open();
            if (stream.Length > 0 && stream.Length + bytes.Length > maxBytes)
            {
                stream.Dispose();
                Rotate();
                stream = Open();
            }
            using (stream)
            {
                stream.Write(bytes);
            }
        }
    }

    public void Dispose()
    {
    }

    // Opened per write, as in Python, so every process appends to the current file after a rotation.
    private FileStream Open()
    {
        var options = PrivateFile.Options(FileMode.Append, FileAccess.Write);
        try
        {
            return new FileStream(Path, options);
        }
        catch (DirectoryNotFoundException)
        {
            System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(Path))!);
            return new FileStream(Path, options);
        }
    }

    private void Rotate()
    {
        if (backups <= 0)
        {
            File.Delete(Path);
            return;
        }
        for (var i = backups - 1; i > 0; i--)
        {
            if (File.Exists($"{Path}.{i}"))
            {
                File.Move($"{Path}.{i}", $"{Path}.{i + 1}", overwrite: true);
            }
        }
        File.Move(Path, $"{Path}.1", overwrite: true);
    }
}

internal static class PrivateFile
{
    public static FileStreamOptions Options(FileMode mode, FileAccess access)
    {
        var options = new FileStreamOptions { Mode = mode, Access = access, Share = FileShare.ReadWrite | FileShare.Delete };
        if (!OperatingSystem.IsWindows())
        {
            options.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
        }
        return options;
    }
}
