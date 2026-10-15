namespace Heliograph.Core.Detect;

public enum DetectionPlatform
{
    Windows,
    MacOS,
    Linux,
}

public interface IRegistry
{
    string? AppPathDefault(string exe);
}

public interface IFileSystem
{
    bool FileExists(string path);

    bool DirectoryHasEntries(string path);

    IReadOnlyList<string> ListDirectories(string path);

    string? ReadText(string path);
}

public interface IEnvironmentSource
{
    string? Get(string name);
}

public readonly record struct ProcessResult(int ExitCode, string Stdout, string Stderr);

public interface IProcessRunner
{
    ProcessResult Run(string fileName, IReadOnlyList<string> arguments, TimeSpan timeout);
}

public interface IShellLauncher
{
    void Open(string target);
}

public interface IAppWindowProbe
{
    IReadOnlyList<string> FindWindowTitles();
}

public sealed record DetectionSeams(
    DetectionPlatform Platform,
    IRegistry Registry,
    IFileSystem Files,
    IEnvironmentSource Env,
    IProcessRunner Runner,
    IShellLauncher Shell,
    IAppWindowProbe Window,
    Func<double> Clock);
