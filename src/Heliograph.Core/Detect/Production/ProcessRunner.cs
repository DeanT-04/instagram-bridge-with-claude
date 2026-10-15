using System.ComponentModel;
using System.Diagnostics;
using System.Text;
using Heliograph.Core.Detect;

namespace Heliograph.Core.Detect.Production;

/// <summary>Runs a program without a shell and without a window. Output is capped; the tree is killed on timeout.</summary>
public sealed class ProcessRunner : IProcessRunner
{
    private const int MaxOutputChars = 64 * 1024;

    public ProcessResult Run(string fileName, IReadOnlyList<string> arguments, TimeSpan timeout)
    {
        var info = new ProcessStartInfo(fileName)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var argument in arguments)
        {
            info.ArgumentList.Add(argument);
        }

        using var process = Process.Start(info) ?? throw new IOException($"could not start {fileName}");
        var stdout = ReadBounded(process.StandardOutput);
        var stderr = ReadBounded(process.StandardError);

        if (!process.WaitForExit(timeout) || !Task.WaitAll([stdout, stderr], TimeSpan.FromSeconds(2)))
        {
            Kill(process);
            throw new TimeoutException($"{fileName} timed out after {timeout.TotalSeconds:0.#}s");
        }

        process.WaitForExit();
        return new ProcessResult(process.ExitCode, stdout.Result, stderr.Result);
    }

    private static Task<string> ReadBounded(StreamReader reader) => Task.Run(() =>
    {
        var text = new StringBuilder();
        var buffer = new char[4096];
        int read;
        while (text.Length < MaxOutputChars && (read = reader.Read(buffer, 0, buffer.Length)) > 0)
        {
            text.Append(buffer, 0, read);
        }
        return text.Length > MaxOutputChars ? text.ToString(0, MaxOutputChars) : text.ToString();
    });

    private static void Kill(Process process)
    {
        try
        {
            process.Kill(entireProcessTree: true);
        }
        catch (Exception error) when (error is InvalidOperationException or Win32Exception or NotSupportedException)
        {
            // Already exited, or the OS refused the kill; the timeout is reported either way.
        }
    }
}
