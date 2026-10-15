using Heliograph.Core.Detect;

namespace Heliograph.Core.Tests.Detect;

internal sealed class FakeRegistry : IRegistry
{
    public Dictionary<string, string?> Values { get; } = new(StringComparer.OrdinalIgnoreCase);

    public bool Broken { get; set; }

    public string? AppPathDefault(string exe)
    {
        if (Broken)
        {
            throw new IOException("registry hive is damaged");
        }
        return Values.GetValueOrDefault(exe);
    }
}

internal sealed class FakeFiles : IFileSystem
{
    public HashSet<string> Existing { get; } = new(StringComparer.OrdinalIgnoreCase);

    public HashSet<string> NonEmpty { get; } = new(StringComparer.OrdinalIgnoreCase);

    public Dictionary<string, List<string>> Children { get; } = new(StringComparer.OrdinalIgnoreCase);

    public Dictionary<string, string> Texts { get; } = new(StringComparer.OrdinalIgnoreCase);

    public bool FileExists(string path) => Existing.Contains(path);

    public bool DirectoryHasEntries(string path) => NonEmpty.Contains(path);

    public IReadOnlyList<string> ListDirectories(string path) =>
        Children.TryGetValue(path, out var found) ? found : new List<string>();

    public string? ReadText(string path) => Texts.GetValueOrDefault(path);
}

internal sealed class FakeEnv : IEnvironmentSource
{
    private readonly Dictionary<string, string> _values = new(StringComparer.OrdinalIgnoreCase);

    public FakeEnv Set(string name, string value)
    {
        _values[name] = value;
        return this;
    }

    public string? Get(string name) => _values.GetValueOrDefault(name);
}

internal sealed class FakeRunner : IProcessRunner
{
    public List<(string File, IReadOnlyList<string> Arguments)> Calls { get; } = [];

    public Func<string, IReadOnlyList<string>, ProcessResult> Handler { get; set; } =
        (file, _) => throw new InvalidOperationException($"unexpected process {file}");

    public ProcessResult Run(string fileName, IReadOnlyList<string> arguments, TimeSpan timeout)
    {
        Calls.Add((fileName, arguments));
        return Handler(fileName, arguments);
    }
}

internal sealed class FakeShell : IShellLauncher
{
    public List<string> Opened { get; } = [];

    public Exception? Failure { get; set; }

    public void Open(string target)
    {
        if (Failure is not null)
        {
            throw Failure;
        }
        Opened.Add(target);
    }
}

internal sealed class FakeWindows : IAppWindowProbe
{
    public Func<IReadOnlyList<string>> Titles { get; set; } = () => [];

    public int Calls { get; private set; }

    public string? LastAumid { get; private set; }

    public IReadOnlyList<string> FindWindowTitles(string aumid)
    {
        Calls++;
        LastAumid = aumid;
        return Titles();
    }
}

internal sealed class Rig
{
    public DetectionPlatform Platform { get; set; } = DetectionPlatform.Windows;

    public FakeRegistry Registry { get; } = new();

    public FakeFiles Files { get; } = new();

    public FakeEnv Env { get; } = new();

    public FakeRunner Runner { get; } = new();

    public FakeShell Shell { get; } = new();

    public FakeWindows Window { get; } = new();

    public double Now { get; set; }

    public DetectionSeams Seams => new(
        Platform, Registry, Files, Env, Runner, Shell, Window, () => Now);
}
