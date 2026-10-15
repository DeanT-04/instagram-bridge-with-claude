using Heliograph.Core.Detect;
using System.Diagnostics;

namespace Heliograph.Core.Detect.Production;

/// <summary>Runs a program without a shell and without a window. Kills the tree on timeout.</summary>
public sealed class ProcessRunner : IProcessRunner
{
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
        var stdout = process.StandardOutput.ReadToEndAsync();
        var stderr = process.StandardError.ReadToEndAsync();

        if (!process.WaitForExit(timeout))
        {
            try
            {
                process.Kill(entireProcessTree: true);
            }
            catch (InvalidOperationException)
            {
                // Already exited between the timeout and the kill.
            }
            throw new TimeoutException($"{fileName} timed out after {timeout.TotalSeconds:0.#}s");
        }

        process.WaitForExit();
        return new ProcessResult(process.ExitCode, stdout.Result, stderr.Result);
    }
}
