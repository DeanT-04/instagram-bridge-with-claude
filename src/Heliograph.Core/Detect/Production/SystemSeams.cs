using Heliograph.Core.Detect;
namespace Heliograph.Core.Detect.Production;

/// <summary>Real-machine seams for the detectors. Every read degrades to empty or null instead of throwing.</summary>
public static class SystemSeams
{
    public static DetectionSeams ForCurrentProcess() => new(
        Platform: CurrentPlatform(),
        Registry: new WindowsRegistry(),
        Files: new DiskFiles(),
        Env: new ProcessEnvironment(),
        Runner: new ProcessRunner(),
        Shell: new ShellLauncher(),
        Window: OperatingSystem.IsWindows() ? new AppWindowProbe() : new NoWindowProbe(),
        Clock: () => Environment.TickCount64 / 1000.0);

    private static DetectionPlatform CurrentPlatform() =>
        OperatingSystem.IsWindows() ? DetectionPlatform.Windows
        : OperatingSystem.IsMacOS() ? DetectionPlatform.MacOS
        : DetectionPlatform.Linux;

    private sealed class NoWindowProbe : IAppWindowProbe
    {
        public IReadOnlyList<string> FindWindowTitles(string aumid) => [];
    }

    private sealed class ProcessEnvironment : IEnvironmentSource
    {
        public string? Get(string name) => Environment.GetEnvironmentVariable(name);
    }

    private sealed class DiskFiles : IFileSystem
    {
        public bool FileExists(string path)
        {
            try
            {
                return File.Exists(path);
            }
            catch (Exception)
            {
                return false;
            }
        }

        public bool DirectoryHasEntries(string path)
        {
            try
            {
                return Directory.Exists(path) && Directory.EnumerateFileSystemEntries(path).Any();
            }
            catch (Exception)
            {
                return false;
            }
        }

        public IReadOnlyList<string> ListDirectories(string path)
        {
            try
            {
                return Directory.Exists(path) ? Directory.GetDirectories(path) : [];
            }
            catch (Exception)
            {
                return [];
            }
        }

        public string? ReadText(string path)
        {
            try
            {
                return File.Exists(path) ? File.ReadAllText(path) : null;
            }
            catch (Exception)
            {
                return null;
            }
        }
    }
}
