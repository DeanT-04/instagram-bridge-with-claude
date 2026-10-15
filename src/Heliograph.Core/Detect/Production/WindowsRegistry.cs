using Heliograph.Core.Detect;
using Microsoft.Win32;

namespace Heliograph.Core.Detect.Production;

/// <summary>Reads App Paths defaults, current user first, then local machine. Any failure reads as absent.</summary>
public sealed class WindowsRegistry : IRegistry
{
    private const string AppPaths = @"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\";

    public string? AppPathDefault(string exe)
    {
        if (!OperatingSystem.IsWindows() || exe.Length == 0 || exe.IndexOfAny(['\\', '/']) >= 0)
        {
            return null;
        }

        foreach (var hive in new[] { Registry.CurrentUser, Registry.LocalMachine })
        {
            try
            {
                using var key = hive.OpenSubKey(AppPaths + exe);
                if (key?.GetValue(string.Empty) is string value && !string.IsNullOrWhiteSpace(value))
                {
                    return value;
                }
            }
            catch (Exception)
            {
                // A damaged or denied hive is treated as absent; the caller falls back to common locations.
            }
        }
        return null;
    }
}
